"""派生资产谱系：固定输入、参数与软件版本，支持后继版本与分片运行。

任何派生资产（质控矩阵、注释版本、分析结果、公开数据包）都必须固定其
输入清单、参数和软件版本。撤回或质量复核不删除既有事实，而是生成后继
版本；已用于正式论文的事实永远保留。大型批次可分片重试，同一运行的
同一分片只计入一次。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Mapping

from .contracts import ContractIssue

ASSET_STATUSES = ("active", "superseded", "withdrawn")


@dataclass(frozen=True)
class InputPin:
    """一个被固定的输入资产及其修订号。"""

    asset_id: str
    revision: int


@dataclass(frozen=True)
class SoftwarePin:
    """一个被固定的软件及其版本。"""

    name: str
    version: str


@dataclass(frozen=True)
class AssetManifest:
    """派生资产的生成依据：输入清单、参数与软件版本。"""

    inputs: tuple[InputPin, ...]
    parameters: Mapping[str, Any]
    software: tuple[SoftwarePin, ...]

    def __post_init__(self) -> None:
        # 参数表在固定后不可再被改写
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


def validate_manifest(manifest: AssetManifest) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    if not manifest.inputs:
        issues.append(ContractIssue("inputs", "required", "派生资产必须固定输入清单"))
    for index, pin in enumerate(manifest.inputs):
        if not pin.asset_id.strip():
            issues.append(ContractIssue(f"inputs[{index}].asset_id", "non_empty_string", "输入资产标识必须是非空字符串"))
        if pin.revision < 1:
            issues.append(ContractIssue(f"inputs[{index}].revision", "positive_integer", "输入修订号必须是正整数"))
    if not manifest.software:
        issues.append(ContractIssue("software", "required", "派生资产必须固定软件版本"))
    for index, pin in enumerate(manifest.software):
        if not pin.name.strip() or not pin.version.strip():
            issues.append(ContractIssue(f"software[{index}]", "non_empty_string", "软件名称与版本必须是非空字符串"))
    return sorted(issues, key=lambda issue: (issue.field, issue.code))


@dataclass(frozen=True)
class DerivedAsset:
    """一个带修订号的派生资产。事实不可删除，只能被后继版本取代或标记撤回。"""

    asset_id: str
    kind: str
    manifest: AssetManifest
    revision: int = 1
    status: str = "active"
    supersedes: str | None = None
    used_in_formal_paper: bool = False
    withdrawal_reason: str | None = None


def validate_asset(asset: DerivedAsset) -> list[ContractIssue]:
    issues = validate_manifest(asset.manifest)
    if asset.status not in ASSET_STATUSES:
        issues.append(ContractIssue("status", "unsupported_value", "资产状态未在契约中登记"))
    if asset.revision < 1:
        issues.append(ContractIssue("revision", "positive_integer", "修订号必须是正整数"))
    return sorted(issues, key=lambda issue: (issue.field, issue.code))


def withdraw_asset(asset: DerivedAsset, reason: str) -> tuple[DerivedAsset | None, list[ContractIssue]]:
    """撤回资产：标记状态并保留事实，返回更新后的资产。"""
    if not reason.strip():
        return None, [ContractIssue("withdrawal_reason", "required", "撤回必须说明原因")]
    return replace(asset, status="withdrawn", withdrawal_reason=reason), []


def create_successor(asset: DerivedAsset, manifest: AssetManifest) -> tuple[DerivedAsset, DerivedAsset]:
    """质量复核或撤回后生成后继版本：旧版本标记为 superseded，新版本修订号加一。"""
    previous = replace(asset, status="superseded") if asset.status == "active" else asset
    successor = DerivedAsset(
        asset_id=asset.asset_id,
        kind=asset.kind,
        manifest=manifest,
        revision=asset.revision + 1,
        status="active",
        supersedes=asset.asset_id,
        used_in_formal_paper=asset.used_in_formal_paper,
    )
    return previous, successor


def ensure_deletable(asset: DerivedAsset) -> list[ContractIssue]:
    """已用于正式论文的事实不可删除；其余资产也不物理删除，仅允许撤回。"""
    if asset.used_in_formal_paper:
        return [ContractIssue("asset_id", "published_fact_retained", "已用于正式论文的事实不可删除，只能撤回并生成后继版本")]
    return [ContractIssue("asset_id", "deletion_not_supported", "资产不可物理删除，请使用撤回并生成后继版本")]


@dataclass(frozen=True)
class ShardResult:
    """一个分片的一次完成上报。"""

    shard_id: str
    attempt: int
    output_digest: str


@dataclass(frozen=True)
class ShardedRun:
    """一次分片分析运行。完成的分片按分片标识去重，重试不会重复计入。"""

    run_id: str
    manifest: AssetManifest
    shard_ids: tuple[str, ...]
    completed: Mapping[str, ShardResult]

    @staticmethod
    def start(run_id: str, manifest: AssetManifest, shard_ids: tuple[str, ...]) -> ShardedRun:
        return ShardedRun(run_id=run_id, manifest=manifest, shard_ids=shard_ids, completed=MappingProxyType({}))


def record_shard(run: ShardedRun, result: ShardResult) -> tuple[ShardedRun, list[ContractIssue]]:
    """记录分片完成。同一分片重复上报相同结果时为幂等空操作，不计入第二次。"""
    if result.shard_id not in run.shard_ids:
        return run, [ContractIssue("shard_id", "unknown_shard", "分片不属于该运行")]
    existing = run.completed.get(result.shard_id)
    if existing is not None:
        if existing.output_digest == result.output_digest:
            return run, []
        return run, [ContractIssue("shard_id", "conflicting_result", "同一分片上报了不一致的结果")]
    completed = dict(run.completed)
    completed[result.shard_id] = result
    return replace(run, completed=MappingProxyType(completed)), []


def completion_count(run: ShardedRun) -> int:
    return len(run.completed)


def is_complete(run: ShardedRun) -> bool:
    return all(shard_id in run.completed for shard_id in run.shard_ids)
