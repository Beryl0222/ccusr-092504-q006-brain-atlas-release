"""撤回与质量复核的影响传播、后继版本谱系。

上游事实（授权撤回、批次复核结论、注释版本、质控阈值）发生变化时，
不删除、不改写旧事实，而是：

1. 登记上游否定/变更事件（records 模块负责）；
2. 把引用旧版本的派生资产标记为 ``superseded``；
3. 新运行固定新的输入版本，通过 ``supersedes_run`` 串成后继链；
4. 已用于正式论文图表/数据包的版本打 ``paper_locked``，永远保留。
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import AggregateType, EventType
from .errors import LineageError
from .provenance import ProvenanceManifest
from .store import EventStore


@dataclass(frozen=True)
class ImpactReport:
    """一次上游变更对派生资产的影响。"""

    source_aggregate: str
    impacted_runs: tuple[str, ...]
    paper_locked_runs: tuple[str, ...]


class LineageService:
    def __init__(self, store: EventStore) -> None:
        self._store = store

    # ---- 谱系索引 ------------------------------------------------------

    def runs_using(self, ref_type: str, ref_id: str) -> list[str]:
        """返回所有输入清单引用某资产（任意版本）的分析运行。"""
        hits: list[str] = []
        for event in self._store.all_events():
            if event.aggregate_type != AggregateType.RUN:
                continue
            for item in event.payload.get("manifest", {}).get("inputs", []):
                if item.get("ref_type") == ref_type and item.get("ref_id") == ref_id:
                    hits.append(event.aggregate_id)
                    break
        # 去重但保持顺序。
        return list(dict.fromkeys(hits))

    def is_paper_locked(self, run_id: str) -> bool:
        """运行已被正式论文图表或已发布数据包引用即锁定，不可删除。"""
        for event in self._store.all_events():
            if event.event_type == EventType.FIGURE_APPROVED:
                if run_id in event.payload.get("source_run_ids", []):
                    return True
            if event.event_type == EventType.PACKAGE_RELEASED:
                if run_id in event.payload.get("source_run_ids", []):
                    return True
        return False

    # ---- 影响传播 ------------------------------------------------------

    def propagate_change(self, ref_type: str, ref_id: str, *, reason: str) -> ImpactReport:
        """把上游变更传播到全部受影响运行。

        已用于正式论文的运行不标记废止（其结论必须原样可查），只列入
        paper_locked 清单提示必须发布更正后继版本。
        """
        impacted: list[str] = []
        locked: list[str] = []
        for run_id in self.runs_using(ref_type, ref_id):
            if self._already_superseded(run_id, ref_type, ref_id):
                continue
            if self.is_paper_locked(run_id):
                locked.append(run_id)
                continue
            self._store.append(
                event_type=EventType.ANALYSIS_SUPERSEDED,
                aggregate_type=AggregateType.RUN,
                aggregate_id=run_id,
                summary=f"上游 {ref_type}/{ref_id} 变更，运行废止：{reason}",
                payload={
                    "changed_ref_type": ref_type,
                    "changed_ref_id": ref_id,
                    "reason": reason,
                    "paper_locked": False,
                },
                expected_version=self._store.version(run_id),
            )
            impacted.append(run_id)
        return ImpactReport(ref_id, tuple(impacted), tuple(locked))

    def validate_successor(
        self,
        supersedes_run: str,
        new_manifest: ProvenanceManifest,
    ) -> None:
        """校验后继清单：被后继运行必须存在，且新清单固定了更新的输入版本。"""
        old_events = self._store.stream(supersedes_run)
        if not old_events:
            raise LineageError(f"被后继运行 {supersedes_run} 不存在")
        old_inputs = {
            (i["ref_type"], i["ref_id"]): i["version"]
            for e in old_events
            for i in e.payload.get("manifest", {}).get("inputs", [])
        }
        new_inputs = {(i.ref_type, i.ref_id): i.version for i in new_manifest.inputs}
        if not any(new_ver > old_inputs.get(key, 0) for key, new_ver in new_inputs.items()):
            raise LineageError(
                f"后继运行必须固定至少一个比 {supersedes_run} 更新的输入版本",
                code="successor_requires_newer_inputs",
            )

    def _already_superseded(self, run_id: str, ref_type: str, ref_id: str) -> bool:
        return any(
            e.event_type == EventType.ANALYSIS_SUPERSEDED
            and e.payload.get("changed_ref_type") == ref_type
            and e.payload.get("changed_ref_id") == ref_id
            for e in self._store.stream(run_id)
        )
