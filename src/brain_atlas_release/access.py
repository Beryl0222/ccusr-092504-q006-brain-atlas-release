"""用途与角色的最小授权。

授权判定需要同时满足：

1. 请求用途在队列登记的 ``access_conditions`` 中；
2. 请求角色属于该用途允许的角色集合；
3. 请求人持有该队列该用途的有效授权（数据管理员按最小授权发放）；
4. 队列限制（疾病、祖源等）与请求人申报的研究背景逐项匹配；
5. 队列成员的供体授权覆盖该用途，且未撤回。

临床接口只能读取聚合层面的研究发现视图，视图强制附带"相关性不等于
个体诊断"的边界，禁止读取样本/测序/质控等个体级资产。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .catalog import AggregateType, Purpose
from .cohorts import CohortRegistry
from .errors import AccessDeniedError
from .records import DonorRegistry, SampleRegistry

# 临床接口永远不可接触的个体级资产类型。
INDIVIDUAL_LEVEL_ASSETS: frozenset[AggregateType] = frozenset({
    AggregateType.DONOR,
    AggregateType.SAMPLE,
    AggregateType.BATCH,
    AggregateType.SEQUENCING_FILE,
    AggregateType.CELL_QC,
})


@dataclass(frozen=True)
class AccessRequest:
    requester_id: str
    roles: frozenset[str]
    purpose: Purpose | str
    cohort_id: str
    study_context: Mapping[str, str] = field(default_factory=dict)
    asset_type: AggregateType | str | None = None  # 访问具体资产时填写


@dataclass(frozen=True)
class Grant:
    requester_id: str
    cohort_id: str
    purpose: str
    roles: frozenset[str]


class AccessService:
    def __init__(
        self,
        *,
        cohorts: CohortRegistry,
        donors: DonorRegistry,
        samples: SampleRegistry,
    ) -> None:
        self._cohorts = cohorts
        self._donors = donors
        self._samples = samples
        self._grants: set[Grant] = set()

    # ---- 授权发放（数据管理员） ----------------------------------------

    def grant(
        self,
        requester_id: str,
        *,
        cohort_id: str,
        purpose: Purpose | str,
        roles: Iterable[str],
    ) -> Grant:
        """按最小授权发放：角色集合必须是队列该用途允许角色的子集。"""
        purpose = Purpose(purpose)
        cohort = self._cohorts.get(cohort_id)
        if cohort is None:
            raise AccessDeniedError(f"队列 {cohort_id} 不存在")
        allowed = cohort.access_conditions.get(purpose.value)
        if not allowed:
            raise AccessDeniedError(f"队列未开放用途 {purpose.value}")
        role_set = frozenset(roles)
        if not role_set or not role_set <= allowed:
            raise AccessDeniedError(
                f"授权角色 {sorted(role_set)} 超出最小角色集合 {sorted(allowed)}",
                code="roles_not_minimal",
            )
        grant = Grant(requester_id, cohort_id, purpose.value, role_set)
        self._grants.add(grant)
        return grant

    def revoke(self, grant: Grant) -> None:
        self._grants.discard(grant)

    # ---- 授权判定 ------------------------------------------------------

    def authorize(self, request: AccessRequest) -> None:
        """不满足任一条件即抛 AccessDeniedError；通过则返回 None。"""
        purpose = Purpose(request.purpose)
        cohort = self._cohorts.get(request.cohort_id)
        if cohort is None:
            raise AccessDeniedError(f"队列 {request.cohort_id} 不存在")

        if purpose == Purpose.CLINICAL:
            raise AccessDeniedError(
                "临床用途必须通过 clinical_view 读取研究发现，不得直接取数",
                code="clinical_requires_view",
            )

        allowed_roles = cohort.access_conditions.get(purpose.value)
        if not allowed_roles:
            raise AccessDeniedError(f"队列未开放用途 {purpose.value}")
        roles = frozenset(request.roles)
        if not roles & allowed_roles:
            raise AccessDeniedError(
                f"角色 {sorted(roles)} 不满足用途 {purpose.value} 的要求",
                code="role_mismatch",
            )

        if not any(
            g.requester_id == request.requester_id
            and g.cohort_id == request.cohort_id
            and g.purpose == purpose.value
            and g.roles & roles
            for g in self._grants
        ):
            raise AccessDeniedError("缺少该队列该用途的有效授权", code="grant_missing")

        for key, value in cohort.restriction.items():
            if request.study_context.get(key) != value:
                raise AccessDeniedError(
                    f"研究背景 {key}={request.study_context.get(key)!r} 与队列限制 {value!r} 不符",
                    code="restriction_mismatch",
                )

        asset_type = request.asset_type
        if asset_type is not None and AggregateType(asset_type) in INDIVIDUAL_LEVEL_ASSETS:
            self._assert_member_consent(cohort, purpose)

    def _assert_member_consent(self, cohort, purpose: Purpose) -> None:
        for sample_id in cohort.members:
            sample = self._samples.get(sample_id)
            if sample is None:
                raise AccessDeniedError(f"成员样本 {sample_id} 缺少登记", code="lineage_gap")
            donor = self._donors.get(sample.donor_id)
            if donor is None or donor.withdrawn:
                raise AccessDeniedError(f"样本 {sample_id} 供体授权不可用", code="consent_unavailable")
            if purpose.value not in donor.allowed_purposes:
                raise AccessDeniedError(
                    f"样本 {sample_id} 供体授权不覆盖用途 {purpose.value}",
                    code="consent_scope_mismatch",
                )
