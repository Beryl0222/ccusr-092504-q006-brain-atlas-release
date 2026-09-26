"""测试共用的领域装配与最小可用世界。"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from brain_atlas_release.access import AccessService
from brain_atlas_release.annotations import AnnotationAdjudicator
from brain_atlas_release.catalog import Purpose
from brain_atlas_release.cohorts import CohortRegistry
from brain_atlas_release.findings import FindingService
from brain_atlas_release.lineage import LineageService
from brain_atlas_release.processing import AnalysisRunService
from brain_atlas_release.provenance import InputRef, ProvenanceManifest, SoftwareComponent
from brain_atlas_release.publishing import FigureService, PackageService
from brain_atlas_release.records import (
    AnnotationRegistry,
    BatchRegistry,
    CellQcRegistry,
    DonorRegistry,
    SampleRegistry,
    SequencingFileRegistry,
)
from brain_atlas_release.store import EventStore

GOOD_CHECKSUM = "a" * 64
SOFTWARE = (SoftwareComponent("atlas-pipeline", "2.4.1"),)


def load_schema() -> dict:
    return json.loads((ROOT / "contracts" / "domain.schema.json").read_text(encoding="utf-8"))


@dataclass
class World:
    store: EventStore
    donors: DonorRegistry
    samples: SampleRegistry
    batches: BatchRegistry
    files: SequencingFileRegistry
    qcs: CellQcRegistry
    annotations: AnnotationRegistry
    cohorts: CohortRegistry
    lineage: LineageService
    runs: AnalysisRunService
    adjudications: AnnotationAdjudicator
    findings: FindingService
    figures: FigureService
    packages: PackageService
    access: AccessService

    def current_versions(self, refs: list[tuple[str, str]]) -> dict[tuple[str, str], int]:
        return {(kind, ref_id): self.store.version(ref_id) for kind, ref_id in refs}


def build_world(*, validate_contracts: bool = False) -> World:
    store = EventStore(load_schema() if validate_contracts else None)
    donors = DonorRegistry(store)
    samples = SampleRegistry(store, donors)
    batches = BatchRegistry(store)
    files = SequencingFileRegistry(store)
    qcs = CellQcRegistry(store)
    annotations = AnnotationRegistry(store)
    cohorts = CohortRegistry(
        store, donors=donors, samples=samples, batches=batches, files=files, qcs=qcs
    )
    lineage = LineageService(store)
    runs = AnalysisRunService(store, lineage=lineage)
    adjudications = AnnotationAdjudicator(store, annotations)
    findings = FindingService(store)
    figures = FigureService(store, runs)
    packages = PackageService(store, figures=figures)
    access = AccessService(cohorts=cohorts, donors=donors, samples=samples)
    return World(
        store=store,
        donors=donors,
        samples=samples,
        batches=batches,
        files=files,
        qcs=qcs,
        annotations=annotations,
        cohorts=cohorts,
        lineage=lineage,
        runs=runs,
        adjudications=adjudications,
        findings=findings,
        figures=figures,
        packages=packages,
        access=access,
    )


def make_ready_sample(
    world: World,
    *,
    donor_id: str = "donor-1",
    sample_id: str = "sample-1",
    batch_id: str = "batch-1",
    file_id: str = "file-1",
    qc_id: str = "qc-1",
    purposes=(Purpose.RESEARCH, Purpose.DISEASE_RESEARCH),
    threshold_version: str = "qc-v3",
) -> dict:
    """登记一条走到“测序校验通过+质控通过+批次复核通过”的样本链。"""
    if world.donors.get(donor_id) is None:
        world.donors.register(donor_id, allowed_purposes=purposes)
    world.samples.derive(
        sample_id, donor_id=donor_id, deidentification_ref=f"deid-map/{donor_id}"
    )
    world.batches.prepare(batch_id, sample_ids=[sample_id], protocol_version="prep-v7")
    world.batches.review_quality(
        batch_id, accepted=True, reason="复核通过", reviewer_id="reviewer-1"
    )
    world.files.register(
        file_id,
        batch_id=batch_id,
        sample_id=sample_id,
        checksum_value=GOOD_CHECKSUM,
    )
    world.files.verify(file_id, observed_checksum=GOOD_CHECKSUM)
    world.qcs.record(
        qc_id,
        sample_id=sample_id,
        batch_id=batch_id,
        threshold_version=threshold_version,
        passed=True,
        metrics={"min_genes": 200, "mito_fraction": 0.04},
    )
    return {
        "donor_id": donor_id,
        "sample_id": sample_id,
        "batch_id": batch_id,
        "file_id": file_id,
        "qc_id": qc_id,
    }


def manifest_for(world: World, ids: dict, *, parameters: dict | None = None) -> ProvenanceManifest:
    return ProvenanceManifest(
        inputs=(
            InputRef("deidentified_sample", ids["sample_id"], world.store.version(ids["sample_id"])),
            InputRef("cell_qc_result", ids["qc_id"], world.store.version(ids["qc_id"])),
            InputRef("sequencing_file", ids["file_id"],
                     world.store.version(ids["file_id"]), GOOD_CHECKSUM),
        ),
        parameters=parameters or {"resolution": 1.0, "min_cells": 30},
        software=SOFTWARE,
    )
