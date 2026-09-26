"""分析运行分片幂等测试。"""

from __future__ import annotations

import unittest

from support import build_world, make_ready_sample, manifest_for

from brain_atlas_release.errors import DomainError, DuplicateIngestionError


class ShardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = build_world()
        ids = make_ready_sample(self.world)
        self.ids = ids
        self.manifest = manifest_for(self.world, ids)
        self.world.runs.start("run-1", manifest=self.manifest, expected_shards=3)

    def _complete(self, index: int, count: int = 10):
        return self.world.runs.complete_shard(
            "run-1", shard_index=index, output_refs=[f"m/{index}"],
            manifest_fingerprint=self.manifest.fingerprint(), record_count=count,
        )

    def test_shard_retry_is_idempotent_and_not_double_counted(self) -> None:
        w = self.world
        first = self._complete(0, count=10)
        retried = self._complete(0, count=10)
        self.assertEqual(first.event_id, retried.event_id)
        self.assertEqual(w.runs.total_record_count("run-1"), 10)

        # 同分片以不同内容重放必须报错，绝不被第二次计入。
        with self.assertRaises(DuplicateIngestionError):
            w.runs.complete_shard(
                "run-1", shard_index=0, output_refs=["m/0"],
                manifest_fingerprint=self.manifest.fingerprint(), record_count=999,
            )
        self.assertEqual(w.runs.total_record_count("run-1"), 10)

    def test_shard_index_range_checked(self) -> None:
        with self.assertRaises(DomainError):
            self._complete(3)

    def test_fingerprint_mismatch_forces_new_run(self) -> None:
        other = manifest_for(self.world, self.ids, parameters={"resolution": 2.0})
        with self.assertRaises(DomainError) as ctx:
            self.world.runs.complete_shard(
                "run-1", shard_index=0, output_refs=["x"],
                manifest_fingerprint=other.fingerprint(), record_count=1,
            )
        self.assertEqual(ctx.exception.code, "fingerprint_mismatch")

    def test_run_completes_only_when_all_shards_succeed(self) -> None:
        w = self.world
        self._complete(0)
        self._complete(1)
        with self.assertRaises(DomainError) as ctx:
            w.runs.complete("run-1")
        self.assertEqual(ctx.exception.code, "shards_incomplete")

        w.runs.fail_shard("run-1", shard_index=2, reason="节点重启")
        with self.assertRaises(DomainError) as ctx:
            w.runs.complete("run-1")
        self.assertEqual(ctx.exception.code, "shards_failed")

        # 失败分片重试成功后完成；完成事件幂等。
        self._complete(2)
        completed = w.runs.complete("run-1")
        self.assertEqual(completed.event_type, "ANALYSIS_COMPLETED")
        self.assertEqual(w.runs.total_record_count("run-1"), 30)
        self.assertIs(w.runs.complete("run-1"), completed)
        self.assertEqual(w.runs.get("run-1").status, "completed")


if __name__ == "__main__":
    unittest.main()
