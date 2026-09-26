"""端到端：同一供体进入生命阶段/疾病/基因性状三类成果，经历撤回、
复算、注释裁定，最终发布数据包并可逐图回溯。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from support import GOOD_CHECKSUM, build_world, make_ready_sample, manifest_for

from brain_atlas_release.access import AccessRequest
from brain_atlas_release.catalog import Purpose
from brain_atlas_release.errors import AccessDeniedError
from brain_atlas_release.publishing import Citation, PackageAsset

ROOT = Path(__file__).resolve().parents[1]


class EndToEndTests(unittest.TestCase):
    def test_three_output_lines_share_donor_and_release_with_traceability(self) -> None:
        world = build_world(validate_contracts=True)

        # 1) 一个供体，两个样本分别进入疾病队列与生命阶段/基因性状队列。
        ids_a = make_ready_sample(
            world, donor_id="donor-1", sample_id="sample-a",
            batch_id="batch-a", file_id="file-a", qc_id="qc-a",
            purposes=[Purpose.RESEARCH, Purpose.DISEASE_RESEARCH,
                      Purpose.ANCESTRY_RESEARCH, Purpose.PUBLIC_DOWNLOAD],
        )
        ids_b = make_ready_sample(
            world, donor_id="donor-1", sample_id="sample-b",
            batch_id="batch-b", file_id="file-b", qc_id="qc-b",
            purposes=[Purpose.RESEARCH, Purpose.DISEASE_RESEARCH,
                      Purpose.ANCESTRY_RESEARCH, Purpose.PUBLIC_DOWNLOAD],
        )

        world.cohorts.define(
            "cohort-ad", study_type="disease",
            restriction={"disease": "alzheimer"},
            access_conditions={Purpose.DISEASE_RESEARCH: ["researcher"]},
        )
        world.cohorts.define(
            "cohort-lifespan", study_type="life_stage",
            restriction={"life_stage_window": "adult"},
            access_conditions={Purpose.RESEARCH: ["researcher"]},
        )
        world.cohorts.define(
            "cohort-trait", study_type="gene_trait",
            restriction={"trait_domain": "microglia"},
            access_conditions={Purpose.RESEARCH: ["researcher"]},
        )
        world.cohorts.include("cohort-ad", sample_id="sample-a",
                              qc_id="qc-a", file_id="file-a")
        world.cohorts.include("cohort-lifespan", sample_id="sample-b",
                              qc_id="qc-b", file_id="file-b")
        world.cohorts.include("cohort-trait", sample_id="sample-b",
                              qc_id="qc-b", file_id="file-b")
        self.assertEqual(
            world.cohorts.get("cohort-lifespan").members
            & world.cohorts.get("cohort-trait").members,
            frozenset({"sample-b"}),
        )

        # 2) 注释冲突由有资质人员裁定。
        world.annotations.submit("ann-7", target_ref="cluster-7",
                                 annotation_version="v1", annotator_id="a",
                                 label="小胶质细胞")
        world.annotations.submit("ann-7", target_ref="cluster-7",
                                 annotation_version="v2", annotator_id="b",
                                 label="巨噬细胞")
        world.adjudications.raise_conflict("ann-7", competing_versions=["v1", "v2"],
                                           raised_by="a")
        world.adjudications.adjudicate(
            "ann-7", chosen_version="v1", adjudicator_id="dr-chen",
            adjudicator_roles={"board_certified_neuropathologist"},
            rationale="脑内标记基因组合支持小胶质细胞",
        )

        # 3) 三条成果线各自启动分析运行。
        manifests = {
            "run-ad": manifest_for(world, ids_a, parameters={"line": "disease"}),
            "run-life": manifest_for(world, ids_b, parameters={"line": "life_stage"}),
            "run-trait": manifest_for(world, ids_b, parameters={"line": "gene_trait"}),
        }
        for run_id, manifest in manifests.items():
            world.runs.start(run_id, manifest=manifest, expected_shards=2)
            for shard in range(2):
                world.runs.complete_shard(
                    run_id, shard_index=shard, output_refs=[f"{run_id}/{shard}"],
                    manifest_fingerprint=manifest.fingerprint(), record_count=40,
                )
            world.runs.complete(run_id)

        # 4) 论文定稿前质控阈值调整：旧运行废止并生成后继版本；
        #    已用于图表的运行锁定不删，只提示需要更正。
        world.figures.approve("fig-disease", title="疾病差异",
                              source_run_ids=["run-ad"], approver_id="pi",
                              approval_ref="ap-1", paper_ref="paper-1")
        world.qcs.change_threshold("qc-a", new_threshold_version="qc-v4",
                                   reason="线粒体阈值收紧")
        report = world.lineage.propagate_change("cell_qc_result", "qc-a",
                                                reason="阈值收紧")
        self.assertEqual(report.paper_locked_runs, ("run-ad",))
        self.assertFalse(world.runs.get("run-ad").superseded)

        new_manifest = manifest_for(world, ids_a, parameters={"line": "disease"})
        world.runs.start("run-ad-r2", manifest=new_manifest, expected_shards=2,
                         supersedes_run="run-ad")
        for shard in range(2):
            world.runs.complete_shard(
                "run-ad-r2", shard_index=shard, output_refs=[f"run-ad-r2/{shard}"],
                manifest_fingerprint=new_manifest.fingerprint(), record_count=39,
            )
        world.runs.complete("run-ad-r2")
        world.figures.approve("fig-disease-r2", title="疾病差异（复算）",
                              source_run_ids=["run-ad-r2"], approver_id="pi",
                              approval_ref="ap-2", paper_ref="paper-1",
                              supersedes_figure="fig-disease")

        # 5) 研究发现带证据范围；临床接口只拿带边界声明的聚合视图。
        world.findings.record(
            "finding-ad",
            statement="APOE ε4 与小胶质细胞活化比例升高相关",
            cohort_id="cohort-ad", source_run_ids=["run-ad-r2"],
            evidence_scope={"cohort_id": "cohort-ad", "sample_count": 39,
                            "run_ids": ["run-ad-r2"]},
            limitations=["祖源覆盖有限", "为观察性关联"],
        )
        view = world.findings.clinical_view("finding-ad")
        self.assertIsNone(view["individual_level_data"])
        self.assertIn("不构成针对任何个体的诊断", view["boundary_notice"])

        # 6) 最小授权：疾病研究者不能取生命阶段队列，临床用途不能直接取数。
        world.access.grant("alice", cohort_id="cohort-ad",
                           purpose=Purpose.DISEASE_RESEARCH, roles=["researcher"])
        world.access.authorize(AccessRequest(
            requester_id="alice", roles=frozenset({"researcher"}),
            purpose=Purpose.DISEASE_RESEARCH, cohort_id="cohort-ad",
            study_context={"disease": "alzheimer"},
        ))
        with self.assertRaises(AccessDeniedError):
            world.access.authorize(AccessRequest(
                requester_id="alice", roles=frozenset({"researcher"}),
                purpose=Purpose.RESEARCH, cohort_id="cohort-lifespan",
                study_context={"life_stage_window": "adult"},
            ))
        with self.assertRaises(AccessDeniedError):
            world.access.authorize(AccessRequest(
                requester_id="alice", roles=frozenset({"researcher"}),
                purpose=Purpose.CLINICAL, cohort_id="cohort-ad",
            ))

        # 7) 组装、发布、通知；中断恢复不重复；下载者确认修订。
        figures = ["fig-disease-r2", "fig-lifespan", "fig-trait"]
        world.figures.approve("fig-lifespan", title="生命阶段轨迹",
                              source_run_ids=["run-life"], approver_id="pi",
                              approval_ref="ap-3", paper_ref="paper-1")
        world.figures.approve("fig-trait", title="基因性状关联",
                              source_run_ids=["run-trait"], approver_id="pi",
                              approval_ref="ap-4", paper_ref="paper-1")
        assets = [PackageAsset("paper_figure", fig, f"checksum-{fig}") for fig in figures]
        citations = [Citation("paper", "脑图谱联盟 2026 doi:10.0/atlas")]
        world.packages.assemble("pkg-2026", assets=assets, citations=citations)
        world.packages.resume_publication(
            "pkg-2026", recipients=["lab-a", "lab-b", "lab-c"]
        )
        # 中断后再次恢复：无新增事件。
        self.assertEqual(
            world.packages.resume_publication(
                "pkg-2026", recipients=["lab-a", "lab-b", "lab-c"]
            ),
            [],
        )

        # 8) 每张图都能回溯到具体数据版本与审批；下载者核对修订指纹。
        for figure_id in figures:
            trace = world.figures.traceability(figure_id)
            self.assertTrue(trace["approval"]["approval_ref"].startswith("ap-"))
            self.assertEqual(len(trace["data_lineage"]), 1)
            self.assertTrue(trace["data_lineage"][0]["manifest_fingerprint"])
        rev = world.packages.revision_manifest("pkg-2026", 1)
        self.assertTrue(
            world.packages.verify_download("pkg-2026", 1, rev["manifest_fingerprint"])
        )

        # 9) 旧图表/旧运行事实仍可查（不能删除已用于正式论文的事实）。
        self.assertIsNotNone(world.figures.get("fig-disease"))
        self.assertEqual(world.runs.get("run-ad").status, "completed")

    def test_every_catalog_value_is_registered_in_schema(self) -> None:
        schema = json.loads((ROOT / "contracts" / "domain.schema.json").read_text("utf-8"))
        from brain_atlas_release.catalog import EVENT_AGGREGATE, AggregateType, EventType

        self.assertEqual(set(EventType), set(schema["properties"]["event_type"]["enum"]))
        self.assertEqual(set(AggregateType), set(schema["properties"]["aggregate_type"]["enum"]))
        # 每类事件都登记了归属聚合。
        self.assertEqual(set(EVENT_AGGREGATE), set(EventType))


if __name__ == "__main__":
    unittest.main()
