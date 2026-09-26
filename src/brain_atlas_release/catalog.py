"""领域对象与事件类型登记。

登记保持显式枚举，新增事件类型必须同时在 ``contracts/domain.schema.json``
登记，交换层与领域层共享同一份词汇表。
"""

from __future__ import annotations

from enum import StrEnum


class AggregateType(StrEnum):
    """发布台管理的聚合类型。"""

    DONOR = "donor_record"                 # 供体授权记录
    SAMPLE = "deidentified_sample"         # 去标识化样本
    BATCH = "biospecimen_batch"            # 制备批次
    SEQUENCING_FILE = "sequencing_file"    # 测序文件
    CELL_QC = "cell_qc_result"             # 细胞质控结果
    ANNOTATION = "cell_annotation"         # 细胞注释（含版本）
    COHORT = "cohort"                      # 队列纳排标准与成员
    RUN = "analysis_run"                   # 分析运行
    FINDING = "research_finding"           # 研究发现（论文结论）
    FIGURE = "paper_figure"                # 论文图表
    PACKAGE = "release_package"            # 公开数据包


class EventType(StrEnum):
    """领域事件类型。

    撤回/质量复核类事件不会删除旧事实，而是登记否定事实并触发后继版本，
    因此同一聚合上始终只有追加（append-only）。
    """

    # 供体授权
    CONSENT_REGISTERED = "CONSENT_REGISTERED"
    CONSENT_SCOPE_UPDATED = "CONSENT_SCOPE_UPDATED"
    CONSENT_WITHDRAWN = "CONSENT_WITHDRAWN"
    # 样本与批次
    SAMPLE_DERIVED = "SAMPLE_DERIVED"
    BATCH_PREPARED = "BATCH_PREPARED"
    BATCH_QUALITY_REVIEWED = "BATCH_QUALITY_REVIEWED"
    # 测序
    SEQUENCING_FILE_REGISTERED = "SEQUENCING_FILE_REGISTERED"
    SEQUENCING_CHECKSUM_VERIFIED = "SEQUENCING_CHECKSUM_VERIFIED"
    SEQUENCING_CHECKSUM_FAILED = "SEQUENCING_CHECKSUM_FAILED"
    # 质控与注释
    CELL_QC_RECORDED = "CELL_QC_RECORDED"
    QC_THRESHOLD_CHANGED = "QC_THRESHOLD_CHANGED"
    ANNOTATION_SUBMITTED = "ANNOTATION_SUBMITTED"
    ANNOTATION_CONFLICT_RAISED = "ANNOTATION_CONFLICT_RAISED"
    ANNOTATION_APPROVED = "ANNOTATION_APPROVED"
    # 队列
    COHORT_DEFINED = "COHORT_DEFINED"
    SAMPLE_INCLUDED = "SAMPLE_INCLUDED"
    SAMPLE_EXCLUDED = "SAMPLE_EXCLUDED"
    # 分析运行
    ANALYSIS_STARTED = "ANALYSIS_STARTED"
    ANALYSIS_SHARD_COMPLETED = "ANALYSIS_SHARD_COMPLETED"
    ANALYSIS_COMPLETED = "ANALYSIS_COMPLETED"
    ANALYSIS_SUPERSEDED = "ANALYSIS_SUPERSEDED"
    # 研究发现
    FINDING_RECORDED = "FINDING_RECORDED"
    FINDING_CORRECTED = "FINDING_CORRECTED"
    # 图表与数据包
    FIGURE_APPROVED = "FIGURE_APPROVED"
    PACKAGE_ASSEMBLED = "PACKAGE_ASSEMBLED"
    PACKAGE_RELEASED = "PACKAGE_RELEASED"
    PACKAGE_REVISION_PUBLISHED = "PACKAGE_REVISION_PUBLISHED"
    PACKAGE_NOTIFICATION_SENT = "PACKAGE_NOTIFICATION_SENT"


# 事件类型允许出现的聚合类型，登记错配在追加事件时即被拒绝。
EVENT_AGGREGATE: dict[EventType, AggregateType] = {
    EventType.CONSENT_REGISTERED: AggregateType.DONOR,
    EventType.CONSENT_SCOPE_UPDATED: AggregateType.DONOR,
    EventType.CONSENT_WITHDRAWN: AggregateType.DONOR,
    EventType.SAMPLE_DERIVED: AggregateType.SAMPLE,
    EventType.BATCH_PREPARED: AggregateType.BATCH,
    EventType.BATCH_QUALITY_REVIEWED: AggregateType.BATCH,
    EventType.SEQUENCING_FILE_REGISTERED: AggregateType.SEQUENCING_FILE,
    EventType.SEQUENCING_CHECKSUM_VERIFIED: AggregateType.SEQUENCING_FILE,
    EventType.SEQUENCING_CHECKSUM_FAILED: AggregateType.SEQUENCING_FILE,
    EventType.CELL_QC_RECORDED: AggregateType.CELL_QC,
    EventType.QC_THRESHOLD_CHANGED: AggregateType.CELL_QC,
    EventType.ANNOTATION_SUBMITTED: AggregateType.ANNOTATION,
    EventType.ANNOTATION_CONFLICT_RAISED: AggregateType.ANNOTATION,
    EventType.ANNOTATION_APPROVED: AggregateType.ANNOTATION,
    EventType.COHORT_DEFINED: AggregateType.COHORT,
    EventType.SAMPLE_INCLUDED: AggregateType.COHORT,
    EventType.SAMPLE_EXCLUDED: AggregateType.COHORT,
    EventType.ANALYSIS_STARTED: AggregateType.RUN,
    EventType.ANALYSIS_SHARD_COMPLETED: AggregateType.RUN,
    EventType.ANALYSIS_COMPLETED: AggregateType.RUN,
    EventType.ANALYSIS_SUPERSEDED: AggregateType.RUN,
    EventType.FINDING_RECORDED: AggregateType.FINDING,
    EventType.FINDING_CORRECTED: AggregateType.FINDING,
    EventType.FIGURE_APPROVED: AggregateType.FIGURE,
    EventType.PACKAGE_ASSEMBLED: AggregateType.PACKAGE,
    EventType.PACKAGE_RELEASED: AggregateType.PACKAGE,
    EventType.PACKAGE_REVISION_PUBLISHED: AggregateType.PACKAGE,
    EventType.PACKAGE_NOTIFICATION_SENT: AggregateType.PACKAGE,
}


class Purpose(StrEnum):
    """数据使用用途，授权判断以用途+角色二元组为单位。"""

    PIPELINE = "pipeline"            # 联盟内制备与计算
    RESEARCH = "research"            # 获批的合作研究
    DISEASE_RESEARCH = "disease_research"   # 限定疾病队列研究
    ANCESTRY_RESEARCH = "ancestry_research"  # 限定祖源队列研究
    PUBLIC_DOWNLOAD = "public_download"      # 公开数据包下载
    CLINICAL = "clinical"            # 临床接口：只读研究结论
