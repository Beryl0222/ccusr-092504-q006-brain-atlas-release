"""供体、样本、批次、测序、质控登记测试。"""

from __future__ import annotations

import unittest

from support import GOOD_CHECKSUM, build_world, make_ready_sample

from brain_atlas_release.catalog import Purpose
from brain_atlas_release.errors import DomainError, ImmutabilityError, LineageError


class RecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = build_world()

    def test_consent_scope_and_withdrawal_are_append_only(self) -> None:
        w = self.world
        w.donors.register("donor-1", allowed_purposes=[Purpose.RESEARCH])
        w.donors.update_scope(
            "donor-1", allowed_purposes=[Purpose.RESEARCH, Purpose.PUBLIC_DOWNLOAD],
            reason="补充公开授权",
        )
        snapshot = w.donors.get("donor-1")
        self.assertEqual(
            snapshot.allowed_purposes, {Purpose.RESEARCH, Purpose.PUBLIC_DOWNLOAD}
        )
        w.donors.withdraw("donor-1", reason="本人申请退出")
        self.assertTrue(w.donors.get("donor-1").withdrawn)

        # 撤回事实不能重复登记，授权范围也不能在撤回后改写。
        with self.assertRaises(LineageError):
            w.donors.withdraw("donor-1", reason="再次撤回")
        with self.assertRaises(LineageError):
            w.donors.update_scope("donor-1", allowed_purposes=[Purpose.RESEARCH], reason="试图恢复")

        # 历史事件原样保留，版本单调到 3。
        self.assertEqual(w.store.version("donor-1"), 3)
        with self.assertRaises(ImmutabilityError):
            w.donors.register("donor-1", allowed_purposes=[Purpose.RESEARCH])

    def test_no_new_sample_after_withdrawal(self) -> None:
        w = self.world
        w.donors.register("donor-1", allowed_purposes=[Purpose.RESEARCH])
        w.samples.derive("s1", donor_id="donor-1", deidentification_ref="map/1")
        w.donors.withdraw("donor-1", reason="退出")
        with self.assertRaises(LineageError):
            w.samples.derive("s2", donor_id="donor-1", deidentification_ref="map/2")

    def test_batch_review_history_is_retained(self) -> None:
        ids = make_ready_sample(self.world)
        w = self.world
        # 已通过后又打回、再复核通过：事实全部保留，快照取最新结论。
        w.batches.review_quality(ids["batch_id"], accepted=False, reason="污染", reviewer_id="r1")
        self.assertEqual(w.batches.get(ids["batch_id"]).status, "rejected")
        w.batches.review_quality(ids["batch_id"], accepted=True, reason="复检无污染", reviewer_id="r2")
        self.assertEqual(w.batches.get(ids["batch_id"]).status, "accepted")
        reviews = [
            e for e in w.store.stream(ids["batch_id"])
            if e.event_type == "BATCH_QUALITY_REVIEWED"
        ]
        self.assertEqual([True, False, True], [e.payload["accepted"] for e in reviews])

    def test_sequencing_checksum_failure_is_recorded_then_verified(self) -> None:
        ids = make_ready_sample(self.world)
        w = self.world
        with self.assertRaises(DomainError):
            w.files.register("bad", batch_id="x", sample_id="y", checksum_value="deadbeef")
        # 新文件：先登记失败事实，再校验通过——失败事实仍保留在流中。
        w.files.register(
            "file-2", batch_id=ids["batch_id"], sample_id=ids["sample_id"],
            checksum_value=GOOD_CHECKSUM,
        )
        w.files.verify("file-2", observed_checksum="b" * 64)
        self.assertFalse(w.files.get("file-2").verified)
        w.files.verify("file-2", observed_checksum=GOOD_CHECKSUM)
        self.assertTrue(w.files.get("file-2").verified)
        kinds = [e.event_type for e in w.store.stream("file-2")]
        self.assertIn("SEQUENCING_CHECKSUM_FAILED", kinds)

    def test_qc_becomes_stale_after_threshold_change_until_rerun(self) -> None:
        ids = make_ready_sample(self.world)
        w = self.world
        self.assertFalse(w.qcs.get(ids["qc_id"]).stale)
        w.qcs.change_threshold(ids["qc_id"], new_threshold_version="qc-v4", reason="提高线粒体阈值")
        stale = w.qcs.get(ids["qc_id"])
        self.assertTrue(stale.stale)
        self.assertEqual(stale.threshold_version, "qc-v4")


if __name__ == "__main__":
    unittest.main()
