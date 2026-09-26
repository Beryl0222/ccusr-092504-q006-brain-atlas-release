"""论文图表审批与数据包发布。

图表：审批时固定所引用的分析运行及其数据版本，任何图表都能回溯到
可用数据版本（运行清单指纹）和审批记录。

发布：数据包先组装（资产、引用、通知对象），发布时逐项核验资产；发布
中断后 ``resume_publication`` 只补齐缺失步骤——已发布修订不重复登记，
已通知对象不重复通知。更正以修订形式追加，旧修订原样保留，下载者可凭
修订号和清单指纹确认自己拿到的是哪次修订。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .catalog import AggregateType, EventType
from .errors import DomainError, PublicationError
from .processing import AnalysisRunService
from .store import Event, EventStore


@dataclass(frozen=True)
class PackageAsset:
    asset_type: str       # paper_figure | research_finding | data_file
    asset_id: str
    checksum: str

    def as_dict(self) -> dict[str, str]:
        return {"asset_type": self.asset_type, "asset_id": self.asset_id, "checksum": self.checksum}


@dataclass(frozen=True)
class Citation:
    kind: str             # paper | dataset
    value: str

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "value": self.value}


@dataclass(frozen=True)
class ReleaseSnapshot:
    package_id: str
    assembled: bool
    current_revision: int          # 0 表示尚未发布
    released_revisions: tuple[int, ...]
    notified: frozenset[str]
    assets: tuple[PackageAsset, ...]
    citations: tuple[Citation, ...]


class FigureService:
    def __init__(self, store: EventStore, runs: AnalysisRunService) -> None:
        self._store = store
        self._runs = runs

    def approve(
        self,
        figure_id: str,
        *,
        title: str,
        source_run_ids: Iterable[str],
        approver_id: str,
        approval_ref: str,
        paper_ref: str,
        supersedes_figure: str | None = None,
    ) -> Event:
        run_ids = tuple(source_run_ids)
        if not run_ids:
            raise PublicationError("图表必须引用至少一个分析运行")
        run_versions: list[dict[str, Any]] = []
        for run_id in run_ids:
            run = self._runs.get(run_id)
            if run is None:
                raise PublicationError(f"运行 {run_id} 不存在", code="run_not_found")
            if run.status != "completed":
                raise PublicationError(
                    f"运行 {run_id} 状态为 {run.status}，不能作为图表数据来源",
                    code="run_not_completed",
                )
            if run.superseded:
                raise PublicationError(
                    f"运行 {run_id} 已废止，新图表必须引用后继数据版本",
                    code="run_superseded",
                )
            run_versions.append({
                "run_id": run_id,
                "run_version": self._store.version(run_id),
                "manifest_fingerprint": run.manifest_fingerprint,
            })
        if self.get(figure_id) is not None and supersedes_figure is None:
            raise PublicationError("图表已有审批，换版必须显式声明 supersedes_figure")
        return self._store.append(
            event_type=EventType.FIGURE_APPROVED,
            aggregate_type=AggregateType.FIGURE,
            aggregate_id=figure_id,
            summary=f"审批论文图表《{title}》",
            payload={
                "title": title,
                "source_run_ids": sorted(run_ids),
                "run_versions": sorted(run_versions, key=lambda item: item["run_id"]),
                "approver_id": approver_id,
                "approval_ref": approval_ref,
                "paper_ref": paper_ref,
                "supersedes_figure": supersedes_figure,
            },
        )

    def get(self, figure_id: str) -> Mapping[str, Any] | None:
        events = self._store.stream(figure_id)
        if not events:
            return None
        return events[-1].payload

    def traceability(self, figure_id: str) -> dict[str, Any]:
        """返回图表 → 审批 → 运行版本 → 固定输入清单的完整回溯链。"""
        events = self._store.stream(figure_id)
        approvals = [e for e in events if e.event_type == EventType.FIGURE_APPROVED]
        if not approvals:
            raise PublicationError(f"图表 {figure_id} 尚未审批", code="figure_not_approved")
        approval = approvals[-1]
        chain: list[dict[str, Any]] = []
        for item in approval.payload["run_versions"]:
            run_events = self._store.stream(item["run_id"])
            manifest = run_events[0].payload["manifest"]
            chain.append({
                "run_id": item["run_id"],
                "run_version_at_approval": item["run_version"],
                "manifest_fingerprint": item["manifest_fingerprint"],
                "inputs": manifest["inputs"],
                "parameters": manifest["parameters"],
                "software": manifest["software"],
            })
        return {
            "figure_id": figure_id,
            "title": approval.payload["title"],
            "paper_ref": approval.payload["paper_ref"],
            "approval": {
                "approver_id": approval.payload["approver_id"],
                "approval_ref": approval.payload["approval_ref"],
                "approved_at": approval.occurred_at,
            },
            "data_lineage": chain,
        }


class PackageService:
    def __init__(
        self,
        store: EventStore,
        *,
        figures: FigureService,
        verify_asset: Any | None = None,
    ) -> None:
        self._store = store
        self._figures = figures
        # verify_asset(asset) 抛异常表示资产缺失或校验失败；缺省只校验图表已审批。
        self._verify_asset = verify_asset

    # ---- 组装 ----------------------------------------------------------

    def assemble(
        self,
        package_id: str,
        *,
        assets: Iterable[PackageAsset],
        citations: Iterable[Citation],
    ) -> Event:
        snapshot = self.get(package_id)
        if snapshot is not None and snapshot.assembled:
            raise PublicationError(f"数据包 {package_id} 已组装，换版请发布修订")
        asset_list = tuple(assets)
        citation_list = tuple(citations)
        self._validate_contents(asset_list, citation_list)
        return self._store.append(
            event_type=EventType.PACKAGE_ASSEMBLED,
            aggregate_type=AggregateType.PACKAGE,
            aggregate_id=package_id,
            summary=f"组装公开数据包 {package_id}",
            payload={
                "assets": [a.as_dict() for a in sorted(asset_list, key=_asset_key)],
                "citations": [c.as_dict() for c in sorted(citation_list, key=lambda c: (c.kind, c.value))],
            },
        )

    # ---- 发布与恢复 ----------------------------------------------------

    def publish(self, package_id: str) -> Event:
        """发布首个修订（revision=1），不发送通知。"""
        snapshot = self.get(package_id)
        if snapshot is None or not snapshot.assembled:
            raise PublicationError("数据包尚未组装", code="package_not_assembled")
        if snapshot.released_revisions:
            raise PublicationError("已有发布修订，换版请使用 publish_revision")
        return self._release(package_id, revision=1, changelog="首次发布")

    def notify(self, package_id: str, recipients: Iterable[str]) -> list[Event]:
        """通知下载者；已通知对象不重复通知（任意中断后可安全重放）。"""
        snapshot = self.get(package_id)
        if snapshot is None or snapshot.current_revision == 0:
            raise PublicationError("数据包尚未发布，不能发送通知", code="package_not_released")
        produced: list[Event] = []
        for recipient in sorted(frozenset(recipients) - snapshot.notified):
            produced.append(self._notify(package_id, recipient))
        return produced

    def resume_publication(self, package_id: str, *, recipients: Iterable[str]) -> list[Event]:
        """发布中断后的恢复：只补齐缺失的发布与通知，绝不重复。"""
        snapshot = self.get(package_id)
        if snapshot is None or not snapshot.assembled:
            raise PublicationError("数据包尚未组装", code="package_not_assembled")
        produced: list[Event] = []
        if not snapshot.released_revisions:
            produced.append(self._release(package_id, revision=1, changelog="首次发布"))
        produced.extend(self.notify(package_id, recipients))
        return produced

    def publish_revision(self, package_id: str, *, changelog: str) -> Event:
        """以修订形式追加发布（资产清单沿用组装结果）。"""
        snapshot = self.get(package_id)
        if snapshot is None or not snapshot.assembled:
            raise PublicationError("数据包尚未组装", code="package_not_assembled")
        if not snapshot.released_revisions:
            raise PublicationError("请先发布首个修订")
        if not changelog.strip():
            raise PublicationError("修订必须附变更说明")
        revision = max(snapshot.released_revisions) + 1
        return self._release(package_id, revision=revision, changelog=changelog)

    # ---- 下载确认 ------------------------------------------------------

    def revision_manifest(self, package_id: str, revision: int) -> dict[str, Any]:
        for event in self._store.stream(package_id):
            if event.event_type in (EventType.PACKAGE_RELEASED, EventType.PACKAGE_REVISION_PUBLISHED) \
                    and event.payload.get("revision") == revision:
                return {
                    "package_id": package_id,
                    "revision": revision,
                    "released_at": event.occurred_at,
                    "changelog": event.payload.get("changelog"),
                    "supersedes_revision": event.payload.get("supersedes_revision"),
                    "manifest_fingerprint": event.payload["manifest_fingerprint"],
                    "assets": event.payload["assets"],
                    "citations": event.payload["citations"],
                }
        raise PublicationError(f"修订 {revision} 不存在", code="revision_not_found")

    def verify_download(self, package_id: str, revision: int, observed_fingerprint: str) -> bool:
        """下载者核对：自己手中的清单指纹对应哪次已发布修订。"""
        expected = self.revision_manifest(package_id, revision)["manifest_fingerprint"]
        return observed_fingerprint.strip().lower() == expected

    def get(self, package_id: str) -> ReleaseSnapshot | None:
        events = self._store.stream(package_id)
        if not events:
            return None
        assets: tuple[PackageAsset, ...] = ()
        citations: tuple[Citation, ...] = ()
        assembled = False
        revisions: list[int] = []
        notified: set[str] = set()
        for event in events:
            if event.event_type == EventType.PACKAGE_ASSEMBLED:
                assembled = True
                assets = tuple(PackageAsset(a["asset_type"], a["asset_id"], a["checksum"])
                               for a in event.payload["assets"])
                citations = tuple(Citation(c["kind"], c["value"])
                                  for c in event.payload["citations"])
            elif event.event_type in (EventType.PACKAGE_RELEASED, EventType.PACKAGE_REVISION_PUBLISHED):
                revisions.append(event.payload["revision"])
            elif event.event_type == EventType.PACKAGE_NOTIFICATION_SENT:
                notified.add(event.payload["recipient"])
        return ReleaseSnapshot(
            package_id=package_id,
            assembled=assembled,
            current_revision=max(revisions, default=0),
            released_revisions=tuple(sorted(revisions)),
            notified=frozenset(notified),
            assets=assets,
            citations=citations,
        )

    # ---- 内部 ----------------------------------------------------------

    def _release(self, package_id: str, *, revision: int, changelog: str) -> Event:
        snapshot = self.get(package_id)
        # 发布前逐项核验资产：缺一项即不能发布；恢复时重新核验后才补齐发布事件。
        for asset in snapshot.assets:
            self._check_asset(asset)
        fingerprint = self._contents_fingerprint(snapshot.assets, snapshot.citations)
        event_type = (
            EventType.PACKAGE_RELEASED
            if revision == 1 else EventType.PACKAGE_REVISION_PUBLISHED
        )
        return self._store.append(
            event_type=event_type,
            aggregate_type=AggregateType.PACKAGE,
            aggregate_id=package_id,
            summary=f"发布数据包修订 {revision}",
            payload={
                "revision": revision,
                "supersedes_revision": revision - 1 if revision > 1 else None,
                "changelog": changelog,
                "manifest_fingerprint": fingerprint,
                "assets": [a.as_dict() for a in sorted(snapshot.assets, key=_asset_key)],
                "citations": [c.as_dict() for c in sorted(snapshot.citations, key=lambda c: (c.kind, c.value))],
            },
            expected_version=self._store.version(package_id),
        )

    def _notify(self, package_id: str, recipient: str) -> Event:
        snapshot = self.get(package_id)
        return self._store.append(
            event_type=EventType.PACKAGE_NOTIFICATION_SENT,
            aggregate_type=AggregateType.PACKAGE,
            aggregate_id=package_id,
            summary=f"通知 {recipient}：数据包修订 {snapshot.current_revision} 已发布",
            payload={"recipient": recipient, "revision": snapshot.current_revision},
            expected_version=self._store.version(package_id),
        )

    def _check_asset(self, asset: PackageAsset) -> None:
        if asset.asset_type == AggregateType.FIGURE:
            if self._figures.get(asset.asset_id) is None:
                raise PublicationError(f"图表 {asset.asset_id} 未审批，不能发布", code="asset_missing")
        if self._verify_asset is not None:
            self._verify_asset(asset)

    def _validate_contents(
        self,
        assets: tuple[PackageAsset, ...],
        citations: tuple[Citation, ...],
    ) -> None:
        if not assets:
            raise PublicationError("数据包至少包含一个资产")
        keys = [_asset_key(a) for a in assets]
        if len(keys) != len(set(keys)):
            raise PublicationError("资产清单存在重复登记")
        if any(not a.checksum.strip() for a in assets):
            raise PublicationError("每个资产必须附带校验值")
        if not citations:
            raise PublicationError("发布必须登记论文或数据集引用")
        if any(not c.value.strip() for c in citations):
            raise PublicationError("引用内容不能为空")

    @staticmethod
    def _contents_fingerprint(assets: Iterable[PackageAsset], citations: Iterable[Citation]) -> str:
        body = json.dumps(
            {
                "assets": [a.as_dict() for a in sorted(assets, key=_asset_key)],
                "citations": [c.as_dict() for c in sorted(citations, key=lambda c: (c.kind, c.value))],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _asset_key(asset: PackageAsset) -> tuple[str, str]:
    return asset.asset_type, asset.asset_id
