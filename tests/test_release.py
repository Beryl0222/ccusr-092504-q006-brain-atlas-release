from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from brain_atlas_release.lineage import InputPin
from brain_atlas_release.release import (
    FigureRef,
    PublishProgress,
    ReleasePackage,
    check_figure_traceability,
    issue_receipt,
    resume_plan,
    validate_figure,
    verify_receipt,
)


def _package() -> ReleasePackage:
    return ReleasePackage(
        package_id="pkg-1",
        revision=3,
        asset_ids=("matrix-1", "annotation-7", "summary-2"),
        citations=("doi:10.1000/a", "doi:10.1000/b"),
        notifications=("lab-east", "lab-west"),
        figures=(
            FigureRef(
                figure_id="fig-1",
                paper_id="paper-9",
                source_assets=(InputPin("matrix-1", 2),),
                approval_id="approval-5",
            ),
        ),
    )


class FigureTests(unittest.TestCase):
    def test_figure_requires_sources_and_approval(self) -> None:
        figure = FigureRef("fig-2", "paper-9", (), "")
        fields = {issue.field for issue in validate_figure(figure)}
        self.assertIn("source_assets", fields)
        self.assertIn("approval_id", fields)

    def test_figure_traces_to_available_revision(self) -> None:
        figure = _package().figures[0]
        self.assertEqual([], check_figure_traceability(figure, {"matrix-1": frozenset({2})}))

    def test_figure_rejects_withdrawn_revision(self) -> None:
        figure = _package().figures[0]
        issues = check_figure_traceability(figure, {"matrix-1": frozenset({3})})
        self.assertEqual(["revision_unavailable"], [issue.code for issue in issues])


class ResumeTests(unittest.TestCase):
    def test_resume_only_fills_missing_parts(self) -> None:
        progress = PublishProgress(
            published_assets=frozenset({"matrix-1"}),
            published_citations=frozenset({"doi:10.1000/a"}),
            notified=frozenset({"lab-east"}),
        )
        plan = resume_plan(_package(), progress)
        self.assertEqual(("annotation-7", "summary-2"), plan.remaining_assets)
        self.assertEqual(("doi:10.1000/b",), plan.remaining_citations)
        self.assertEqual(("lab-west",), plan.remaining_notifications)
        self.assertFalse(plan.is_complete)

    def test_resume_after_full_publish_is_noop(self) -> None:
        package = _package()
        progress = PublishProgress(
            published_assets=frozenset(package.asset_ids),
            published_citations=frozenset(package.citations),
            notified=frozenset(package.notifications),
        )
        self.assertTrue(resume_plan(package, progress).is_complete)


class ReceiptTests(unittest.TestCase):
    def test_receipt_confirms_revision(self) -> None:
        package = _package()
        receipt = issue_receipt(package, "sha256:abc", "downloader-1")
        self.assertEqual(3, receipt.revision)
        self.assertEqual([], verify_receipt(receipt, package, "sha256:abc"))

    def test_receipt_detects_revision_and_digest_mismatch(self) -> None:
        package = _package()
        receipt = issue_receipt(package, "sha256:abc", "downloader-1")
        newer = ReleasePackage(
            package_id=package.package_id,
            revision=4,
            asset_ids=package.asset_ids,
            citations=package.citations,
            notifications=package.notifications,
            figures=package.figures,
        )
        issues = verify_receipt(receipt, newer, "sha256:xyz")
        codes = {issue.code for issue in issues}
        self.assertEqual({"mismatch"}, codes)
        self.assertIn("revision", {issue.field for issue in issues})
        self.assertIn("content_digest", {issue.field for issue in issues})


if __name__ == "__main__":
    unittest.main()
