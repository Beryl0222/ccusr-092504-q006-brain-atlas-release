"""研究资产登记：供体授权、样本、批次、测序文件、质控、注释、队列。

所有写入都以领域事件落库；本模块只提供命令和快照读取，不保存可变状态。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .catalog import AggregateType, EventType, Purpose
from .errors import DomainError, ImmutabilityError, LineageError
from .store import Event, EventStore

# 有资质裁定标注冲突的角色。
QUALIFIED_ANNOTATION_ROLES: frozenset[str] = frozenset(
    {"senior_curator", "principal_investigator", "board_certified_neuropathologist"}
)


# --------------------------------------------------------------------------
# 快照
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DonorSnapshot:
    donor_id: str
    allowed_purposes: frozenset[str]
    withdrawn: bool
    withdrawal_reason: str | None


@dataclass(frozen=True)
class SampleSnapshot:
    sample_id: str
    donor_id: str
    deidentification_ref: str  # 去标识化映射引用，本身不保存可逆标识
    current: bool


@dataclass(frozen=True)
class BatchSnapshot:
    batch_id: str
    sample_ids: frozenset[str]
    protocol_version: str
    status: str  # prepared | accepted | rejected
    review_reason: str | None


@dataclass(frozen=True)
class SequencingFileSnapshot:
    file_id: str
    batch_id: str
    sample_id: str
    checksum_alg: str
    checksum_value: str
    verified: bool


@dataclass(frozen=True)
class QcSnapshot:
    qc_id: str
    sample_id: str
    batch_id: str
    threshold_version: str
    passed: bool
    metrics: Mapping[str, Any]
    stale: bool  # 阈值变更后未复算即为过时


@dataclass(frozen=True)
class AnnotationSnapshot:
    annotation_id: str
    target_ref: str
    approved_version: str | None
    approved_label: str | None
    open_conflict: bool
    versions: tuple[str, ...]


@dataclass(frozen=True)
class CohortSnapshot:
    cohort_id: str
    study_type: str            # life_stage | disease | gene_trait
    restriction: Mapping[str, str]
    members: frozenset[str]
    exclusions: Mapping[str, str]
    access_conditions: Mapping[str, frozenset[str]]
    sealed: bool


def _events_by_type(store: EventStore, aggregate_id: str) -> list[Event]:
    return store.stream(aggregate_id)


# --------------------------------------------------------------------------
# 供体授权
# --------------------------------------------------------------------------


class DonorRegistry:
    def __init__(self, store: EventStore) -> None:
        self._store = store

    def register(
        self,
        donor_id: str,
        *,
        allowed_purposes: Iterable[Purpose | str],
        summary: str = "登记供体授权范围",
    ) -> Event:
        purposes = sorted({Purpose(p).value for p in allowed_purposes})
        if not purposes:
            raise DomainError("授权用途不能为空", code="consent_scope_empty")
        if self.get(donor_id) is not None:
            raise ImmutabilityError(f"供体 {donor_id} 已登记，授权变更必须走范围更新")
        return self._store.append(
            event_type=EventType.CONSENT_REGISTERED,
            aggregate_type=AggregateType.DONOR,
            aggregate_id=donor_id,
            summary=summary,
            payload={"allowed_purposes": purposes},
        )

    def update_scope(
        self,
        donor_id: str,
        *,
        allowed_purposes: Iterable[Purpose | str],
        reason: str,
    ) -> Event:
        donor = self.get(donor_id)
        if donor is None:
            raise DomainError(f"供体 {donor_id} 尚未登记", code="donor_not_found")
        if donor.withdrawn:
            raise LineageError("供体已撤回授权，不能扩大或重置授权范围")
        purposes = sorted({Purpose(p).value for p in allowed_purposes})
        if not purposes:
            raise DomainError("授权用途不能为空", code="consent_scope_empty")
        return self._store.append(
            event_type=EventType.CONSENT_SCOPE_UPDATED,
            aggregate_type=AggregateType.DONOR,
            aggregate_id=donor_id,
            summary=f"更新授权范围：{reason}",
            payload={"allowed_purposes": purposes, "reason": reason},
            expected_version=self._store.version(donor_id),
        )

    def withdraw(self, donor_id: str, *, reason: str) -> Event:
        donor = self.get(donor_id)
        if donor is None:
            raise DomainError(f"供体 {donor_id} 尚未登记", code="donor_not_found")
        if donor.withdrawn:
            raise LineageError("供体已处于撤回状态，撤回事实不可重复登记")
        return self._store.append(
            event_type=EventType.CONSENT_WITHDRAWN,
            aggregate_type=AggregateType.DONOR,
            aggregate_id=donor_id,
            summary=f"撤回授权：{reason}",
            payload={"reason": reason},
            expected_version=self._store.version(donor_id),
        )

    def get(self, donor_id: str) -> DonorSnapshot | None:
        events = _events_by_type(self._store, donor_id)
        if not events:
            return None
        allowed: frozenset[str] = frozenset()
        withdrawn = False
        withdrawal_reason = None
        for event in events:
            if event.event_type == EventType.CONSENT_REGISTERED:
                allowed = frozenset(event.payload.get("allowed_purposes", ()))
            elif event.event_type == EventType.CONSENT_SCOPE_UPDATED:
                allowed = frozenset(event.payload.get("allowed_purposes", ()))
            elif event.event_type == EventType.CONSENT_WITHDRAWN:
                withdrawn = True
                withdrawal_reason = event.payload.get("reason")
        return DonorSnapshot(donor_id, allowed, withdrawn, withdrawal_reason)


# --------------------------------------------------------------------------
# 去标识化样本
# --------------------------------------------------------------------------


class SampleRegistry:
    def __init__(self, store: EventStore, donors: DonorRegistry) -> None:
        self._store = store
        self._donors = donors

    def derive(
        self,
        sample_id: str,
        *,
        donor_id: str,
        deidentification_ref: str,
    ) -> Event:
        donor = self._donors.get(donor_id)
        if donor is None:
            raise DomainError("样本必须挂接到已登记供体", code="donor_not_found")
        if donor.withdrawn:
            raise LineageError("供体已撤回授权，不得再派生新样本")
        if not deidentification_ref.strip():
            raise DomainError("必须提供去标识化映射引用", code="deid_ref_required")
        return self._store.append(
            event_type=EventType.SAMPLE_DERIVED,
            aggregate_type=AggregateType.SAMPLE,
            aggregate_id=sample_id,
            summary=f"从供体派生去标识化样本 {sample_id}",
            payload={
                "donor_id": donor_id,
                "deidentification_ref": deidentification_ref,
            },
        )

    def get(self, sample_id: str) -> SampleSnapshot | None:
        events = _events_by_type(self._store, sample_id)
        if not events:
            return None
        latest = events[-1]
        return SampleSnapshot(
            sample_id=sample_id,
            donor_id=latest.payload["donor_id"],
            deidentification_ref=latest.payload["deidentification_ref"],
            current=latest.event_type == EventType.SAMPLE_DERIVED,
        )


# --------------------------------------------------------------------------
# 制备批次与质量复核
# --------------------------------------------------------------------------


class BatchRegistry:
    def __init__(self, store: EventStore) -> None:
        self._store = store

    def prepare(
        self,
        batch_id: str,
        *,
        sample_ids: Iterable[str],
        protocol_version: str,
    ) -> Event:
        sample_ids = sorted(frozenset(sample_ids))
        if not sample_ids:
            raise DomainError("制备批次至少包含一个样本", code="batch_empty")
        if not protocol_version.strip():
            raise DomainError("必须记录制备协议版本", code="protocol_version_required")
        return self._store.append(
            event_type=EventType.BATCH_PREPARED,
            aggregate_type=AggregateType.BATCH,
            aggregate_id=batch_id,
            summary=f"登记制备批次 {batch_id}",
            payload={"sample_ids": sample_ids, "protocol_version": protocol_version},
        )

    def review_quality(
        self,
        batch_id: str,
        *,
        accepted: bool,
        reason: str,
        reviewer_id: str,
    ) -> Event:
        snapshot = self.get(batch_id)
        if snapshot is None:
            raise DomainError(f"批次 {batch_id} 尚未制备", code="batch_not_found")
        return self._store.append(
            event_type=EventType.BATCH_QUALITY_REVIEWED,
            aggregate_type=AggregateType.BATCH,
            aggregate_id=batch_id,
            summary=f"批次质量复核{'通过' if accepted else '不通过'}：{reason}",
            payload={
                "accepted": accepted,
                "reason": reason,
                "reviewer_id": reviewer_id,
            },
            expected_version=self._store.version(batch_id),
        )

    def get(self, batch_id: str) -> BatchSnapshot | None:
        events = _events_by_type(self._store, batch_id)
        if not events:
            return None
        sample_ids: frozenset[str] = frozenset()
        protocol_version = ""
        status = "prepared"
        review_reason = None
        for event in events:
            if event.event_type == EventType.BATCH_PREPARED:
                sample_ids = frozenset(event.payload["sample_ids"])
                protocol_version = event.payload["protocol_version"]
            elif event.event_type == EventType.BATCH_QUALITY_REVIEWED:
                status = "accepted" if event.payload["accepted"] else "rejected"
                review_reason = event.payload["reason"]
        return BatchSnapshot(batch_id, sample_ids, protocol_version, status, review_reason)


# --------------------------------------------------------------------------
# 测序文件与校验
# --------------------------------------------------------------------------


class SequencingFileRegistry:
    def __init__(self, store: EventStore) -> None:
        self._store = store

    def register(
        self,
        file_id: str,
        *,
        batch_id: str,
        sample_id: str,
        checksum_alg: str = "sha256",
        checksum_value: str,
    ) -> Event:
        if checksum_alg != "sha256" or len(checksum_value.strip()) != 64:
            raise DomainError("测序文件必须登记 sha256 校验值", code="checksum_invalid")
        return self._store.append(
            event_type=EventType.SEQUENCING_FILE_REGISTERED,
            aggregate_type=AggregateType.SEQUENCING_FILE,
            aggregate_id=file_id,
            summary=f"登记测序文件 {file_id}",
            payload={
                "batch_id": batch_id,
                "sample_id": sample_id,
                "checksum_alg": checksum_alg,
                "checksum_value": checksum_value.lower(),
            },
        )

    def verify(self, file_id: str, *, observed_checksum: str) -> list[Event]:
        """登记一次校验结果；失败也保留为事实，供复核算账。"""
        snapshot = self.get(file_id)
        if snapshot is None:
            raise DomainError(f"测序文件 {file_id} 尚未登记", code="file_not_found")
        ok = observed_checksum.strip().lower() == snapshot.checksum_value
        event_type = (
            EventType.SEQUENCING_CHECKSUM_VERIFIED
            if ok else EventType.SEQUENCING_CHECKSUM_FAILED
        )
        event = self._store.append(
            event_type=event_type,
            aggregate_type=AggregateType.SEQUENCING_FILE,
            aggregate_id=file_id,
            summary="测序文件校验" + ("通过" if ok else "失败"),
            payload={"observed_checksum": observed_checksum.strip().lower()},
            expected_version=self._store.version(file_id),
        )
        return [event]

    def get(self, file_id: str) -> SequencingFileSnapshot | None:
        events = _events_by_type(self._store, file_id)
        if not events:
            return None
        registered = events[0]
        verified = any(e.event_type == EventType.SEQUENCING_CHECKSUM_VERIFIED for e in events)
        return SequencingFileSnapshot(
            file_id=file_id,
            batch_id=registered.payload["batch_id"],
            sample_id=registered.payload["sample_id"],
            checksum_alg=registered.payload["checksum_alg"],
            checksum_value=registered.payload["checksum_value"],
            verified=verified,
        )


# --------------------------------------------------------------------------
# 细胞质控
# --------------------------------------------------------------------------


class CellQcRegistry:
    def __init__(self, store: EventStore) -> None:
        self._store = store

    def record(
        self,
        qc_id: str,
        *,
        sample_id: str,
        batch_id: str,
        threshold_version: str,
        passed: bool,
        metrics: Mapping[str, Any],
    ) -> Event:
        if not threshold_version.strip():
            raise DomainError("质控结果必须标明阈值版本", code="threshold_version_required")
        if not metrics:
            raise DomainError("质控结果必须包含度量明细", code="metrics_required")
        return self._store.append(
            event_type=EventType.CELL_QC_RECORDED,
            aggregate_type=AggregateType.CELL_QC,
            aggregate_id=qc_id,
            summary=f"登记细胞质控结果 {qc_id}（阈值 {threshold_version}）",
            payload={
                "sample_id": sample_id,
                "batch_id": batch_id,
                "threshold_version": threshold_version,
                "passed": passed,
                "metrics": dict(metrics),
            },
        )

    def change_threshold(self, qc_id: str, *, new_threshold_version: str, reason: str) -> Event:
        if self.get(qc_id) is None:
            raise DomainError(f"质控对象 {qc_id} 尚未登记", code="qc_not_found")
        return self._store.append(
            event_type=EventType.QC_THRESHOLD_CHANGED,
            aggregate_type=AggregateType.CELL_QC,
            aggregate_id=qc_id,
            summary=f"质控阈值变更为 {new_threshold_version}：{reason}",
            payload={"new_threshold_version": new_threshold_version, "reason": reason},
            expected_version=self._store.version(qc_id),
        )

    def get(self, qc_id: str) -> QcSnapshot | None:
        events = _events_by_type(self._store, qc_id)
        if not events:
            return None
        threshold_version = events[0].payload["threshold_version"]
        metrics = events[0].payload["metrics"]
        passed = events[0].payload["passed"]
        sample_id = events[0].payload["sample_id"]
        batch_id = events[0].payload["batch_id"]
        stale = False
        for event in events[1:]:
            if event.event_type == EventType.QC_THRESHOLD_CHANGED:
                threshold_version = event.payload["new_threshold_version"]
                stale = True
            elif event.event_type == EventType.CELL_QC_RECORDED:
                threshold_version = event.payload["threshold_version"]
                metrics = event.payload["metrics"]
                passed = event.payload["passed"]
                stale = False
        return QcSnapshot(qc_id, sample_id, batch_id, threshold_version, passed, metrics, stale)


# --------------------------------------------------------------------------
# 注释与并行裁定（裁定流程的状态机部分见 annotations.py）
# --------------------------------------------------------------------------


class AnnotationRegistry:
    def __init__(self, store: EventStore) -> None:
        self._store = store

    def submit(
        self,
        annotation_id: str,
        *,
        target_ref: str,
        annotation_version: str,
        annotator_id: str,
        label: str,
    ) -> Event:
        known = self.get(annotation_id)
        if known is not None and known.target_ref != target_ref:
            raise DomainError("注释聚合只能针对同一标注目标", code="target_mismatch")
        return self._store.append(
            event_type=EventType.ANNOTATION_SUBMITTED,
            aggregate_type=AggregateType.ANNOTATION,
            aggregate_id=annotation_id,
            summary=f"提交标注 {annotation_version}：{label}",
            payload={
                "target_ref": target_ref,
                "annotation_version": annotation_version,
                "annotator_id": annotator_id,
                "label": label,
            },
        )

    def get(self, annotation_id: str) -> AnnotationSnapshot | None:
        events = _events_by_type(self._store, annotation_id)
        if not events:
            return None
        target_ref = events[0].payload["target_ref"]
        versions: list[str] = []
        approved_version = None
        approved_label = None
        open_conflict = False
        for event in events:
            if event.event_type == EventType.ANNOTATION_SUBMITTED:
                versions.append(event.payload["annotation_version"])
            elif event.event_type == EventType.ANNOTATION_CONFLICT_RAISED:
                open_conflict = not event.payload.get("resolved", False)
            elif event.event_type == EventType.ANNOTATION_APPROVED:
                approved_version = event.payload["annotation_version"]
                approved_label = event.payload["label"]
                open_conflict = False
        return AnnotationSnapshot(
            annotation_id,
            target_ref,
            approved_version,
            approved_label,
            open_conflict,
            tuple(versions),
        )
