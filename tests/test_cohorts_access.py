"""队列纳排与最小授权测试。"""

from __future__ import annotations

import unittest

from support import build_world, make_ready_sample

from brain_atlas_release.access import AccessRequest
from brain_atlas_release.catalog import AggregateType, Purpose
from brain_atlas_release.errors import AccessDeniedError, DomainError


class CohortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = build_world()
        self.ids = make_ready_sample(self.world)
        self.world.cohorts.define(
            "cohort-ad",
            study_type="disease",
            restriction={"disease": "alzheimer"},
            access_conditions={
                Purpose.DISEASE_RESEARCH: ["researcher"],
                Purpose.RESEARCH: ["researcher", "consortium_member"],
            },
        )

    def test_inclusion_requires_full_chain(self) -> None:
        w = self.world
        event = w.cohorts.include(
            "cohort-ad", sample_id=self.ids["sample_id"],
            qc_id=self.ids["qc_id"], file_id=self.ids["file_id"],
        )
        self.assertEqual(event.event_type, "SAMPLE_INCLUDED")
        # 幂等：重复纳入返回原事件，不追加新事实。
        again = w.cohorts.include(
            "cohort-ad", sample_id=self.ids["sample_id"],
            qc_id=self.ids["qc_id"], file_id=self.ids["file_id"],
        )
        self.assertEqual(event.event_id, again.event_id)
        self.assertEqual(2, w.store.version("cohort-ad"))

    def test_inclusion_rejected_when_qc_stale(self) -> None:
        w = self.world
        w.qcs.change_threshold(self.ids["qc_id"], new_threshold_version="qc-v4", reason="收紧")
        with self.assertRaises(DomainError) as ctx:
            w.cohorts.include(
                "cohort-ad", sample_id=self.ids["sample_id"],
                qc_id=self.ids["qc_id"], file_id=self.ids["file_id"],
            )
        self.assertEqual(ctx.exception.code, "inclusion_ineligible")

    def test_inclusion_rejected_after_consent_withdrawal(self) -> None:
        w = self.world
        w.donors.withdraw(self.ids["donor_id"], reason="退出")
        with self.assertRaises(DomainError):
            w.cohorts.include(
                "cohort-ad", sample_id=self.ids["sample_id"],
                qc_id=self.ids["qc_id"], file_id=self.ids["file_id"],
            )

    def test_exclusion_is_recorded_with_reason(self) -> None:
        w = self.world
        w.cohorts.include(
            "cohort-ad", sample_id=self.ids["sample_id"],
            qc_id=self.ids["qc_id"], file_id=self.ids["file_id"],
        )
        w.cohorts.exclude("cohort-ad", sample_id=self.ids["sample_id"], reason="复核剔除")
        cohort = w.cohorts.get("cohort-ad")
        self.assertNotIn(self.ids["sample_id"], cohort.members)
        self.assertEqual(cohort.exclusions[self.ids["sample_id"]], "复核剔除")


class AccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = build_world()
        self.ids = make_ready_sample(self.world)
        self.world.cohorts.define(
            "cohort-ad",
            study_type="disease",
            restriction={"disease": "alzheimer"},
            access_conditions={Purpose.DISEASE_RESEARCH: ["researcher"]},
        )
        self.world.cohorts.include(
            "cohort-ad", sample_id=self.ids["sample_id"],
            qc_id=self.ids["qc_id"], file_id=self.ids["file_id"],
        )

    def _request(self, **overrides) -> AccessRequest:
        base = dict(
            requester_id="alice",
            roles=frozenset({"researcher"}),
            purpose=Purpose.DISEASE_RESEARCH,
            cohort_id="cohort-ad",
            study_context={"disease": "alzheimer"},
        )
        base.update(overrides)
        return AccessRequest(**base)

    def test_minimal_grant_allows_access(self) -> None:
        w = self.world
        w.access.grant(
            "alice", cohort_id="cohort-ad",
            purpose=Purpose.DISEASE_RESEARCH, roles=["researcher"],
        )
        w.access.authorize(self._request())

    def test_grant_beyond_allowed_roles_rejected(self) -> None:
        w = self.world
        with self.assertRaises(AccessDeniedError) as ctx:
            w.access.grant(
                "alice", cohort_id="cohort-ad",
                purpose=Purpose.DISEASE_RESEARCH, roles=["researcher", "admin"],
            )
        self.assertEqual(ctx.exception.code, "roles_not_minimal")

    def test_unregistered_purpose_denied(self) -> None:
        w = self.world
        with self.assertRaises(AccessDeniedError):
            w.access.grant("alice", cohort_id="cohort-ad",
                           purpose=Purpose.ANCESTRY_RESEARCH, roles=["researcher"])

    def test_missing_grant_denied(self) -> None:
        with self.assertRaises(AccessDeniedError) as ctx:
            self.world.access.authorize(self._request())
        self.assertEqual(ctx.exception.code, "grant_missing")

    def test_restriction_mismatch_denied(self) -> None:
        w = self.world
        w.access.grant("alice", cohort_id="cohort-ad",
                       purpose=Purpose.DISEASE_RESEARCH, roles=["researcher"])
        with self.assertRaises(AccessDeniedError) as ctx:
            w.access.authorize(self._request(study_context={"disease": "parkinson"}))
        self.assertEqual(ctx.exception.code, "restriction_mismatch")

    def test_individual_level_access_checks_member_consent(self) -> None:
        w = self.world
        w.access.grant("alice", cohort_id="cohort-ad",
                       purpose=Purpose.DISEASE_RESEARCH, roles=["researcher"])
        # 授权用途不含该队列成员供体的用途 → 个体级访问被拒。
        w.donors.update_scope(
            self.ids["donor_id"], allowed_purposes=[Purpose.RESEARCH], reason="收窄"
        )
        with self.assertRaises(AccessDeniedError) as ctx:
            w.access.authorize(self._request(asset_type=AggregateType.SAMPLE))
        self.assertEqual(ctx.exception.code, "consent_scope_mismatch")

    def test_clinical_purpose_never_grants_raw_access(self) -> None:
        w = self.world
        with self.assertRaises(AccessDeniedError) as ctx:
            w.access.authorize(self._request(purpose=Purpose.CLINICAL))
        self.assertEqual(ctx.exception.code, "clinical_requires_view")


if __name__ == "__main__":
    unittest.main()
