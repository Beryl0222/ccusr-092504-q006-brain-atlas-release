"""图表审批、可恢复发布、修订与下载确认测试。"""

from __future__ import annotations

import unittest

from support import build_world, make_ready_sample, manifest_for

from brain_atlas_release.errors import PublicationError
from brain_atlas_release.publishing import Citation, PackageAsset


def _finish_run(world, run_id: str, manifest, *, shards: int = 1, count: int = 50) -> None:
    world.runs.start(run_id, manifest=manifest, expected_shards=shards)
    for index in range(shards):
        world.runs.complete_shard(
            run_id, shard_index=index, output_refs=[f"{run_id}/m/{index}"],
            manifest_fingerprint=manifest.fingerprint(), record_count=count,
        )
    world.runs.complete(run_id)


class FigureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = build_world()
        ids = make_ready_sample(self.world)
        self.ids = ids
        self.manifest = manifest_for(self.world, ids)
        _finish_run(self.world, "run-1", self.manifest)

    def test_figure_traceable_to_data_version_and_approval(self) -> None:
        w = self.world
        w.figures.approve(
            "fig-1", title="细胞类型图谱", source_run_ids=["run-1"],
            approver_id="pi-1", approval_ref="ap-2026-09-01", paper_ref="paper-1",
        )
        trace = w.figures.traceability("fig-1")
        self.assertEqual(trace["approval"]["approver_id"], "pi-1")
        lineage = trace["data_lineage"][0]
        self.assertEqual(lineage["manifest_fingerprint"], self.manifest.fingerprint())
        self.assertEqual(len(lineage["inputs"]), 3)
        self.assertEqual(lineage["software"][0]["version"], "2.4.1")

    def test_figure_rejects_uncompleted_or_superseded_run(self) -> None:
        w = self.world
        _finish_run(w, "run-2", manifest_for(w, self.ids))
        w.lineage.propagate_change("cell_qc_result", self.ids["qc_id"], reason="复核")
        with self.assertRaises(PublicationError) as ctx:
            w.figures.approve(
                "fig-x", title="X", source_run_ids=["run-2"],
                approver_id="pi-1", approval_ref="ap", paper_ref="p",
            )
        self.assertEqual(ctx.exception.code, "run_superseded")


class PackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = build_world()
        ids = make_ready_sample(self.world)
        self.ids = ids
        self.manifest = manifest_for(self.world, ids)
        _finish_run(self.world, "run-1", self.manifest, shards=4, count=25)
        self.world.figures.approve(
            "fig-1", title="图一", source_run_ids=["run-1"],
            approver_id="pi-1", approval_ref="ap-1", paper_ref="paper-1",
        )
        self.asset = PackageAsset("paper_figure", "fig-1", "f" * 64)
        self.citation = Citation("paper", "脑图谱联盟 2026 预印本 doi:10.0/atlas")

    def test_publish_and_downloader_confirms_revision(self) -> None:
        w = self.world
        w.packages.assemble("pkg-1", assets=[self.asset], citations=[self.citation])
        w.packages.publish("pkg-1")
        w.packages.notify("pkg-1", ["lab-a", "lab-b"])
        rev = w.packages.revision_manifest("pkg-1", 1)
        self.assertTrue(w.packages.verify_download("pkg-1", 1, rev["manifest_fingerprint"]))
        self.assertFalse(w.packages.verify_download("pkg-1", 1, "0" * 64))

    def test_interrupted_publication_only_fills_missing_pieces(self) -> None:
        w = self.world
        w.packages.assemble("pkg-1", assets=[self.asset], citations=[self.citation])
        # 发布成功、通知在 lab-b 处中断。
        w.packages.publish("pkg-1")
        events = w.packages.notify("pkg-1", ["lab-a"])
        self.assertEqual([e.payload["recipient"] for e in events], ["lab-a"])
        version_after = w.store.version("pkg-1")

        # 恢复：发布不重复登记，lab-a 不重复通知，只补 lab-b。
        recovered = w.packages.resume_publication(
            "pkg-1", recipients=["lab-a", "lab-b"]
        )
        self.assertEqual([e.event_type for e in recovered], ["PACKAGE_NOTIFICATION_SENT"])
        self.assertEqual([e.payload["recipient"] for e in recovered], ["lab-b"])
        snapshot = w.packages.get("pkg-1")
        self.assertEqual(snapshot.released_revisions, (1,))
        self.assertEqual(snapshot.notified, frozenset({"lab-a", "lab-b"}))
        self.assertEqual(w.store.version("pkg-1"), version_after + 1)

    def test_interrupted_before_release_resumes_release(self) -> None:
        w = self.world
        w.packages.assemble("pkg-1", assets=[self.asset], citations=[self.citation])
        produced = w.packages.resume_publication("pkg-1", recipients=["lab-a"])
        self.assertEqual(
            [e.event_type for e in produced],
            ["PACKAGE_RELEASED", "PACKAGE_NOTIFICATION_SENT"],
        )
        # 再次恢复为空操作。
        self.assertEqual(
            w.packages.resume_publication("pkg-1", recipients=["lab-a"]), []
        )

    def test_revisions_append_and_old_revision_remains(self) -> None:
        w = self.world
        w.packages.assemble("pkg-1", assets=[self.asset], citations=[self.citation])
        w.packages.publish("pkg-1")
        with self.assertRaises(PublicationError):
            w.packages.publish("pkg-1")
        w.packages.publish_revision("pkg-1", changelog="修订图一注释")
        snapshot = w.packages.get("pkg-1")
        self.assertEqual(snapshot.current_revision, 2)
        self.assertEqual(snapshot.released_revisions, (1, 2))
        rev1 = w.packages.revision_manifest("pkg-1", 1)
        rev2 = w.packages.revision_manifest("pkg-1", 2)
        self.assertEqual(rev1["supersedes_revision"], None)
        self.assertEqual(rev2["supersedes_revision"], 1)

    def test_publish_requires_approved_figure_and_citation(self) -> None:
        w = self.world
        ghost = PackageAsset("paper_figure", "fig-ghost", "c" * 64)
        w.packages.assemble("pkg-x", assets=[ghost], citations=[self.citation])
        with self.assertRaises(PublicationError) as ctx:
            w.packages.publish("pkg-x")
        self.assertEqual(ctx.exception.code, "asset_missing")

        with self.assertRaises(PublicationError):
            w.packages.assemble("pkg-y", assets=[self.asset], citations=[])


if __name__ == "__main__":
    unittest.main()
