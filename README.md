# 脑图谱研究资产发布台

本项目提供脑图谱研究资产发布台所需的领域事件交换约定与基础校验库。各接入方使用统一的聚合标识、事件版本和发生时间表达业务事实，避免跨系统交换时丢失来源顺序。

## 目录

- `contracts/domain.schema.json`：领域事件信封和已登记类型。
- `data/sample.json`：中文联调样例。
- `src/brain_atlas_release/contracts.py`：不依赖第三方包的基础校验器。
- `src/brain_atlas_release/lineage.py`：派生资产谱系与分片运行。
- `src/brain_atlas_release/policy.py`：队列授权、标注裁定与证据边界。
- `src/brain_atlas_release/release.py`：发布续传、图表溯源与下载回执。
- `tests/`：契约边界与领域规则检查。

## 领域对象与事件

聚合覆盖供体授权（donor_record）、去标识化样本（biospecimen_sample）、制备批次（biospecimen_batch）、测序文件（sequencing_file）、细胞质控（cell_qc_result）、注释版本（cell_annotation）、队列纳排（cohort_definition）、分析运行（analysis_run）、论文图表（paper_figure）和公开数据包（release_package）。事件类型覆盖授权登记与撤回、样本去标识化、批次质量复核、测序文件校验、细胞质控、注释批准与裁定、队列成员变更、分片记录、分析完成、图表审批和数据包发布。

## 领域规则

- 任何派生资产固定输入清单、参数和软件版本（`AssetManifest`），参数表固定后不可改写。
- 撤回或质量复核生成后继版本（`withdraw_asset` / `create_successor`），已用于正式论文的事实不可删除（`ensure_deletable`）。
- 大型批次可分片重试，同一运行的同一分片重复上报不重复计入（`record_shard`）。
- 队列访问按用途和角色最小授权，超范围字段被裁剪（`authorize`）。
- 并行标注冲突由有资质人员裁定，裁定须落在已提出标签中并说明理由（`adjudicate`）。
- 研究发现必须标明证据范围与限制；临床接口不得把相关性转换成个体诊断（`validate_finding` / `assess_individual`）。
- 发布中断后只补齐缺少的资产、引用和通知（`resume_plan`）；每个图表回溯到可用数据版本与审批（`check_figure_traceability`）；下载者凭回执确认拿到的是哪次修订（`verify_receipt`）。

## 测试

```bash
python3 -m unittest discover -s tests
```

## 编译检查

```bash
python3 -m compileall -q src tests
```
