"""公开数据包发布：图表溯源、断点续发与下载回执。

发布中断后只补齐缺少的资产、引用和通知；每个图表都能回溯到可用数据
版本与审批；下载者凭回执确认自己拿到的是哪次修订。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .contracts import ContractIssue
from .lineage import InputPin


@dataclass(frozen=True)
class FigureRef:
    """论文图表引用：固定来源资产修订与审批。"""

    figure_id: str
    paper_id: str
    source_assets: tuple[InputPin, ...]
    approval_id: str


def validate_figure(figure: FigureRef) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    if not figure.source_assets:
        issues.append(ContractIssue("source_assets", "required", "图表必须固定来源资产及修订"))
    if not figure.approval_id.strip():
        issues.append(ContractIssue("approval_id", "required", "图表必须关联审批"))
    return sorted(issues, key=lambda issue: (issue.field, issue.code))


def check_figure_traceability(
    figure: FigureRef,
    available: Mapping[str, frozenset[int]],
) -> list[ContractIssue]:
    """确认图表引用的每个资产修订当前可用（未被撤回或取代）。"""
    issues = validate_figure(figure)
    for pin in figure.source_assets:
        revisions = available.get(pin.asset_id)
        if revisions is None or pin.revision not in revisions:
            issues.append(ContractIssue(
                "source_assets",
                "revision_unavailable",
                f"图表引用的资产修订不可用：{pin.asset_id}@{pin.revision}",
            ))
    return sorted(issues, key=lambda issue: (issue.field, issue.code, issue.message))


@dataclass(frozen=True)
class ReleasePackage:
    """一次公开数据包修订的全部应发内容。"""

    package_id: str
    revision: int
    asset_ids: tuple[str, ...]
    citations: tuple[str, ...]
    notifications: tuple[str, ...]
    figures: tuple[FigureRef, ...]


@dataclass(frozen=True)
class PublishProgress:
    """发布中断时已完成的资产、引用与通知。"""

    published_assets: frozenset[str]
    published_citations: frozenset[str]
    notified: frozenset[str]


@dataclass(frozen=True)
class PublishPlan:
    """续发计划：仅包含尚缺的部分，已完成的不重复发布。"""

    package_id: str
    revision: int
    remaining_assets: tuple[str, ...]
    remaining_citations: tuple[str, ...]
    remaining_notifications: tuple[str, ...]

    @property
    def is_complete(self) -> bool:
        return not (self.remaining_assets or self.remaining_citations or self.remaining_notifications)


def resume_plan(package: ReleasePackage, progress: PublishProgress) -> PublishPlan:
    """根据已完成进度计算续发计划，只补齐缺少的资产、引用和通知。"""
    return PublishPlan(
        package_id=package.package_id,
        revision=package.revision,
        remaining_assets=tuple(item for item in package.asset_ids if item not in progress.published_assets),
        remaining_citations=tuple(item for item in package.citations if item not in progress.published_citations),
        remaining_notifications=tuple(item for item in package.notifications if item not in progress.notified),
    )


@dataclass(frozen=True)
class DownloadReceipt:
    """下载回执：下载者据此确认拿到的是哪次修订。"""

    package_id: str
    revision: int
    content_digest: str
    issued_to: str


def issue_receipt(package: ReleasePackage, content_digest: str, downloader_id: str) -> DownloadReceipt:
    return DownloadReceipt(
        package_id=package.package_id,
        revision=package.revision,
        content_digest=content_digest,
        issued_to=downloader_id,
    )


def verify_receipt(
    receipt: DownloadReceipt,
    package: ReleasePackage,
    content_digest: str,
) -> list[ContractIssue]:
    """核对回执与数据包：修订号与内容摘要必须一致。"""
    issues: list[ContractIssue] = []
    if receipt.package_id != package.package_id:
        issues.append(ContractIssue("package_id", "mismatch", "回执与数据包标识不一致"))
    if receipt.revision != package.revision:
        issues.append(ContractIssue("revision", "mismatch", "回执修订号与数据包修订号不一致"))
    if receipt.content_digest != content_digest:
        issues.append(ContractIssue("content_digest", "mismatch", "内容摘要不一致，下载内容可能已变动"))
    return sorted(issues, key=lambda issue: (issue.field, issue.code))
