"""领域规则违例。

所有违例都携带稳定的 ``code``，便于接入方按代码而不是按中文文案处理。
"""

from __future__ import annotations


class DomainError(Exception):
    """全部领域违例的基类。"""

    code = "domain_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class ConcurrencyError(DomainError):
    """事件版本与流中当前版本冲突。"""

    code = "version_conflict"


class ImmutabilityError(DomainError):
    """试图改写或删除已经落库的事实。"""

    code = "event_immutable"


class ProvenanceError(DomainError):
    """派生资产缺少固定输入清单、参数或软件版本。"""

    code = "provenance_incomplete"


class AccessDeniedError(DomainError):
    """用途或角色不满足资产的访问条件。"""

    code = "access_denied"


class LineageError(DomainError):
    """撤回、质量复核或后继版本的谱系规则被违反。"""

    code = "lineage_violation"


class DuplicateIngestionError(DomainError):
    """同一分片在同一运行中被重复计入。"""

    code = "duplicate_shard"


class PublicationError(DomainError):
    """发布的资产、引用或审批不满足发布条件。"""

    code = "publication_invalid"


class AnnotationConflictError(DomainError):
    """并行标注在无具备资质裁定人的情况下无法收敛。"""

    code = "annotation_conflict"


class EvidenceBoundaryError(DomainError):
    """研究发现缺少证据范围，或被越界解释成个体临床结论。"""

    code = "evidence_boundary_violation"
