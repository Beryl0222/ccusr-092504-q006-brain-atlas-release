"""仅追加的领域事件存储。

事件一经追加不可修改、不可删除；聚合版本按追加顺序从 1 单调递增。
撤回或质量复核通过追加否定事件表达，旧事实仍然保留，用于论文事实的
历史可追溯。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from uuid import uuid4

from .catalog import EVENT_AGGREGATE, AggregateType, EventType
from .contracts import validate_event
from .errors import ConcurrencyError, ImmutabilityError


def utc_now() -> datetime:
    """统一使用带时区的 UTC 时间戳。"""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


@dataclass(frozen=True)
class Event:
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    occurred_at: str
    version: int
    summary: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    causation_id: str | None = None  # 触发本事件的上游事件（如撤回→后继版本）

    def as_dict(self) -> dict[str, Any]:
        data = {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "occurred_at": self.occurred_at,
            "version": self.version,
            "summary": self.summary,
            **self.payload,
        }
        if self.causation_id is not None:
            data["causation_id"] = self.causation_id
        return data


class EventStore:
    """内存事件存储；接入方可替换为持久化实现，接口保持一致。"""

    def __init__(self, schema: Mapping[str, Any] | None = None) -> None:
        self._events: dict[str, list[Event]] = defaultdict(list)
        self._event_ids: set[str] = set()
        self._schema = schema

    # ---- 读取 ----------------------------------------------------------

    def stream(self, aggregate_id: str) -> list[Event]:
        """返回某聚合的完整事件流（副本，防止外部就地修改）。"""
        return list(self._events.get(aggregate_id, ()))

    def version(self, aggregate_id: str) -> int:
        events = self._events.get(aggregate_id)
        return events[-1].version if events else 0

    def all_events(self) -> list[Event]:
        ordered: list[Event] = []
        for events in self._events.values():
            ordered.extend(events)
        return sorted(ordered, key=lambda event: (event.occurred_at, event.event_id))

    def exists(self, event_id: str) -> bool:
        return event_id in self._event_ids

    # ---- 写入 ----------------------------------------------------------

    def append(
        self,
        *,
        event_type: EventType | str,
        aggregate_type: AggregateType | str,
        aggregate_id: str,
        summary: str,
        payload: Mapping[str, Any] | None = None,
        causation_id: str | None = None,
        expected_version: int | None = None,
        event_id: str | None = None,
        occurred_at: str | None = None,
    ) -> Event:
        """追加一个事件。

        expected_version 为调用方读到的聚合版本，用于乐观并发；缺省时直接
        追加到流末尾。同一 event_id 重复追加会被拒绝，保证投递幂等。
        """
        event_type = EventType(event_type)
        aggregate_type = AggregateType(aggregate_type)
        registered = EVENT_AGGREGATE.get(event_type)
        if registered is not None and registered != aggregate_type:
            raise ImmutabilityError(
                f"事件 {event_type} 只能登记在聚合 {registered} 上",
                code="aggregate_mismatch",
            )

        current = self._events.get(aggregate_id, [])
        next_version = current[-1].version + 1 if current else 1
        if expected_version is not None and expected_version != (next_version - 1):
            raise ConcurrencyError(
                f"聚合 {aggregate_id} 期望版本 {expected_version}，实际 {next_version - 1}"
            )

        event_id = event_id or f"evt-{uuid4().hex[:12]}"
        if event_id in self._event_ids:
            raise ImmutabilityError(f"事件 {event_id} 已存在，事实不可重复计入")

        event = Event(
            event_id=event_id,
            event_type=event_type.value,
            aggregate_type=aggregate_type.value,
            aggregate_id=aggregate_id,
            occurred_at=occurred_at or utc_now_iso(),
            version=next_version,
            summary=summary,
            payload=dict(payload or {}),
            causation_id=causation_id,
        )

        if self._schema is not None:
            issues = validate_event(event.as_dict(), self._schema)
            if issues:
                codes = ", ".join(f"{item.field}:{item.code}" for item in issues)
                raise ImmutabilityError(f"事件未通过交换契约：{codes}", code="contract_violation")

        self._events[aggregate_id].append(event)
        self._event_ids.add(event_id)
        return event

    def append_many(self, events: Iterable[Mapping[str, Any]]) -> list[Event]:
        """批量追加；用于发布恢复时逐条补齐，已存在的事件不会被覆盖。"""
        appended: list[Event] = []
        for raw in events:
            data = dict(raw)
            if data.get("event_id") and self.exists(data["event_id"]):
                # 发布中断恢复：同一事实只认第一次追加，跳过而不是报错。
                continue
            event = self.append(
                event_type=data["event_type"],
                aggregate_type=data["aggregate_type"],
                aggregate_id=data["aggregate_id"],
                summary=data["summary"],
                payload={k: v for k, v in data.items()
                         if k not in {"event_id", "event_type", "aggregate_type",
                                      "aggregate_id", "occurred_at", "version",
                                      "summary", "causation_id"}},
                causation_id=data.get("causation_id"),
                event_id=data.get("event_id"),
                occurred_at=data.get("occurred_at"),
            )
            appended.append(event)
        return appended
