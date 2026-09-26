"""并行标注冲突的裁定。

多个标注者对同一目标给出不同标签时，必须登记冲突，并由具备资质的
人员（见 ``QUALIFIED_ANNOTATION_ROLES``）裁定。裁定选择某个已提交的
标注版本，产生 ANNOTATION_APPROVED；未经裁定的版本不能进入分析输入。
"""

from __future__ import annotations

from .catalog import AggregateType, EventType
from .errors import AnnotationConflictError, DomainError
from .records import QUALIFIED_ANNOTATION_ROLES, AnnotationRegistry
from .store import Event, EventStore


class AnnotationAdjudicator:
    def __init__(self, store: EventStore, annotations: AnnotationRegistry) -> None:
        self._store = store
        self._annotations = annotations

    def raise_conflict(
        self,
        annotation_id: str,
        *,
        competing_versions: list[str],
        raised_by: str,
    ) -> Event:
        snapshot = self._annotations.get(annotation_id)
        if snapshot is None:
            raise DomainError(f"注释 {annotation_id} 不存在", code="annotation_not_found")
        if len(competing_versions) < 2:
            raise DomainError("冲突至少涉及两个标注版本", code="conflict_too_small")
        unknown = [v for v in competing_versions if v not in snapshot.versions]
        if unknown:
            raise DomainError(f"冲突引用了未提交的版本 {unknown}", code="version_unknown")
        return self._store.append(
            event_type=EventType.ANNOTATION_CONFLICT_RAISED,
            aggregate_type=AggregateType.ANNOTATION,
            aggregate_id=annotation_id,
            summary=f"登记标注冲突：{sorted(competing_versions)}",
            payload={
                "competing_versions": sorted(competing_versions),
                "raised_by": raised_by,
                "resolved": False,
            },
            expected_version=self._store.version(annotation_id),
        )

    def adjudicate(
        self,
        annotation_id: str,
        *,
        chosen_version: str,
        adjudicator_id: str,
        adjudicator_roles: set[str],
        rationale: str,
    ) -> Event:
        snapshot = self._annotations.get(annotation_id)
        if snapshot is None:
            raise DomainError(f"注释 {annotation_id} 不存在", code="annotation_not_found")
        if not QUALIFIED_ANNOTATION_ROLES & adjudicator_roles:
            raise AnnotationConflictError(
                "标注冲突必须由具备资质的人员裁定",
                code="unqualified_adjudicator",
            )
        if chosen_version not in snapshot.versions:
            raise DomainError(f"裁定版本 {chosen_version} 未提交", code="version_unknown")
        if not rationale.strip():
            raise DomainError("裁定必须给出理由", code="rationale_required")
        label = self._label_of(annotation_id, chosen_version)
        return self._store.append(
            event_type=EventType.ANNOTATION_APPROVED,
            aggregate_type=AggregateType.ANNOTATION,
            aggregate_id=annotation_id,
            summary=f"裁定采用标注 {chosen_version}：{label}",
            payload={
                "annotation_version": chosen_version,
                "label": label,
                "adjudicator_id": adjudicator_id,
                "adjudicator_roles": sorted(adjudicator_roles),
                "rationale": rationale,
            },
            expected_version=self._store.version(annotation_id),
        )

    def approved_label(self, annotation_id: str) -> str | None:
        snapshot = self._annotations.get(annotation_id)
        return snapshot.approved_label if snapshot else None

    def _label_of(self, annotation_id: str, version: str) -> str:
        for event in self._store.stream(annotation_id):
            if (
                event.event_type == EventType.ANNOTATION_SUBMITTED
                and event.payload.get("annotation_version") == version
            ):
                return event.payload["label"]
        raise DomainError(f"版本 {version} 无提交记录", code="version_unknown")
