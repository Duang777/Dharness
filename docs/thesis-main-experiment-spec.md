# 论文主实验规范

## 状态

本规范定义 `thesis-main-analysis-v1`。它在 PrefixBench test split 运行前固定主实验的输入、
方法、分母、统计和结论边界。

本规范不替换以下已有协议：

- `experiments/historical-7/manifest.json`
- `experiments/prefixbench-v1/mutation-protocol-v1.json`
- `experiments/prefixbench-v1/test-mutation-protocol-v1.json`

已有协议继续控制 Historical-7、development campaign 和 held-out descriptive campaign。
主实验只能读取这些协议产生的 source-bound artifacts。

## 研究范围

### 系统对象

主对象是 Evidence Harness 的类型化终端 Agent 控制循环。测试对象是模型响应进入控制器后形成的
协议事件、控制状态和完成证据关系，不是模型的通用任务能力。

外部有效性对象是 mini-swe-agent，固定上游：

```text
repository: https://github.com/SWE-agent/mini-swe-agent
commit: 04d809ceab9df28f9adaed044884180159172930
trajectory_format: mini-swe-agent-1.1
```

### 四个研究问题

| RQ | 问题 | 数据 |
|---|---|---|
| RQ1 | Historical-7 是否稳定复现修复前缺陷并确认修复后行为 | 7 个历史缺陷对 |
| RQ2 | state-aware mutation 是否比四类等预算基线发现更多目标不变量违规 | 61 题 PrefixBench test split |
| RQ3 | source-anchored reducer 是否比 no reduction、flat ddmin 和 HDD 产生更小且保真的反例 | RQ2 中全部 state-aware target violations |
| RQ4 | 四类控制语义能否迁移到 mini-swe-agent | 固定 20 题 CrossHarness cohort |

### 不属于主实验的内容

以下内容不进入四个 RQ 的主效应、p 值或置信区间：

- PrefixBench development split。
- Terminal-Bench 2.1。
- production mutation score。
- live rerun cost 或 token savings。
- 任务 reward 或模型能力排名。
- OpenHands 或第三个 Harness。
- I5 至 I10 的扩展不变量。

Terminal-Bench 2.1 只允许进入独立敏感性分析。production mutation score 和 live cost
只允许进入独立预注册的辅助分析。没有独立协议时，这些字段必须是 `not_evaluated`。

## 已知输入

协议冻结时绑定以下已存在文件：

| 角色 | 路径 | SHA-256 |
|---|---|---|
| Historical-7 manifest | `experiments/historical-7/manifest.json` | `498959a371dca885792d2b8807c24808c88035c46534f55284b5ac1baf1bf20e` |
| Historical-7 report | `evaluation/historical-7.json` | `bac8ac96373332473a5cc5011a43cd7a5d0aad3fca730617b062e60cb0077c97` |
| development matrix | `evaluation/matrix-prefixbench-development.json` | `f95bfc0ac1836b49dcab8323e6ff702586edc97c1f71f2986ae3c1e6645a6067` |
| development mutation protocol | `experiments/prefixbench-v1/mutation-protocol-v1.json` | `360c09736d24597ffb4e452dd70b9d468a6213df2167d0e546420c22522cd377` |
| development campaign | `evaluation/prefixbench-v1-development-offline-campaign.json` | `395c10022f9682ff31246701e058a94aa983b1f236c9f3a6b164e8dc62c6aa83` |
| development analysis | `evaluation/prefixbench-v1-development-offline-analysis.json` | `5cac7e32f3540c511a782759efdd25c617a3113f40fb9b9566bc5a40fe3cddb7` |
| test matrix | `evaluation/matrix-prefixbench-test.json` | `a09d843fd1c4c356b7b48e5982655e9e20eded2fe394b7667ba86b00ce1826cb` |
| test mutation protocol | `experiments/prefixbench-v1/test-mutation-protocol-v1.json` | `a6972d068a5595396ccea6bc3d3d66bc0d609c1596aeb16d73fa961e33000d2c` |

测试矩阵冻结提交是
`2e3e65868213238d9bbcdbf3e09ce4356c8edfd9`。held-out 协议冻结提交和本规范的设计基线是
`f5c463b399201911ce9042db559f90f8a6d91337`。

test protocol 的 source set 使用
`sha256-length-framed-path-content-v1`，摘要为
`f1080f463e733f41fd6c27fd6c95d2622837759907999a3bc629ed458de1be9a`。
该 source set 含 21 个文件：

```text
pyproject.toml
uv.lock
evaluation/debian-https.sources
evaluation/debian-bullseye-main.list
evaluation/debian-trixie-https.sources
scripts/collect_evaluation_results.py
scripts/run_evaluation.py
scripts/run_full_evaluation.py
src/evidence_harness/collection_profile.py
src/evidence_harness/evaluation.py
src/evidence_harness/protocol.py
src/evidence_harness/source_binding.py
src/evidence_harness_mutation/attempts.py
src/evidence_harness_mutation/campaign.py
src/evidence_harness_mutation/invariants.py
src/evidence_harness_mutation/journal_loader.py
src/evidence_harness_mutation/model.py
src/evidence_harness_mutation/operators.py
src/evidence_harness_mutation/prefixbench.py
src/evidence_harness_mutation/prefixbench_test_campaign.py
src/evidence_harness_mutation/reducer.py
```

主实验实现只能新增 sibling modules。任何确认性版本都必须证明这 21 个文件与 `f5c463b`
中的 bytes 相同。

## 时间和来源合同

### 三类时间点

协议区分三类输入：

1. 已知输入。Historical-7 和 development 结果已在本协议前产生，因此只能作为 locked
   retrospective 或 descriptive evidence。
2. 未知输入。PrefixBench test 和 CrossHarness outcomes 在协议冻结时必须不存在。
3. 下游结果。RQ2、RQ3、RQ4、主报告和敏感性产物必须由协议提交之后的 producer 生成。

### Non-read gate

`freeze` 和 `preflight` 在读取任何可能暴露 held-out outcome 的文件前检查以下路径：

```text
runs/terminal-bench-2/prefixbench-v1-test-20261002
evaluation/prefixbench-v1-test-canonical.json
evaluation/prefixbench-v1-test-readiness.json
evaluation/prefixbench-v1-test-offline-campaign.json
evaluation/prefixbench-v1-test-method-comparison.json
evaluation/prefixbench-v1-test-reducer-comparison.json
evaluation/miniswe-agent-transfer-v1.json
evaluation/thesis-main-analysis-v1.json
evaluation/thesis-tb21-sensitivity-v1.json
evaluation/thesis-auxiliary-v1.json
```

门禁先执行文件系统存在性检查，再对可提交路径执行：

```bash
git log --all --format=%H -- <path>
```

任一路径存在或在任一可达 ref 中出现时，v1 `freeze` 失败。测试必须使用 reader spy 证明
门禁先于协议输入读取。

### Git chronology

机器可读协议首次出现的提交记为 `P`。每个 outcome artifact 记录 producer commit `O`。
主分析要求：

```text
P is ancestor of O
```

若阶段 2 另有 executable source-set manifest，其提交记为 `E`。每个确认性 outcome producer
还必须满足：

```text
E is ancestor of O
```

报告同时验证工作区 bytes、协议中的逐文件摘要和 `git show <commit>:<path>` bytes。
删除结果文件不能绕过 all-refs history gate。

该证据只支持以下措辞：

> 仓库可验证的协议和执行实现早于所有绑定的 outcome artifacts。

该证据不能证明研究者在仓库外从未查看结果。

### Canonical JSON

全部协议和结果 JSON 使用：

```text
ensure_ascii=True
indent=2
sort_keys=True
one trailing newline
```

外部 JSON、Git 输出和 trajectory 在模块边界解析为 frozen Pydantic models。内部统计函数
不接收裸 `dict[str, Any]`。

## Development 边界

development 分析固定为：

```text
role: calibration-only-descriptive
pooling_into_main_analysis: false
model_selection_use_after_protocol: forbidden
```

主报告复制并验证 development analysis 的全部 claim 状态。以下字段必须保持
`not_evaluated`：

- `container_call_telemetry`
- `inferential_statistics`
- `model_call_telemetry`
- `phase_target_reachability`
- `rq2_baseline_comparison`
- `rq3_production_mutation_score`
- `rq4_live_cost_savings`
- `test_split_generalization`

任何 development task identity 出现在 RQ2 或 RQ3 行中时，主报告无效。

## RQ1 规范

### 输入和单位

输入是 `evaluation/historical-7.json` 的 7 个 case pair 和 14 个 revision cell。统计单位是
historical case pair。

### 成功条件

一个 pair 只有同时满足以下条件才成功：

- vulnerable revision 的观察结果是 `survived`。
- fixed revision 的观察结果是 `killed`。
- 两个 cell 都不是 `inconclusive` 或 `error`。
- 报告中的 frozen expectation 与 manifest 一致。

报告以下计数：

- vulnerable reproduction rate。
- fixed confirmation rate。
- paired success rate。
- inconclusive 和 error 数。
- 两次完整重建是否 byte-identical。

先前路线已定义 `at least 6 of 7 pairs` 为工程成功门槛。主报告保留该门槛，但把 RQ1 标记为
`locked_retrospective`，因为结果先于本协议存在。RQ1 不计算 p 值，不进入多重比较。

### 可支持的结论

RQ1 只支持 Evidence Harness 项目内的历史缺陷复现结论。它不支持未知缺陷发现率或跨 Harness
泛化结论。

## RQ2 规范

### 方法

固定比较五种方法：

| 方法 | 可见信息 | 固定行为 |
|---|---|---|
| `state_aware` | 完整 `StatePrefix`、attempt projection 和 operator precondition | 逐行消费已冻结 held-out campaign |
| `random_json` | canonical event JSON bytes 和 slot key | 固定 hash draw 选择 delete、insert、replace 或 truncate，再经过 JSON 和 Pydantic 边界 |
| `schema_valid_random` | event schema、canonical event 和 slot key | 从能通过对应 payload model 的单字段变体中按 hash 选一个，不读取 attempt 或 phase |
| `agentchaos_style` | 模型响应 JSON bytes 和 slot key | 固定轮换 empty、truncate、omit-required-field、wrong-scalar-type |
| `stateless_semantic` | 有序 events、event type 和 slot key | 使用同一目标语义 edit，但不读取 attempt、phase、epoch、candidate、review state 或 state-aware source line |

基线执行前不能读取 state-aware 的 audit、outcome、source line 或选中候选。分析器只在基线完成
后判断目标状态是否到达。

### Trial grid 和预算

每个 test task 的 slot 数为：

```text
B_task = 4 * max(1, projected_completion_attempts)
```

slot 顺序固定为 attempt-major、operator-minor。operator 顺序是：

1. `stale_evidence_epoch`, target I1。
2. `reorder_check_receipts`, target I2。
3. `review_timeout_fallback`, target I3。
4. `cross_candidate_evidence`, target I4。

没有 projected attempt 的 task 使用一个 synthetic attempt ordinal，只为保留四个
`not_reached` slot。该规则不生成伪造的 completion attempt。

五种方法必须使用相同 task、attempt ordinal、operator 和 slot ordinal。每个方法对每个 slot
只产生一次 mutation，不因 parse rejection、not reached 或不利 outcome 重抽。

随机选择不使用 Python `random`。第 `draw_ordinal` 次选择由以下 bytes 的 SHA-256 决定：

```text
thesis-main-analysis-v1 NUL
rq2 NUL
method NUL
task_identity_sha256 NUL
attempt_ordinal NUL
operator NUL
slot_ordinal NUL
draw_ordinal
```

实现取 digest 的前 8 bytes 作为 unsigned big-endian integer，并对候选数取模。协议 seed
为 `20261003`，并作为固定字段加入 hash 输入。

### Outcome partition

每个 slot 恰好属于以下一类：

```text
input_rejected
target_not_reached
oracle_invalid
oracle_equivalent
target_violation
other_oracle_change
```

定义如下：

- `input_rejected`：mutation bytes 不能通过 JSON 或对应 Pydantic 边界。
- `target_not_reached`：输入可解析，但 mutation 未进入该 slot 的目标控制状态。
- `oracle_invalid`：offline pipeline 无法形成可比较的 baseline 和 mutated audit。
- `oracle_equivalent`：baseline 和 mutated audit 完全相同。
- `target_violation`：baseline 在该 terminal 不违反目标 invariant，mutated audit 新增该目标
  invariant 的相同确定性失败。
- `other_oracle_change`：audit 改变，但没有新增该 slot 的目标 invariant。

这些分类描述 offline oracle。它们不等于 production killed 或 survived。

### 主要 estimand

先在 task 内聚合：

```text
task_detected(method, task) =
  method 在该 task 的固定 slot grid 中是否至少产生一个 target_violation
```

RQ2 对 `state_aware` 与四个基线分别构造 61 个 task-level 配对二元值。

### 统计

每个对比使用：

- 双侧 exact McNemar test。
- 配对 absolute risk difference。
- 10,000 次 task bootstrap percentile 95% interval。
- 四个基线对比按固定方法顺序执行 Holm correction。
- family-wise alpha 为 `0.05`。

固定方法顺序是：

```text
random_json
schema_valid_random
agentchaos_style
stateless_semantic
```

bootstrap draw 的 seed 来自：

```text
SHA256("thesis-main-analysis-v1\0RQ2\0" + comparator)
```

同一 task 内的 attempts 和 mutations 不作为独立样本。

### 次要指标

每种方法报告：

- scheduled slot 数。
- input acceptance rate。
- target reach rate。
- target violation rate。
- oracle equivalent rate。
- other oracle change rate。
- 发现的不同 target invariant 数。
- 每 100 个 slot 的 target violations。

次要指标不产生新的显著性 family。

### 结论规则

每个 contrast 独立报告 `confirmed` 或 `not_confirmed`。只有 risk difference 大于 0 且
Holm-adjusted `p < 0.05` 时，该 contrast 为 `confirmed`。

整体 RQ2 状态使用以下固定规则：

- 四个 contrast 都 confirmed 时，状态为 `broad_superiority_confirmed`。
- 仅 `stateless_semantic` contrast confirmed 时，可以报告
  `nearest_baseline_advantage_confirmed`，但不能报告 broad superiority。
- 其他情况为 `broad_superiority_not_confirmed`。
- 任一 test task 缺失时为 `incomplete`，不生成部分 p 值。

## RQ3 规范

### 纳入集合

RQ3 纳入 RQ2 `state_aware` 方法的全部 `target_violation`。不得抽样，不得按 reducer
表现、task reward、invariant 或反例大小筛选。

每个 case 必须从原 journal 和 frozen `MutationRequest` 重建同一个未缩减
`AppliedMutation`。不能从 held-out campaign 内联的已缩减反例开始。

### Reducer

固定比较四种方法：

| 方法 | 删除空间 |
|---|---|
| `no_reduction` | 不删除，执行三次 witness audit |
| `flat_ddmin` | 把可删除 events 和 target payload members 编为稳定 atom list，执行标准 ddmin |
| `hdd` | 使用 `trace -> event -> payload object/list member` 层次树，逐层执行 ddmin |
| `source_anchored` | 使用冻结的 batch event、single event 和 payload member fixed-point reducer |

所有 reducer 使用相同的稳定候选顺序。顺序先按 source line，再按 JSON object key 和 list
index。reducer 不修改 scalar 值，不使用 wall-clock cutoff，也不共享并行计数器。

### Witness predicate

每个候选必须同时满足：

1. 保留 `run_started`、proposal、verified terminal 和 mutation target anchor。
2. target 仍属于同一个 source-anchored completion attempt。
3. attempt 仍由同一个 terminal 结束。
4. 相同 invariant 在该 terminal 失败。
5. 有序 violation details 完全相同。
6. 连续三次 audit 产生相同 bytes。

### Size 和失败规则

每个 reducer run 报告：

```text
event_count
recursive_payload_member_count
compact_canonical_json_bytes
candidate_evaluations
audit_executions
accepted_reductions
failure_reason
```

主 size 是 `compact_canonical_json_bytes`。每个 task 的主值是：

```text
sum(after_bytes for included cases) / sum(before_bytes for included cases)
```

reducer 超时、异常或无法保持 witness 时，case 不从分母删除。该 run 使用
`after_bytes = before_bytes`，并记录失败原因。

### 统计

source-anchored 分别与 no reduction、flat ddmin 和 HDD 比较。每个对比使用：

- task-level paired retained-byte fraction。
- 双侧 exact Wilcoxon signed-rank test。
- paired rank-biserial effect。
- 10,000 次 task-cluster bootstrap percentile 95% interval。
- 三个对比按 `no_reduction`、`flat_ddmin`、`hdd` 顺序执行 Holm correction。
- family-wise alpha 为 `0.05`。

零差保留在描述统计中，但不参与 signed-rank 的符号枚举。ties 使用平均秩。exact p 值通过
全部符号组合计算。若没有非零 pair，则 p 值为 `1.0`。

audit executions、candidate evaluations、event count 和 payload member count 是次要指标。
它们不进入主检验 family。

### 小样本和结论规则

若纳入少于 10 个 counterexample 或少于 5 个 task：

```text
status: insufficient_cases
p_values: omitted
```

主缩减结论只有在 source-anchored 相比 flat ddmin 和 HDD 都满足以下条件时才确认：

- retained-byte difference 小于 0。
- Holm-adjusted `p < 0.05`。
- 所有 case 均保留在分母中。

与 no reduction 的对比是 sanity comparison，不决定 source-anchored 是否优于通用 reducer。

## RQ4 规范

### Cohort

CrossHarness 使用 ProgramBench 的固定 catalog。stage 2 protocol 按以下规则选择 20 个任务：

```text
SHA256("miniswe-transfer-v1\0" + instance_id)
```

实现按 digest 和 `instance_id` 排序，取前 20 个。选择器不能读取 trajectory、submission、
exit status、reward 或 grader output。stage 2 protocol 必须记录 catalog revision、catalog
SHA-256 和 20 个 exact IDs。

每个 task 接受第一个完整 trajectory。只允许恢复中断，不允许按 outcome 重跑或补样本。

### 独立中间模型

adapter 把 `mini-swe-agent-1.1` trajectory 转换为：

```text
Decision -> Action -> Observation -> CandidateState -> Terminal
```

adapter 和 transfer oracle 不能导入以下模块：

```text
evidence_harness_mutation.invariants
evidence_harness_mutation.operators
evidence_harness_mutation.attempts
```

adapter 不能使用 Evidence Harness 的 event type 或字段名。import graph test 强制该限制。

### 固定四族分母

RQ4 始终报告四个原语义族：

| 原语义族 | mini-swe-agent 映射 | 状态 |
|---|---|---|
| I1 evidence freshness | workspace-changing action 后复用旧 observation 或旧 candidate evidence | `mapped` |
| I2 receipt order | 交换两个 action 的 observation association | `mapped` |
| I3 reviewer acceptance | mini-swe-agent v1 没有独立 reviewer | `unsupported_by_design` |
| I4 candidate binding | 把 submission terminal 绑定到另一 workspace tree digest | `mapped` |

I3 不记为 adapter failure、not applicable 或成功迁移。报告保留
`unsupported_by_design`，因此 mapped family 分母是 3，原语义族分母仍是 4。

### 每族 outcome

三个 mapped families 分别报告：

```text
scheduled
adapter_rejected
preexisting_violation
not_applicable
applicable
target_violation
other_oracle_change
deterministic_replay
```

mutation 只有在 source trajectory 通过目标 invariant 且 mutant 新增该 invariant 失败时，
才记为 `target_violation`。每个 target violation 连续审计三次。

### 成功门槛

一个 mapped family 同时满足以下条件时，该 family 支持迁移：

- 至少 5 个 task 为 applicable。
- 至少 2 个 task 产生 deterministic target violation。
- 三次 audit 对每个计入的 violation 产生相同 bytes。

三个 mapped families 全部达到门槛时，RQ4 为 `transfer_supported`。否则为
`transfer_not_confirmed`。若 20 题 collection 不完整，则为 `incomplete`。

RQ4 不计算 p 值，不比较两个 Harness 的任务成功率，也不声称发现 mini-swe-agent 的生产缺陷。

## 缺失、排除和重试

| 情况 | 处理 |
|---|---|
| PrefixBench 少于 61 个完整 task | RQ2 和 RQ3 为 `incomplete` |
| task 没有 projected completion attempt | 保留 4 个 synthetic ordinal slots，结果通常为 `target_not_reached` |
| baseline parse 失败 | `input_rejected`，不重抽 |
| baseline 未到目标状态 | `target_not_reached`，不删除 |
| offline oracle 无法比较 | `oracle_invalid`，保留原因 |
| reducer 失败 | 使用未缩减大小，保留失败原因 |
| RQ3 少于 10 个 case 或少于 5 个 task | `insufficient_cases`，只报告描述统计 |
| CrossHarness source 已违反 invariant | `preexisting_violation`，不计 transfer credit |
| CrossHarness 缺目标结构 | `not_applicable`，保留在完整 partition 中 |
| mini-swe-agent 不支持 reviewer | I3=`unsupported_by_design` |
| completed task 后再次运行 | 拒绝 |
| 进程中断且 task 未完成 | 使用相同命令 resume |

任何缺失值都不做插补。任何错误都不能通过改变 task、seed、operator、reducer 或 outcome
定义来重试。

## 主报告

固定输出是：

```text
evaluation/thesis-main-analysis-v1.json
```

报告按以下顺序组织：

```text
protocol
chronology
source_bindings
development_context
rq1
rq2
rq3
rq4
sensitivity
auxiliary
deviations
```

每个 RQ 分开记录：

- source artifact bindings。
- 分母和完整 outcome partition。
- estimand。
- effect 和 interval。
- raw p value 和 adjusted p value，若适用。
- disposition。
- 不支持的结论。

主报告不能包含调用者机器上的绝对路径、provider env 内容或 provider env 摘要。

## 重建和检查

### Full rebuild

当某一输入组的全部 raw sources 存在时，checker 必须重建对应子产物并逐字节比较。输入组包括：

- PrefixBench held-out collection。
- RQ2 method comparison。
- RQ3 reducer comparison。
- CrossHarness collection。

### Artifact-only check

当某一输入组的 raw sources 全部不存在时，checker 可以验证：

- Pydantic schema。
- canonical encoding。
- file bindings。
- source-set bindings。
- producer commit 和 ancestor 关系。
- task、slot、method、reducer 和 outcome partition。
- 由嵌套 rows 可重新计算的全部 summary。

任一输入组只存在部分 raw sources 时，checker 失败。artifact-only check 不能宣称重新执行了
模型、环境或 reducer。

## 机器可读协议接口

阶段 1 新增：

```text
src/evidence_harness_mutation/main_analysis_protocol.py
scripts/main_analysis_protocol.py
tests/mutation/test_main_analysis_protocol.py
tests/test_main_analysis_protocol_script.py
experiments/prefixbench-v1/main-analysis-protocol-v1.json
```

Python API 只有：

```python
freeze_main_analysis_protocol(project_root)
load_main_analysis_protocol(project_root)
preflight_main_analysis_protocol(project_root)
check_main_analysis_protocol(project_root)
```

CLI 只有：

```text
freeze
preflight
check
```

调用者不能传入 split、task、method、seed、operator、reducer、alpha、bootstrap count、
source artifact 或 output path。

## 协议版本

`main-analysis-protocol-v1.json` 一旦提交即不可覆盖。任何变化使用新 protocol ID 和新文件。

以下变化必须升级协议版本：

- RQ、hypothesis 或 claim boundary。
- task、split、cohort 或 task selection。
- baseline 的可见信息、选择规则或预算。
- operator、oracle 或 outcome partition。
- reducer 删除空间、witness predicate 或 size function。
- estimand、统计单位、检验、correction、alpha 或 bootstrap。
- missing、exclusion 或 retry 规则。
- source set 或外部 revision。

outcome 产生后的 material change 不能继续标记为 confirmatory。报告必须保留旧协议、旧结果和
deviation artifact。

## 设计合成

三个独立候选经过交叉评审后，候选 B 以 28/30 成为基础。最终规范保留 B 的单一确认性协议、
完整 attempt grid、失败保留规则、20 题 transfer cohort 和分阶段 gates。

规范吸收候选 A 的两项约束：

- all-refs outcome-history gate。
- RQ3 的 10 case 和 5 task 最低门槛。

规范吸收候选 C 的两项约束：

- chronology 只表述为 repository-enforced evidence。
- RQ4 保留四个原语义族，I3 固定为 `unsupported_by_design`。

以下设计不采用：

- 通用实验 DSL。
- 八模块分析 package。
- 30 题 transfer cohort。
- 把 audit calls 作为 RQ3 主 endpoint。
- 把 provider credential hash 写入协议或报告。
- 把 development 结果并入确认性统计。

## 可支持的最终主张

若各 RQ 达到固定门槛，论文可以分别主张：

- 协议感知突变在 Evidence Harness 的历史缺陷上具有项目内复现能力。
- state-aware selection 在 held-out task 上优于达到显著性门槛的具体基线。
- source-anchored reducer 在 held-out violations 上比两个通用 reducer 保留更少 bytes。
- 三个可映射控制语义族能迁移到 mini-swe-agent 的独立 trajectory schema。

论文不能主张：

- 该方法适用于所有 LLM Agent Harness。
- offline violation 等于生产可利用缺陷。
- development 结果证明 test split 泛化。
- Terminal-Bench 2.1 与 2.0 结果可直接合并。
- Git chronology 证明研究者从未在仓库外查看结果。
