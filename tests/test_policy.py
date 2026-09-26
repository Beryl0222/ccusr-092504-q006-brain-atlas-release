from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from brain_atlas_release.policy import (
    AccessRequest,
    AnnotationConflict,
    AnnotationLabel,
    CohortPolicy,
    ResearchFinding,
    adjudicate,
    assess_individual,
    authorize,
    population_statement,
    validate_finding,
)


def _policy() -> CohortPolicy:
    return CohortPolicy(
        cohort_id="cohort-ad-ea",
        disease_area="alzheimer",
        ancestry_group="east_asian",
        allowed=frozenset({("analyst", "research"), ("clinician", "clinical_care")}),
        field_matrix={
            ("analyst", "research"): frozenset({"expression", "cell_type", "qc_flag"}),
            ("clinician", "clinical_care"): frozenset({"cohort_summary"}),
        },
    )


class AccessTests(unittest.TestCase):
    def test_unregistered_role_purpose_is_denied(self) -> None:
        grant, issues = authorize(_policy(), AccessRequest(
            cohort_id="cohort-ad-ea",
            role="analyst",
            purpose="marketing",
            fields=frozenset({"expression"}),
        ))
        self.assertIsNone(grant)
        self.assertEqual(["not_authorized"], [issue.code for issue in issues])

    def test_grant_is_trimmed_to_least_privilege(self) -> None:
        grant, issues = authorize(_policy(), AccessRequest(
            cohort_id="cohort-ad-ea",
            role="analyst",
            purpose="research",
            fields=frozenset({"expression", "donor_identity"}),
        ))
        self.assertEqual([], issues)
        self.assertEqual(frozenset({"expression"}), grant.fields)
        self.assertEqual(frozenset({"donor_identity"}), grant.trimmed_fields)

    def test_request_without_any_permitted_field_is_denied(self) -> None:
        grant, issues = authorize(_policy(), AccessRequest(
            cohort_id="cohort-ad-ea",
            role="analyst",
            purpose="research",
            fields=frozenset({"donor_identity"}),
        ))
        self.assertIsNone(grant)
        self.assertEqual(["not_authorized"], [issue.code for issue in issues])


class AdjudicationTests(unittest.TestCase):
    def _conflict(self) -> AnnotationConflict:
        return AnnotationConflict(
            cell_id="cell-1",
            labels=(
                AnnotationLabel("ann-1", "astrocyte"),
                AnnotationLabel("ann-2", "oligodendrocyte"),
            ),
        )

    def test_qualified_adjudicator_resolves_conflict(self) -> None:
        result, issues = adjudicate(
            self._conflict(), "curator-7", "senior_curator", "astrocyte", "形态与标记基因一致"
        )
        self.assertEqual([], issues)
        self.assertEqual("astrocyte", result.chosen_label)

    def test_unqualified_adjudicator_is_rejected(self) -> None:
        result, issues = adjudicate(
            self._conflict(), "intern-1", "trainee", "astrocyte", "理由"
        )
        self.assertIsNone(result)
        self.assertIn("not_qualified", {issue.code for issue in issues})

    def test_chosen_label_must_come_from_proposals(self) -> None:
        result, issues = adjudicate(
            self._conflict(), "curator-7", "senior_curator", "microglia", "理由"
        )
        self.assertIsNone(result)
        self.assertIn("unsupported_value", {issue.code for issue in issues})

    def test_rationale_is_required(self) -> None:
        result, issues = adjudicate(
            self._conflict(), "curator-7", "neuropathologist", "astrocyte", " "
        )
        self.assertIsNone(result)
        self.assertIn("required", {issue.code for issue in issues})


class FindingTests(unittest.TestCase):
    def test_finding_requires_scope_and_limitations(self) -> None:
        finding = ResearchFinding("f-1", "某基因与病程相关", (), ())
        fields = {issue.field for issue in validate_finding(finding)}
        self.assertIn("evidence_scope", fields)
        self.assertIn("limitations", fields)

    def test_population_statement_carries_scope_and_limits(self) -> None:
        finding = ResearchFinding(
            "f-2",
            "星形胶质细胞亚群与疾病阶段相关",
            ("东亚祖源队列", "尸检皮层样本"),
            ("横断面设计，不能推断因果",),
        )
        statement, issues = population_statement(finding)
        self.assertEqual([], issues)
        self.assertIn("证据范围", statement)
        self.assertIn("限制", statement)

    def test_clinical_interface_never_yields_individual_diagnosis(self) -> None:
        finding = ResearchFinding("f-3", "关联", ("队列",), ("限制",))
        issues = assess_individual(finding, "patient-1")
        self.assertEqual(["correlation_not_diagnosis"], [issue.code for issue in issues])


if __name__ == "__main__":
    unittest.main()
