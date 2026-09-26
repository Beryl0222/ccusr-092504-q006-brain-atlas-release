"""队列访问最小授权、并行标注裁定与研究发现证据边界。

不同疾病或祖源队列的访问条件按用途和角色最小授权；并行标注冲突由
有资质人员裁定；研究发现必须标明证据范围与限制；临床接口不得把
相关性转换成个体诊断。
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import ContractIssue

# 允许登记为裁定人的资质
ADJUDICATOR_QUALIFICATIONS = ("senior_curator", "neuropathologist", "consortium_review_board")


@dataclass(frozen=True)
class CohortPolicy:
    """一个疾病/祖源队列的访问策略：(角色, 用途) -> 可见字段集。"""

    cohort_id: str
    disease_area: str
    ancestry_group: str
    allowed: frozenset[tuple[str, str]]
    field_matrix: dict[tuple[str, str], frozenset[str]]


@dataclass(frozen=True)
class AccessRequest:
    cohort_id: str
    role: str
    purpose: str
    fields: frozenset[str]


@dataclass(frozen=True)
class AccessGrant:
    cohort_id: str
    role: str
    purpose: str
    fields: frozenset[str]
    trimmed_fields: frozenset[str]


def authorize(policy: CohortPolicy, request: AccessRequest) -> tuple[AccessGrant | None, list[ContractIssue]]:
    """按用途和角色最小授权：未登记的组合拒绝；已登记的组合裁剪到策略允许的字段集。"""
    if request.cohort_id != policy.cohort_id:
        return None, [ContractIssue("cohort_id", "cohort_mismatch", "请求队列与策略队列不一致")]
    key = (request.role, request.purpose)
    if key not in policy.allowed:
        return None, [ContractIssue("purpose", "not_authorized", "该角色与用途组合未在队列策略中登记")]
    permitted = policy.field_matrix.get(key, frozenset())
    granted = frozenset(request.fields) & permitted
    trimmed = frozenset(request.fields) - permitted
    if not granted:
        return None, [ContractIssue("fields", "not_authorized", "请求的字段均超出该角色与用途的授权范围")]
    return AccessGrant(
        cohort_id=policy.cohort_id,
        role=request.role,
        purpose=request.purpose,
        fields=granted,
        trimmed_fields=trimmed,
    ), []


@dataclass(frozen=True)
class AnnotationLabel:
    annotator_id: str
    label: str


@dataclass(frozen=True)
class AnnotationConflict:
    """同一细胞在并行标注中出现的分歧。"""

    cell_id: str
    labels: tuple[AnnotationLabel, ...]


@dataclass(frozen=True)
class Adjudication:
    cell_id: str
    chosen_label: str
    adjudicator_id: str
    qualification: str
    rationale: str


def adjudicate(
    conflict: AnnotationConflict,
    adjudicator_id: str,
    qualification: str,
    chosen_label: str,
    rationale: str,
) -> tuple[Adjudication | None, list[ContractIssue]]:
    """由有资质人员裁定并行标注冲突。裁定必须落在已提出的标签中并说明理由。"""
    issues: list[ContractIssue] = []
    proposed = {item.label for item in conflict.labels}
    if len(proposed) < 2:
        issues.append(ContractIssue("labels", "conflict_required", "不存在需要裁定的标注冲突"))
    if qualification not in ADJUDICATOR_QUALIFICATIONS:
        issues.append(ContractIssue("qualification", "not_qualified", "裁定人资质未在契约中登记"))
    if chosen_label not in proposed:
        issues.append(ContractIssue("chosen_label", "unsupported_value", "裁定结果必须来自已提出的标签"))
    if not rationale.strip():
        issues.append(ContractIssue("rationale", "required", "裁定必须说明理由"))
    if issues:
        return None, sorted(issues, key=lambda issue: (issue.field, issue.code))
    return Adjudication(
        cell_id=conflict.cell_id,
        chosen_label=chosen_label,
        adjudicator_id=adjudicator_id,
        qualification=qualification,
        rationale=rationale,
    ), []


@dataclass(frozen=True)
class ResearchFinding:
    """一条研究发现。必须标明证据范围与限制，且仅代表群体层面的关联。"""

    finding_id: str
    claim: str
    evidence_scope: tuple[str, ...]
    limitations: tuple[str, ...]


def validate_finding(finding: ResearchFinding) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    if not finding.claim.strip():
        issues.append(ContractIssue("claim", "required", "研究发现必须给出结论陈述"))
    if not finding.evidence_scope:
        issues.append(ContractIssue("evidence_scope", "required", "研究发现必须标明证据范围"))
    if not finding.limitations:
        issues.append(ContractIssue("limitations", "required", "研究发现必须标明限制"))
    return sorted(issues, key=lambda issue: (issue.field, issue.code))


def population_statement(finding: ResearchFinding) -> tuple[str | None, list[ContractIssue]]:
    """生成面向外部的陈述，自动附带证据范围与限制。"""
    issues = validate_finding(finding)
    if issues:
        return None, issues
    scope = "；".join(finding.evidence_scope)
    limits = "；".join(finding.limitations)
    return f"{finding.claim}（证据范围：{scope}；限制：{limits}）", []


def assess_individual(finding: ResearchFinding, individual_id: str) -> list[ContractIssue]:
    """临床接口守卫：群体层面的关联不得转换成个体诊断。"""
    return [ContractIssue(
        "finding_id",
        "correlation_not_diagnosis",
        "研究发现为群体层面的关联，不得用于个体诊断或疗效推断",
    )]
