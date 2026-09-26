"""派生资产溯源清单。

任何派生资产（分析运行、图表、数据包修订）都必须固定三类输入：

1. ``inputs``：输入资产引用及其当时版本；
2. ``parameters``：完整参数，键排序后做规范化 JSON；
3. ``software``：软件组件名与版本。

清单序列化后计算 sha256 指纹；指纹相同才可视为同一派生定义，
大型批次重跑与发布恢复据此幂等。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class InputRef:
    """一个固定版本的输入资产引用。"""

    ref_type: str          # donor_record | deidentified_sample | cell_qc_result | ...
    ref_id: str
    version: int           # 引用时该聚合的事件版本
    checksum: str | None = None  # 文件类输入附带校验值

    def as_dict(self) -> dict[str, Any]:
        data = {"ref_type": self.ref_type, "ref_id": self.ref_id, "version": self.version}
        if self.checksum is not None:
            data["checksum"] = self.checksum
        return data


@dataclass(frozen=True)
class SoftwareComponent:
    name: str
    version: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version}


@dataclass(frozen=True)
class ProvenanceManifest:
    inputs: tuple[InputRef, ...]
    parameters: Mapping[str, Any]
    software: tuple[SoftwareComponent, ...]

    def __post_init__(self) -> None:
        if not self.inputs:
            raise ValueError("派生资产必须固定至少一个输入")
        if not self.software:
            raise ValueError("派生资产必须记录软件版本")
        versions = {(item.name, item.version) for item in self.software}
        if any(not item.name.strip() or not item.version.strip() for item in self.software):
            raise ValueError("软件组件名与版本不能为空")
        if len(versions) != len(self.software):
            raise ValueError("软件组件清单存在重复登记")
        ids = {(item.ref_type, item.ref_id, item.version) for item in self.inputs}
        if len(ids) != len(self.inputs):
            raise ValueError("输入清单存在重复版本引用")
        if any(item.version < 1 for item in self.inputs):
            raise ValueError("输入引用必须指向已存在的正版本")

    def as_dict(self) -> dict[str, Any]:
        return {
            "inputs": [item.as_dict() for item in sorted(self.inputs, key=_input_sort_key)],
            "parameters": _canonical(self.parameters),
            "software": [item.as_dict() for item in sorted(self.software, key=lambda s: s.name)],
        }

    def canonical_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def _input_sort_key(item: InputRef) -> tuple[str, str, int]:
    return item.ref_type, item.ref_id, item.version


def _canonical(value: Any) -> Any:
    """把参数转成键有序、可 JSON 序列化的规范化形式。"""
    if isinstance(value, Mapping):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def verify_inputs_current(
    manifest: ProvenanceManifest,
    current_versions: Mapping[tuple[str, str], int],
) -> list[InputRef]:
    """返回已过时（上游出现新版本）的输入引用。

    过时不自动删除派生资产，只提示需要生成后继版本——旧版本若已用于正式
    论文，必须原样保留。
    """
    stale: list[InputRef] = []
    for item in manifest.inputs:
        current = current_versions.get((item.ref_type, item.ref_id))
        if current is not None and current != item.version:
            stale.append(item)
    return sorted(stale, key=_input_sort_key)
