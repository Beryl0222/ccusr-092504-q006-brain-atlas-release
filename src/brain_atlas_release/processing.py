"""分析运行与分片幂等。

大型批次被切成若干分片，每个分片可独立重试；幂等键为
``(run_id, shard_index)``，同一运行中同一分片只计入一次。运行完成要求
全部分片成功，且产物清单的溯源指纹与启动时固定的清单一致。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .catalog import AggregateType, EventType
from .errors import DomainError, DuplicateIngestionError, LineageError
from .lineage import LineageService
from .provenance import ProvenanceManifest
from .store import Event, EventStore


@dataclass(frozen=True)
class RunSnapshot:
    run_id: str
    status: str            # started | completed | superseded | failed
    manifest_fingerprint: str
    expected_shards: int
    completed_shards: frozenset[int]
    failed_shards: frozenset[int]
    superseded: bool
    supersedes_run: str | None


class AnalysisRunService:
    def __init__(self, store: EventStore, *, lineage: LineageService | None = None) -> None:
        self._store = store
        self._lineage = lineage

    # ---- 命令 ----------------------------------------------------------

    def start(
        self,
        run_id: str,
        *,
        manifest: ProvenanceManifest,
        expected_shards: int,
        supersedes_run: str | None = None,
    ) -> Event:
        if self.get(run_id) is not None:
            raise DomainError(f"运行 {run_id} 已存在", code="run_exists")
        if expected_shards < 1:
            raise DomainError("运行至少包含一个分片", code="shards_required")
        if supersedes_run is not None:
            if self._lineage is None:
                raise LineageError("登记后继运行需要谱系服务")
            self._lineage.validate_successor(supersedes_run, manifest)
        return self._store.append(
            event_type=EventType.ANALYSIS_STARTED,
            aggregate_type=AggregateType.RUN,
            aggregate_id=run_id,
            summary=f"启动分析运行 {run_id}（{expected_shards} 个分片）",
            payload={
                "manifest": manifest.as_dict(),
                "manifest_fingerprint": manifest.fingerprint(),
                "expected_shards": expected_shards,
                "supersedes_run": supersedes_run,
            },
        )

    def complete_shard(
        self,
        run_id: str,
        *,
        shard_index: int,
        output_refs: list[str],
        manifest_fingerprint: str,
        record_count: int,
    ) -> Event:
        """登记一个分片完成。重复提交同一分片返回原事件，绝不重复计入。"""
        run = self.get(run_id)
        if run is None:
            raise DomainError(f"运行 {run_id} 尚未启动", code="run_not_found")
        if not 0 <= shard_index < run.expected_shards:
            raise DomainError(
                f"分片序号 {shard_index} 超出 0..{run.expected_shards - 1}",
                code="shard_out_of_range",
            )
        if manifest_fingerprint != run.manifest_fingerprint:
            raise DomainError(
                "分片使用的溯源清单与运行启动时不一致，必须作为新运行登记",
                code="fingerprint_mismatch",
            )
        if record_count < 0:
            raise DomainError("分片记录数不能为负", code="record_count_invalid")
        existing = self._find_shard_event(run_id, shard_index)
        if existing is not None:
            # 重试到达：核对后原样返回，记录数不被第二次累加。
            if (existing.payload.get("manifest_fingerprint") != manifest_fingerprint
                    or existing.payload.get("record_count") != record_count):
                raise DuplicateIngestionError(
                    f"分片 {shard_index} 已以不同内容计入，禁止重复计入",
                )
            return existing
        return self._store.append(
            event_type=EventType.ANALYSIS_SHARD_COMPLETED,
            aggregate_type=AggregateType.RUN,
            aggregate_id=run_id,
            summary=f"分片 {shard_index} 完成（{record_count} 条记录）",
            payload={
                "shard_index": shard_index,
                "output_refs": list(output_refs),
                "manifest_fingerprint": manifest_fingerprint,
                "record_count": record_count,
            },
            expected_version=self._store.version(run_id),
        )

    def fail_shard(self, run_id: str, *, shard_index: int, reason: str) -> Event:
        run = self.get(run_id)
        if run is None:
            raise DomainError(f"运行 {run_id} 尚未启动", code="run_not_found")
        if shard_index in run.completed_shards:
            raise DomainError(f"分片 {shard_index} 已完成计入，不能改为失败", code="shard_closed")
        return self._store.append(
            event_type=EventType.ANALYSIS_SHARD_COMPLETED,
            aggregate_type=AggregateType.RUN,
            aggregate_id=run_id,
            summary=f"分片 {shard_index} 失败：{reason}",
            payload={
                "shard_index": shard_index,
                "failed": True,
                "reason": reason,
            },
            expected_version=self._store.version(run_id),
        )

    def complete(self, run_id: str) -> Event:
        run = self.get(run_id)
        if run is None:
            raise DomainError(f"运行 {run_id} 尚未启动", code="run_not_found")
        if run.failed_shards:
            raise DomainError(
                f"存在失败分片 {sorted(run.failed_shards)}，请重试后再完成",
                code="shards_failed",
            )
        if len(run.completed_shards) != run.expected_shards:
            missing = sorted(set(range(run.expected_shards)) - run.completed_shards)
            raise DomainError(
                f"运行仍有未完成分片 {missing}（已成功 {sorted(run.completed_shards)}）",
                code="shards_incomplete",
            )
        if self._last_terminal_event(run_id, EventType.ANALYSIS_COMPLETED) is not None:
            return self._last_terminal_event(run_id, EventType.ANALYSIS_COMPLETED)
        total_records = sum(
            e.payload.get("record_count", 0)
            for e in self._store.stream(run_id)
            if e.event_type == EventType.ANALYSIS_SHARD_COMPLETED and not e.payload.get("failed")
        )
        return self._store.append(
            event_type=EventType.ANALYSIS_COMPLETED,
            aggregate_type=AggregateType.RUN,
            aggregate_id=run_id,
            summary=f"分析运行 {run_id} 完成（共 {total_records} 条记录）",
            payload={"total_record_count": total_records},
            expected_version=self._store.version(run_id),
        )

    # ---- 读取 ----------------------------------------------------------

    def get(self, run_id: str) -> RunSnapshot | None:
        events = self._store.stream(run_id)
        if not events:
            return None
        started = events[0]
        expected = started.payload["expected_shards"]
        completed: set[int] = set()
        failed: set[int] = set()
        superseded = False
        finished = False
        for event in events[1:]:
            if event.event_type == EventType.ANALYSIS_SHARD_COMPLETED and not event.payload.get("failed"):
                completed.add(event.payload["shard_index"])
                failed.discard(event.payload["shard_index"])
            elif event.event_type == EventType.ANALYSIS_SHARD_COMPLETED and event.payload.get("failed"):
                failed.add(event.payload["shard_index"])
            elif event.event_type == EventType.ANALYSIS_SUPERSEDED:
                superseded = True
            elif event.event_type == EventType.ANALYSIS_COMPLETED:
                finished = True
        status = "completed" if finished else "superseded" if superseded else "started"
        if failed and not finished:
            status = "failed"
        return RunSnapshot(
            run_id=run_id,
            status=status,
            manifest_fingerprint=started.payload["manifest_fingerprint"],
            expected_shards=expected,
            completed_shards=frozenset(completed),
            failed_shards=frozenset(failed),
            superseded=superseded,
            supersedes_run=started.payload.get("supersedes_run"),
        )

    def total_record_count(self, run_id: str) -> int:
        return sum(
            e.payload.get("record_count", 0)
            for e in self._store.stream(run_id)
            if e.event_type == EventType.ANALYSIS_SHARD_COMPLETED and not e.payload.get("failed")
        )

    # ---- 内部 -----------------------------------------------------------

    def _find_shard_event(self, run_id: str, shard_index: int) -> Event | None:
        for event in reversed(self._store.stream(run_id)):
            if (
                event.event_type == EventType.ANALYSIS_SHARD_COMPLETED
                and not event.payload.get("failed")
                and event.payload.get("shard_index") == shard_index
            ):
                return event
        return None

    def _last_terminal_event(self, run_id: str, event_type: EventType) -> Event | None:
        for event in reversed(self._store.stream(run_id)):
            if event.event_type == event_type:
                return event
        return None
