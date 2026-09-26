"""溯源清单、撤回传播与后继版本测试。"""

from __future__ import annotations

import unittest

from support import build_world, make_ready_sample, manifest_for

from brain_atlas_release.catalog import Purpose
from brain_atlas_release.errors import LineageError, ProvenanceError
from brain_atlas_release.provenance import InputRef, ProvenanceManifest, SoftwareComponent
from brain_atlas_release.provenance import verify_inputs_current


class ProvenanceTests(unittest.TestCase):
    def test_manifest_requires_inputs_and_software(self) -> None:
        with self.assertRaises(ValueError):
            ProvenanceManifest(inputs=(), parameters={}, software=SOFTWARE)
        with self.assertRaises(ValueError):
            ProvenanceManifest(
                inputs=(InputRef("deidentified_sample", "s1", 1),),
                parameters={},
                software=(),
            )

    def test_fingerprint_is_stable_and_strict(self) -> None:
        inputs = (InputRef("deidentified_sample", "s1", 1),)
        m1 = ProvenanceManifest(
            inputs=inputs, parameters={"b": 2, "a": 1}, software=SOFTWARE
        )
        m2 = ProvenanceManifest(
            inputs=inputs, parameters={"a": 1, "b": 2}, software=SOFTWARE
        )
        self.assertEqual(m1.fingerprint(), m2.fingerprint())
        m3 = ProvenanceManifest(
            inputs=(InputRef("deidentified_sample", "s1", 2),),
            parameters={"a": 1, "b": 2},
            software=SOFTWARE,
        )
        self.assertNotEqual(m1.fingerprint(), m3.fingerprint())

    def test_duplicate_inputs_and_software_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ProvenanceManifest(
                inputs=(
                    InputRef("deidentified_sample", "s1", 1),
                    InputRef("deidentified_sample", "s1", 1),
                ),
                parameters={},
                software=SOFTWARE,
            )
        with self.assertRaises(ValueError):
            ProvenanceManifest(
                inputs=(InputRef("deidentified_sample", "s1", 1),),
                parameters={},
                software=(
                    SoftwareComponent("atlas-pipeline", "2.4.1"),
                    SoftwareComponent("atlas-pipeline", "2.4.1"),
                ),
            )

    def test_stale_input_detection(self) -> None:
        ref = InputRef("cell_qc_result", "qc-1", 1)
        manifest = ProvenanceManifest(
            inputs=(ref,), parameters={}, software=SOFTWARE
        )
        self.assertEqual(
            [ref],
            verify_inputs_current(manifest, {("cell_qc_result", "qc-1"): 2}),
        )
        self.assertEqual(
            [],
            verify_inputs_current(manifest, {("cell_qc_result", "qc-1"): 1}),
        )


SOFTWARE = (SoftwareComponent("atlas-pipeline", "2.4.1"),)


class LineageTests(unittest.TestCase):
    def test_qc_revision_supersedes_run_and_successor_needs_newer_inputs(self) -> None:
        world = build_world()
        ids = make_ready_sample(world)
        manifest = manifest_for(world, ids)
        world.runs.start("run-1", manifest=manifest, expected_shards=1)
        world.runs.complete_shard(
            "run-1", shard_index=0, output_refs=["matrix/run-1/part-0"],
            manifest_fingerprint=manifest.fingerprint(), record_count=100,
        )
        world.runs.complete("run-1")

        # 阈值调整 + 复算：质控聚合出现新版本。
        world.qcs.change_threshold(ids["qc_id"], new_threshold_version="qc-v4", reason="收紧")
        report = world.lineage.propagate_change(
            "cell_qc_result", ids["qc_id"], reason="阈值收紧"
        )
        self.assertEqual(report.impacted_runs, ("run-1",))
        self.assertTrue(world.runs.get("run-1").superseded)

        # 后继运行必须引用更新的输入版本；沿用旧清单被拒绝。
        with self.assertRaises(LineageError):
            world.runs.start("run-2", manifest=manifest, expected_shards=1,
                             supersedes_run="run-1")

        new_manifest = manifest_for(world, ids)  # 重新读取当前版本号
        self.assertNotEqual(new_manifest.fingerprint(), manifest.fingerprint())
        world.runs.start("run-2", manifest=new_manifest, expected_shards=1,
                         supersedes_run="run-1")
        self.assertEqual(world.runs.get("run-2").supersedes_run, "run-1")

    def test_paper_locked_run_is_never_deleted_or_superseded(self) -> None:
        world = build_world()
        ids = make_ready_sample(world)
        manifest = manifest_for(world, ids)
        world.runs.start("run-1", manifest=manifest, expected_shards=1)
        world.runs.complete_shard(
            "run-1", shard_index=0, output_refs=["m/0"],
            manifest_fingerprint=manifest.fingerprint(), record_count=10,
        )
        world.runs.complete("run-1")
        world.figures.approve(
            "fig-1", title="图一", source_run_ids=["run-1"],
            approver_id="boss", approval_ref="ap-1", paper_ref="paper-1",
        )
        self.assertTrue(world.lineage.is_paper_locked("run-1"))
        report = world.lineage.propagate_change(
            "cell_qc_result", ids["qc_id"], reason="复核"
        )
        self.assertEqual(report.impacted_runs, ())
        self.assertEqual(report.paper_locked_runs, ("run-1",))
        self.assertFalse(world.runs.get("run-1").superseded)
        # 原运行事实仍在。
        self.assertIsNotNone(world.runs.get("run-1"))


if __name__ == "__main__":
    unittest.main()
