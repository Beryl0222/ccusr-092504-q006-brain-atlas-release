"""脑图谱研究资产发布台领域服务。

模块分层：

* ``contracts`` / ``catalog``：交换契约与词汇登记；
* ``store``：仅追加事件存储；
* ``records``：供体授权、样本、批次、测序、质控、注释登记；
* ``cohorts``：队列纳排与访问条件登记；
* ``provenance`` / ``lineage``：派生清单、撤回传播与后继版本；
* ``access``：用途与角色最小授权；
* ``annotations``：并行标注冲突裁定；
* ``findings``：研究发现证据边界与临床只读视图；
* ``processing``：分析运行分片幂等；
* ``publishing``：图表审批、可恢复发布、修订与下载校验。
"""

from .access import AccessRequest, AccessService, Grant
from .annotations import AnnotationAdjudicator
from .catalog import AggregateType, EventType, Purpose
from .cohorts import CohortRegistry
from .contracts import ContractIssue, validate_event
from .errors import (
    AccessDeniedError,
    AnnotationConflictError,
    ConcurrencyError,
    DomainError,
    DuplicateIngestionError,
    EvidenceBoundaryError,
    ImmutabilityError,
    LineageError,
    ProvenanceError,
    PublicationError,
)
from .findings import CLINICAL_BOUNDARY_NOTICE, FindingService
from .lineage import ImpactReport, LineageService
from .processing import AnalysisRunService
from .publishing import Citation, FigureService, PackageAsset, PackageService
from .provenance import InputRef, ProvenanceManifest, SoftwareComponent
from .records import (
    AnnotationRegistry,
    BatchRegistry,
    CellQcRegistry,
    DonorRegistry,
    SampleRegistry,
    SequencingFileRegistry,
)
from .store import Event, EventStore

__all__ = [
    # 契约
    "AggregateType",
    "EventType",
    "Purpose",
    "ContractIssue",
    "validate_event",
    # 资产登记
    "DonorRegistry",
    "SampleRegistry",
    "BatchRegistry",
    "SequencingFileRegistry",
    "CellQcRegistry",
    "AnnotationRegistry",
    "CohortRegistry",
    # 事件流
    "Event",
    "EventStore",
    "ConcurrencyError",
    "ImmutabilityError",
    # 溯源
    "InputRef",
    "ProvenanceManifest",
    "SoftwareComponent",
    "LineageService",
    "ImpactReport",
    # 授权
    "AccessRequest",
    "AccessService",
    "Grant",
    # 注释
    "AnnotationAdjudicator",
    # 发现
    "FindingService",
    "CLINICAL_BOUNDARY_NOTICE",
    # 运行
    "AnalysisRunService",
    # 发布
    "FigureService",
    "PackageService",
    "PackageAsset",
    "Citation",
    # 违例
    "DomainError",
    "LineageError",
    "ProvenanceError",
    "AccessDeniedError",
    "DuplicateIngestionError",
    "PublicationError",
    "AnnotationConflictError",
    "EvidenceBoundaryError",
]
