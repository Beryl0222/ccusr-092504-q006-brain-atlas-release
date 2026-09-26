"""标注冲突裁定与研究发现证据边界测试。"""

from __future__ import annotations

import unittest

from support import build_world

from brain_atlas_release.errors import (
    AnnotationConflictError,
    DomainError,
    EvidenceBoundaryError,
)
from brain_atlas_release.findings import CLINICAL_BOUNDARY_NOTICE


class AnnotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = build_world()
        w = self.world
        w.annotations.submit(
            "ann-1", target_ref="cell:cluster-7",
            annotation_version="v1", annotator_id="ann-a", label="小胶质细胞",
        )
        w.annotations.submit(
            "ann-1", target_ref="cell:cluster-7",
            annotation_version="v2", annotator_id="ann-b", label="星形胶质细胞",
        )

    def test_conflict_requires_qualified_adjudicator(self) -> None:
        w = self.world
        w.adjudications.raise_conflict(
            "ann-1", competing_versions=["v1", "v2"], raised_by="ann-a"
        )
        with self.assertRaises(AnnotationConflictError):
            w.adjudications.adjudicate(
                "ann-1", chosen_version="v1",
                adjudicator_id="intern", adjudicator_roles={"junior_curator"},
                rationale="凭感觉",
            )
        self.assertIsNone(w.adjudications.approved_label("ann-1"))

    def test_qualified_adjudication_approves_version(self) -> None:
        w = self.world
        w.adjudications.raise_conflict(
            "ann-1", competing_versions=["v1", "v2"], raised_by="ann-a"
        )
        event = w.adjudications.adjudicate(
            "ann-1", chosen_version="v2",
            adjudicator_id="dr-chen",
            adjudicator_roles={"senior_curator"},
            rationale="形态学与标记基因均支持星形胶质细胞",
        )
        self.assertEqual(event.event_type, "ANNOTATION_APPROVED")
        self.assertEqual(w.adjudications.approved_label("ann-1"), "星形胶质细胞")
        self.assertFalse(w.annotations.get("ann-1").open_conflict)

    def test_conflict_must_reference_submitted_versions(self) -> None:
        with self.assertRaises(DomainError):
            self.world.adjudications.raise_conflict(
                "ann-1", competing_versions=["v1", "v9"], raised_by="ann-a"
            )


class FindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = build_world()

    def _record(self, **overrides):
        kwargs = dict(
            statement="APOE ε4 携带与小胶质细胞亚群扩增相关",
            cohort_id="cohort-ad",
            source_run_ids=["run-1"],
            evidence_scope={
                "cohort_id": "cohort-ad", "sample_count": 128, "run_ids": ["run-1"],
            },
            limitations=["仅覆盖欧洲祖源样本", "未校正药物暴露"],
        )
        kwargs.update(overrides)
        return self.world.findings.record("finding-1", **kwargs)

    def test_record_requires_scope_and_limitations(self) -> None:
        with self.assertRaises(EvidenceBoundaryError):
            self._record(limitations=[])
        with self.assertRaises(EvidenceBoundaryError):
            self._record(evidence_scope={"cohort_id": "cohort-ad"})

    def test_clinical_overreach_rejected(self) -> None:
        with self.assertRaises(EvidenceBoundaryError) as ctx:
            self._record(statement="该基因关联可用于诊断阿尔茨海默病")
        self.assertEqual(ctx.exception.code, "claim_overreach")

    def test_clinical_view_carries_boundary_notice(self) -> None:
        self._record()
        view = self.world.findings.clinical_view("finding-1")
        self.assertEqual(view["boundary_notice"], CLINICAL_BOUNDARY_NOTICE)
        self.assertEqual(view["claim_type"], "association")
        self.assertIsNone(view["individual_level_data"])

    def test_correction_appends_without_erasing(self) -> None:
        self._record()
        self.world.findings.correct(
            "finding-1",
            statement="APOE ε4 携带与小胶质细胞亚群扩增弱相关",
            limitations=["仅覆盖欧洲祖源样本", "样本量扩大后效应减弱"],
            reason="纳入第二批数据后复算",
        )
        snapshot = self.world.findings.get("finding-1")
        self.assertTrue(snapshot.corrected)
        self.assertIn("弱相关", snapshot.statement)
        # 原始记录事件仍在流中。
        kinds = [e.event_type for e in self.world.store.stream("finding-1")]
        self.assertEqual(kinds, ["FINDING_RECORDED", "FINDING_CORRECTED"])


if __name__ == "__main__":
    unittest.main()
