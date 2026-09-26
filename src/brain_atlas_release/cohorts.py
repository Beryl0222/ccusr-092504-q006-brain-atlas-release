"""队列纳排与访问条件。

同一供体可以进入多个分析集（生命阶段、疾病、基因性状），成员资格按
“样本 + 当时证据”逐条判定：授权用途覆盖、未撤回、制备批次通过、测序
校验通过、细胞质控通过且未随阈值变更而过时。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .catalog import AggregateType, EventType, Purpose
from .errors import DomainError
from .records import (
    BatchRegistry,
    CellQcRegistry,
    DonorRegistry,
    SampleRegistry,
    SequencingFileRegistry,
)
from .store import Event, EventStore

STUDY_TYPES = ("life_stage", "disease", "gene_trait")


@dataclass(frozen=True)
class EligibilityReport:
    eligible: bool
    reasons: tuple[str, ...]


class CohortRegistry:
    def __init__(
        self,
        store: EventStore,
        *,
        donors: DonorRegistry,
        samples: SampleRegistry,
        batches: BatchRegistry,
        files: SequencingFileRegistry,
        qcs: CellQcRegistry,
    ) -> None:
        self._store = store
        self._donors = donors
        self._samples = samples
        self._batches = batches
        self._files = files
        self._qcs = qcs

    # ---- 命令 ----------------------------------------------------------

    def define(
        self,
        cohort_id: str,
        *,
        study_type: str,
        restriction: Mapping[str, str] | None = None,
        access_conditions: Mapping[Purpose | str, Iterable[str]] | None = None,
    ) -> Event:
        if study_type not in STUDY_TYPES:
            raise DomainError(f"未知研究类型 {study_type}", code="unknown_study_type")
        if self.get(cohort_id) is not None:
            raise DomainError(f"队列 {cohort_id} 已定义", code="cohort_exists")
        conditions = {
            Purpose(purpose).value: sorted(frozenset(roles))
            for purpose, roles in (access_conditions or {}).items()
        }
        return self._store.append(
            event_type=EventType.COHORT_DEFINED,
            aggregate_type=AggregateType.COHORT,
            aggregate_id=cohort_id,
            summary=f"定义{study_type}队列 {cohort_id}",
            payload={
                "study_type": study_type,
                "restriction": dict(restriction or {}),
                "access_conditions": conditions,
            },
        )

    def include(self, cohort_id: str, *, sample_id: str, qc_id: str, file_id: str) -> Event:
        cohort = self.get(cohort_id)
        if cohort is None:
            raise DomainError(f"队列 {cohort_id} 尚未定义", code="cohort_not_found")
        if sample_id in cohort.members:
            # 幂等：重复纳排判定不产生重复事实。
            return self._last_event(cohort_id, EventType.SAMPLE_INCLUDED, sample_id)
        report = self.evaluate(sample_id, qc_id=qc_id, file_id=file_id)
        if not report.eligible:
            raise DomainError(
                f"样本 {sample_id} 不满足纳入条件：{'; '.join(report.reasons)}",
                code="inclusion_ineligible",
            )
        return self._store.append(
            event_type=EventType.SAMPLE_INCLUDED,
            aggregate_type=AggregateType.COHORT,
            aggregate_id=cohort_id,
            summary=f"纳入样本 {sample_id}",
            payload={"sample_id": sample_id, "qc_id": qc_id, "file_id": file_id},
            expected_version=self._store.version(cohort_id),
        )

    def exclude(self, cohort_id: str, *, sample_id: str, reason: str) -> Event:
        cohort = self.get(cohort_id)
        if cohort is None:
            raise DomainError(f"队列 {cohort_id} 尚未定义", code="cohort_not_found")
        return self._store.append(
            event_type=EventType.SAMPLE_EXCLUDED,
            aggregate_type=AggregateType.COHORT,
            aggregate_id=cohort_id,
            summary=f"排除样本 {sample_id}：{reason}",
            payload={"sample_id": sample_id, "reason": reason},
            expected_version=self._store.version(cohort_id),
        )

    # ---- 资格判定 ------------------------------------------------------

    def evaluate(
        self,
        sample_id: str,
        *,
        qc_id: str,
        file_id: str,
        required_purposes: Iterable[str] = (),
    ) -> EligibilityReport:
        reasons: list[str] = []
        sample = self._samples.get(sample_id)
        if sample is None:
            return EligibilityReport(False, ("样本不存在",))
        donor = self._donors.get(sample.donor_id)
        if donor is None:
            reasons.append("供体授权不存在")
        else:
            if donor.withdrawn:
                reasons.append("供体已撤回授权")
            missing = [p for p in required_purposes if p not in donor.allowed_purposes]
            if missing:
                reasons.append(f"授权用途缺少 {sorted(missing)}")

        qc = self._qcs.get(qc_id)
        if qc is None:
            reasons.append("细胞质控结果不存在")
        elif qc.sample_id != sample_id:
            reasons.append("质控结果与样本不匹配")
        elif not qc.passed:
            reasons.append("细胞质控未通过")
        elif qc.stale:
            reasons.append("质控阈值已变更，结果需要复算")

        batch = self._batches.get(qc.batch_id) if qc is not None else None
        if qc is not None and batch is None:
            reasons.append("制备批次不存在")
        elif batch is not None and batch.status != "accepted":
            reasons.append("制备批次质量复核未通过")

        seq = self._files.get(file_id)
        if seq is None:
            reasons.append("测序文件不存在")
        elif seq.sample_id != sample_id:
            reasons.append("测序文件与样本不匹配")
        elif not seq.verified:
            reasons.append("测序文件校验未通过")

        return EligibilityReport(not reasons, tuple(reasons))

    # ---- 读取 ----------------------------------------------------------

    def get(self, cohort_id: str):
        from .records import CohortSnapshot  # 避免循环导入

        events = self._store.stream(cohort_id)
        if not events:
            return None
        study_type = events[0].payload["study_type"]
        restriction = events[0].payload.get("restriction", {})
        access_conditions = {
            purpose: frozenset(roles)
            for purpose, roles in events[0].payload.get("access_conditions", {}).items()
        }
        members: set[str] = set()
        exclusions: dict[str, str] = {}
        for event in events[1:]:
            sample_id = event.payload.get("sample_id")
            if sample_id is None:
                continue
            if event.event_type == EventType.SAMPLE_INCLUDED:
                members.add(sample_id)
                exclusions.pop(sample_id, None)
            elif event.event_type == EventType.SAMPLE_EXCLUDED:
                members.discard(sample_id)
                exclusions[sample_id] = event.payload["reason"]
        return CohortSnapshot(
            cohort_id,
            study_type,
            restriction,
            frozenset(members),
            dict(exclusions),
            access_conditions,
            sealed=False,
        )

    def _last_event(self, cohort_id: str, event_type: EventType, sample_id: str) -> Event:
        for event in reversed(self._store.stream(cohort_id)):
            if event.event_type == event_type and event.payload.get("sample_id") == sample_id:
                return event
        raise DomainError("幂等重放找不到原始事件", code="idempotency_replay_failed")
