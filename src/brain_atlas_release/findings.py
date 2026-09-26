"""研究发现与证据边界。

每条研究发现必须显式登记：

* ``claim_type``：当前只允许 ``association``（相关性/关联），不允许
  疗效、因果或个体诊断式断言；
* ``evidence_scope``：证据覆盖的队列、样本量与分析运行；
* ``limitations``：限制（混杂、样本量、祖源覆盖等）。

发现可被更正（FINDING_CORRECTED），更正不删除原结论。临床接口只能经
``clinical_view`` 取得附带固定边界声明的聚合视图。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .catalog import AggregateType, EventType
from .errors import EvidenceBoundaryError
from .store import Event, EventStore

CLAIM_ASSOCIATION = "association"
ALLOWED_CLAIM_TYPES: frozenset[str] = frozenset({CLAIM_ASSOCIATION})

# 研究资产中禁止出现的临床越界措辞（提交端应在文案层面另外引导）。
PROHIBITED_PHRASES: tuple[str, ...] = (
    "疗效",
    "治愈率",
    "可用于诊断",
    "诊断标准",
    "临床诊断",
    "治疗效果",
)

CLINICAL_BOUNDARY_NOTICE = (
    "本结论为群体水平的研究关联，不构成针对任何个体的诊断、预后判断或"
    "治疗建议；相关性不等于因果，亦不代表临床疗效。"
)


@dataclass(frozen=True)
class FindingSnapshot:
    finding_id: str
    claim_type: str
    statement: str
    cohort_id: str
    source_run_ids: frozenset[str]
    evidence_scope: dict
    limitations: tuple[str, ...]
    corrected: bool


class FindingService:
    def __init__(self, store: EventStore) -> None:
        self._store = store

    def record(
        self,
        finding_id: str,
        *,
        statement: str,
        cohort_id: str,
        source_run_ids: Iterable[str],
        evidence_scope: dict,
        limitations: Iterable[str],
    ) -> Event:
        self._validate_text(statement)
        source_run_ids = tuple(source_run_ids)
        if not source_run_ids:
            raise EvidenceBoundaryError("研究发现必须引用至少一个分析运行")
        scope = self._validate_scope(evidence_scope)
        limits = tuple(item.strip() for item in limitations if item.strip())
        if not limits:
            raise EvidenceBoundaryError("研究发现必须登记证据限制")
        return self._store.append(
            event_type=EventType.FINDING_RECORDED,
            aggregate_type=AggregateType.FINDING,
            aggregate_id=finding_id,
            summary=f"登记研究发现：{statement}",
            payload={
                "claim_type": CLAIM_ASSOCIATION,
                "statement": statement,
                "cohort_id": cohort_id,
                "source_run_ids": sorted(source_run_ids),
                "evidence_scope": scope,
                "limitations": list(limits),
            },
        )

    def correct(
        self,
        finding_id: str,
        *,
        statement: str,
        limitations: Iterable[str],
        reason: str,
    ) -> Event:
        if self.get(finding_id) is None:
            raise EvidenceBoundaryError(f"研究发现 {finding_id} 不存在")
        self._validate_text(statement)
        limits = tuple(item.strip() for item in limitations if item.strip())
        if not limits:
            raise EvidenceBoundaryError("更正后的发现仍需登记证据限制")
        if not reason.strip():
            raise EvidenceBoundaryError("更正必须说明原因")
        return self._store.append(
            event_type=EventType.FINDING_CORRECTED,
            aggregate_type=AggregateType.FINDING,
            aggregate_id=finding_id,
            summary=f"更正研究发现：{reason}",
            payload={"statement": statement, "limitations": list(limits), "reason": reason},
            expected_version=self._store.version(finding_id),
        )

    def get(self, finding_id: str) -> FindingSnapshot | None:
        events = self._store.stream(finding_id)
        if not events:
            return None
        first = events[0].payload
        statement = first["statement"]
        limitations = tuple(first["limitations"])
        corrected = False
        for event in events[1:]:
            if event.event_type == EventType.FINDING_CORRECTED:
                statement = event.payload["statement"]
                limitations = tuple(event.payload["limitations"])
                corrected = True
        return FindingSnapshot(
            finding_id=finding_id,
            claim_type=first["claim_type"],
            statement=statement,
            cohort_id=first["cohort_id"],
            source_run_ids=frozenset(first["source_run_ids"]),
            evidence_scope=dict(first["evidence_scope"]),
            limitations=limitations,
            corrected=corrected,
        )

    def clinical_view(self, finding_id: str) -> dict:
        """临床接口唯一允许的读取形态：聚合结论 + 强制边界声明。"""
        snapshot = self.get(finding_id)
        if snapshot is None:
            raise EvidenceBoundaryError(f"研究发现 {finding_id} 不存在")
        return {
            "finding_id": snapshot.finding_id,
            "statement": snapshot.statement,
            "claim_type": snapshot.claim_type,
            "evidence_scope": snapshot.evidence_scope,
            "boundary_notice": CLINICAL_BOUNDARY_NOTICE,
            "individual_level_data": None,
        }

    # ---- 校验 ----------------------------------------------------------

    def _validate_text(self, statement: str) -> None:
        text = statement.strip()
        if not text:
            raise EvidenceBoundaryError("研究发现表述不能为空")
        hit = next((phrase for phrase in PROHIBITED_PHRASES if phrase in text), None)
        if hit is not None:
            raise EvidenceBoundaryError(
                f"研究关联不得写成临床结论，命中禁用措辞“{hit}”",
                code="claim_overreach",
            )

    def _validate_scope(self, scope: dict) -> dict:
        if not isinstance(scope, dict) or not scope:
            raise EvidenceBoundaryError("必须描述证据范围")
        for key in ("cohort_id", "sample_count", "run_ids"):
            if key not in scope:
                raise EvidenceBoundaryError(f"证据范围缺少 {key}")
        if int(scope["sample_count"]) <= 0:
            raise EvidenceBoundaryError("证据范围的样本量必须为正")
        if not scope["run_ids"]:
            raise EvidenceBoundaryError("证据范围必须列出分析运行")
        return {
            "cohort_id": scope["cohort_id"],
            "sample_count": int(scope["sample_count"]),
            "run_ids": list(scope["run_ids"]),
        }
