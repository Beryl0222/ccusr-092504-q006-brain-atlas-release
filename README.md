# 脑图谱研究资产发布台

面向脑图谱联盟三类成果（生命阶段、疾病变化、基因性状关联）的研究资产
发布领域服务。以**仅追加事件流**贯通：供体授权范围 → 去标识化样本 →
制备批次 → 测序文件校验 → 细胞质控 → 注释版本 → 队列纳排 → 分析运行 →
研究发现 → 论文图表 → 公开数据包。

## 核心约束

- **派生资产固定三要素**：输入清单（资产+当时版本+校验值）、完整参数、
  软件组件版本；清单规范化后计算 sha256 指纹（`provenance.py`）。
- **事实不可删除**：撤回授权、批次复核打回、阈值调整、注释改判、发现
  更正全部以新事件追加；已用于正式论文图表或已发布包的运行进入
  `paper_locked`，不废止、不删除，只允许追加后继版本（`lineage.py`）。
- **最小授权**：按“用途 × 角色 × 队列”发放授权，授权角色必须是队列
  开放角色的子集；队列限制（疾病/祖源等）逐项匹配；个体级资产还要核对
  成员供体授权覆盖与撤回状态；临床用途只能读取带强制边界声明的聚合
  发现视图（`access.py`、`findings.py`）。
- **标注冲突裁定**：并行标注分歧必须登记冲突，由具备资质的人员
  （资深策展人/PI/执业神经病理医师）在已提交版本中裁定（`annotations.py`）。
- **证据边界**：研究发现只允许“关联”型断言，必须登记证据范围与限制，
  禁用疗效/诊断类措辞；临床视图恒附“相关性不构成个体诊断”声明。
- **分片幂等**：大型批次按分片重试，`(运行, 分片)` 只计入一次；清单
  指纹不一致必须另开运行；运行完成要求全部分片成功（`processing.py`）。
- **可恢复发布**：发布中断后只补齐缺失的发布事件、资产、引用和通知，
  不重复发布、不重复通知；修订顺序追加，旧修订可查；下载者凭修订号与
  清单指纹核对自己拿到的是哪次修订（`publishing.py`）。

## 目录

| 路径 | 职责 |
| --- | --- |
| `contracts/domain.schema.json` | 领域事件信封、12 类聚合与 28 类事件登记 |
| `src/brain_atlas_release/catalog.py` | 聚合、事件、用途枚举与事件-聚合归属表 |
| `src/brain_atlas_release/contracts.py` | 不依赖第三方包的交换层校验器 |
| `src/brain_atlas_release/errors.py` | 带稳定 code 的领域违例 |
| `src/brain_atlas_release/store.py` | 仅追加事件存储、版本递增、投递幂等 |
| `src/brain_atlas_release/records.py` | 供体/样本/批次/测序/质控/注释登记 |
| `src/brain_atlas_release/cohorts.py` | 队列定义、纳排资格判定 |
| `src/brain_atlas_release/provenance.py` | 派生清单与指纹 |
| `src/brain_atlas_release/lineage.py` | 撤回传播、论文锁定、后继版本校验 |
| `src/brain_atlas_release/access.py` | 用途与角色最小授权 |
| `src/brain_atlas_release/annotations.py` | 并行标注冲突裁定 |
| `src/brain_atlas_release/findings.py` | 研究发现证据边界与临床只读视图 |
| `src/brain_atlas_release/processing.py` | 分析运行与分片幂等 |
| `src/brain_atlas_release/publishing.py` | 图表审批、可恢复发布、修订与下载核对 |
| `tests/` | 契约边界、各模块单测与端到端联调 |

## 测试

```bash
python3 -m unittest discover -s tests
```

端到端用例 `tests/test_end_to_end.py` 演示同一供体进入三类成果、阈值
调整后的废止与后继、冲突裁定、最小授权、可恢复发布和逐图回溯。

## 编译检查

```bash
python3 -m compileall -q src tests
```
