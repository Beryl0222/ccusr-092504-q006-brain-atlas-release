from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from brain_atlas_release.lineage import (
    AssetManifest,
    DerivedAsset,
    InputPin,
    ShardResult,
    ShardedRun,
    SoftwarePin,
    completion_count,
    create_successor,
    ensure_deletable,
    is_complete,
    record_shard,
    validate_asset,
    validate_manifest,
    withdraw_asset,
)


def _manifest() -> AssetManifest:
    return AssetManifest(
        inputs=(InputPin("cell_qc_result-1", 2),),
        parameters={"min_genes": 500, "annotation_version": "v7"},
        software=(SoftwarePin("scanpy", "1.10.2"),),
    )


class ManifestTests(unittest.TestCase):
    def test_manifest_requires_inputs_and_software(self) -> None:
        manifest = AssetManifest(inputs=(), parameters={}, software=())
        codes = {issue.code for issue in validate_manifest(manifest)}
        self.assertIn("required", codes)

    def test_manifest_parameters_are_frozen(self) -> None:
        manifest = _manifest()
        with self.assertRaises(TypeError):
            manifest.parameters["min_genes"] = 100  # type: ignore[index]

    def test_valid_asset_passes(self) -> None:
        asset = DerivedAsset(asset_id="analysis_run-9", kind="analysis_run", manifest=_manifest())
        self.assertEqual([], validate_asset(asset))


class WithdrawalTests(unittest.TestCase):
    def test_withdraw_requires_reason(self) -> None:
        asset = DerivedAsset(asset_id="a-1", kind="cell_annotation", manifest=_manifest())
        updated, issues = withdraw_asset(asset, "  ")
        self.assertIsNone(updated)
        self.assertEqual(["required"], [issue.code for issue in issues])

    def test_withdrawal_keeps_fact_and_successor_increments_revision(self) -> None:
        asset = DerivedAsset(asset_id="a-2", kind="cell_annotation", manifest=_manifest())
        withdrawn, issues = withdraw_asset(asset, "质控复核未通过")
        self.assertEqual([], issues)
        self.assertEqual("withdrawn", withdrawn.status)
        previous, successor = create_successor(withdrawn, _manifest())
        self.assertEqual(asset.revision + 1, successor.revision)
        self.assertEqual(asset.asset_id, successor.supersedes)

    def test_active_asset_is_superseded_by_successor(self) -> None:
        asset = DerivedAsset(asset_id="a-3", kind="analysis_run", manifest=_manifest())
        previous, successor = create_successor(asset, _manifest())
        self.assertEqual("superseded", previous.status)
        self.assertEqual("active", successor.status)

    def test_published_fact_cannot_be_deleted(self) -> None:
        asset = DerivedAsset(
            asset_id="a-4",
            kind="paper_figure",
            manifest=_manifest(),
            used_in_formal_paper=True,
        )
        codes = [issue.code for issue in ensure_deletable(asset)]
        self.assertEqual(["published_fact_retained"], codes)


class ShardedRunTests(unittest.TestCase):
    def _run(self) -> ShardedRun:
        return ShardedRun.start("run-1", _manifest(), ("s1", "s2"))

    def test_duplicate_shard_is_not_counted_twice(self) -> None:
        run = self._run()
        run, issues = record_shard(run, ShardResult("s1", attempt=1, output_digest="d1"))
        self.assertEqual([], issues)
        run, issues = record_shard(run, ShardResult("s1", attempt=2, output_digest="d1"))
        self.assertEqual([], issues)
        self.assertEqual(1, completion_count(run))
        self.assertFalse(is_complete(run))

    def test_conflicting_result_is_rejected(self) -> None:
        run = self._run()
        run, _ = record_shard(run, ShardResult("s1", attempt=1, output_digest="d1"))
        run, issues = record_shard(run, ShardResult("s1", attempt=2, output_digest="d2"))
        self.assertEqual(["conflicting_result"], [issue.code for issue in issues])
        self.assertEqual(1, completion_count(run))

    def test_unknown_shard_is_rejected(self) -> None:
        run = self._run()
        run, issues = record_shard(run, ShardResult("s9", attempt=1, output_digest="d1"))
        self.assertEqual(["unknown_shard"], [issue.code for issue in issues])

    def test_run_completes_when_all_shards_recorded(self) -> None:
        run = self._run()
        run, _ = record_shard(run, ShardResult("s1", attempt=1, output_digest="d1"))
        run, _ = record_shard(run, ShardResult("s2", attempt=1, output_digest="d2"))
        self.assertTrue(is_complete(run))
        self.assertEqual(2, completion_count(run))


if __name__ == "__main__":
    unittest.main()
