# 论文定位与贡献计划

更新日期：2026-10-08

## 文档目的

本文记录 Evidence Harness 论文的定位、贡献层次和后续实验门槛。它回答两个问题：

1. 当前工作是否达到硕士论文要求。
2. 如果计划中的实验全部完成，工作是否具备 B 类会议投稿竞争力。

本文不把待执行实验写成结果。实验状态以 `evaluation/` 中的 canonical artifact 和主实验协议为准。

## 当前判断

### 硕士论文

当前工作已经具备硕士论文的主体条件：

- 有明确的问题。LLM Agent 的完成判定不能只依赖模型自报告。
- 有可实现的机制。完成被定义为由证据约束和状态约束共同守卫的协议状态转移。
- 有完整参考实现。Evidence Harness 实现了控制循环、证据门禁、完成审查、新鲜度绑定、回放和隔离验证。
- 有真实运行证据。项目保存了 Terminal-Bench 2.0、Historical-7 和 PrefixBench development 产物。
- 有可复现门禁。测试、静态检查、类型检查、构建和结果检查都有固定命令。
- 有明确边界。当前结果没有把 development、Terminal-Bench 2.0 或设计预期改写成确认性主实验结论。

如果学校要求论文包含问题、设计、实现、实验和局限，这个项目的主体已经覆盖这些部分。后续实验会提高结论强度，但不会改变论文已经形成的基本结构。

### B 类会议

如果后续计划中的实验全部完成，且结果稳定支持当前主张，项目可以达到认真准备 B 类会议投稿的水平。是否录用仍取决于最近邻工作的区分度、实验公平性、结果大小和论文表达。

仅有完整代码或更多 benchmark 分数不够。B 类会议版本必须证明这不是只适用于 Evidence Harness 的工程组合，而是一种有清楚抽象边界、受控对照和可迁移证据支持的完成控制方法。

## 核心贡献的收缩版本

论文只保留一个核心创新，其他内容作为机制、实现或评估支撑。

### 核心贡献

提出一种状态约束的完成控制协议，将 LLM Agent 的完成从模型自报告转化为由完成契约、证据覆盖、证据新鲜度、候选状态和控制状态共同守卫的协议状态转移。

该贡献的机制名称是 Evidence Protocol。Dharness 或 Evidence Harness 是它的参考实现，不应被表述为论文唯一的创新对象。

### 机制支撑

- `work_epoch`、`attempt` 和 `candidate_digest`：状态绑定的证据新鲜度机制。
- `Completion Contract`、`Evidence Projection`、`Valid`、`Coverage` 和 `Accept`：完成协议的组成部分。
- `EvidenceGate` 和 `ReviewOK`：完成状态转移的机械和语义守卫。
- 隔离 completion check：避免验证过程污染被评分候选，并提供可审计的证据来源。
- finalization 和预算保留：保证任务在接近墙钟边界时仍有完成审查机会。

### 评估支撑

- Historical-7：用真实历史缺陷验证修复前后行为。
- PrefixBench：评估状态化突变、基线和 reducer。
- 内部与外部双层评价：区分 Harness 接受完成和外部任务验证通过。
- mini-swe-agent 迁移：检验协议是否依赖 Evidence Harness 的具体字段。

突变测试、隔离重跑、语义审查和双层指标不是四个平行的核心创新。它们分别承担评估、证据获取、完成守卫和结论校准的职责。

## 论文可以支持的主张

### 当前已支持

这些主张必须绑定现有冻结产物：

1. Evidence Harness 在 Terminal-Bench 2.0 的固定 89 题上产生了 59 个 reward 1.0、30 个 reward 0 和 0 个运行错误。
2. 85 个 live trial 中，内部完成信号和外部 verifier reward 有 22 例不一致，其中有 10 个内部假阳性和 12 个内部假阴性。
3. Historical-7 的 7 个历史缺陷对都满足修复前 `survived`、修复后 `killed` 的冻结预期。
4. PrefixBench development campaign 已经产生描述性 artifact，并明确区分 `offline_violation`、`oracle_equivalent` 和 production mutation result。
5. 当前实现、测试和结果产物支持 Evidence Harness 内部的完成控制分析。

这些主张不能直接扩展为“协议优于所有基线”或“协议已经跨 Harness 泛化”。

### 完成全部计划实验后可以增加的主张

只有对应产物通过主实验检查后，才能增加以下主张：

- RQ2：state-aware mutation 是否比四类等预算 baseline 发现更多目标不变量违规。
- RQ3：source-anchored reducer 是否比 no reduction、flat ddmin 和 HDD 产生更小且保持 witness 的反例。
- RQ4：四类控制语义是否可以迁移到 mini-swe-agent。
- Freshness、Coverage、Candidate Binding、Review 和 BudgetBound 各自对完成控制的影响。
- 协议带来的执行、token、环境调用和验证次数开销。

如果某项对照没有达到预设门槛，论文应报告 `not_confirmed`，而不是把设计预期写成正向结论。

## B 类会议的最低证据包

以下项目是“可以认真投稿”的最低条件。它们不是录用保证。

### 1. Held-out test split

- 完成 PrefixBench 61 题 test split。
- 结果来自冻结协议之后的 producer。
- 每个 task 恰好有一个 canonical result。
- 重建结果具有稳定 bytes。

对应产物：

```text
evaluation/prefixbench-v1-test-canonical.json
evaluation/prefixbench-v1-test-readiness.json
evaluation/prefixbench-v1-test-offline-campaign.json
```

### 2. RQ2 等预算对照

五种方法必须使用同一 task、attempt、operator 和 slot grid：

```text
state_aware
random_json
schema_valid_random
agentchaos_style
stateless_semantic
```

主比较先聚合到 task，再进行配对统计。论文至少报告 target violation、target reach、input acceptance、absolute risk difference、95% task bootstrap interval 和多重比较校正结果。

### 3. RQ3 reducer 对照

四种 reducer 必须从同一个未缩减输入开始，并共享 witness predicate：

```text
no_reduction
flat_ddmin
hdd
source_anchored
```

比较不能只看平均事件数。必须同时报告 witness 保真、失败 reducer 的原因和候选审计成本。

### 4. 组件消融

至少完成以下消融：

- Freshness。
- Candidate Binding。
- Coverage。
- Review。
- BudgetBound。

每个消融都要固定 task、模型、预算、输入和统计口径。只改变一个组件，不能把多个组件同时关闭后归因给单个机制。

### 5. 泛化或形式化支撑

至少完成以下一项，最好完成两项：

- 在 mini-swe-agent 上完成字段映射和四类控制语义的迁移实验。
- 对 stale evidence non-acceptance、candidate mismatch non-acceptance、fail-closed completion 和 state transition safety 给出形式化性质、证明或模型检查结果。

跨 Harness 实验失败也有价值，但结论必须收窄为“适用于满足接口条件的类型化终端 Harness”，不能继续声称普遍泛化。

### 6. 成本和边界

报告协议相对于 baseline 的 wall time、token、环境调用、验证次数和失败模式。成本不能只列总数，还要解释收益和开销的关系。

## 结果强度与最终定位

| 完成状态 | 硕士论文定位 | B 类会议定位 |
| --- | --- | --- |
| 当前实现、59/89、Historical-7 和 development artifact | 基本够 | 设计和工程证据有价值，但对照不足 |
| 计划实验全部完成，但差异小或不稳定 | 很扎实 | 可能被评价为系统工程或负结果研究 |
| test split、RQ2、RQ3、消融和成本结果稳定支持主张 | 很强 | 具备正式投稿竞争力 |
| 上述结果加形式化性质或跨多个 Harness 验证 | 很强 | 新颖性和外部有效性更有说服力 |

“全部完成”不等于“结果一定支持主张”。论文应根据预注册的结论规则报告 `confirmed`、`not_confirmed` 或 `incomplete`。

## 实验完成后的论文叙事

最终论文按下面的证据链组织：

```text
真实运行发现完成判定错位
    -> 定义状态约束完成控制协议
    -> 实现 Evidence Harness reference implementation
    -> 用 Historical-7 验证真实历史缺陷
    -> 用 PrefixBench test split 做受控方法比较
    -> 用消融解释 Freshness、Coverage、Review 和 Candidate Binding 的作用
    -> 用 reducer 对照验证反例缩减效果
    -> 用成本分析报告协议开销
    -> 用 mini-swe-agent 或形式化性质检验外部有效性
    -> 说明适用条件、失败边界和不能支持的结论
```

这条链条比继续添加孤立模块更重要。后续实现应优先服务于一项可验收的 RQ 或一项明确的有效性威胁。

## 相关文档和执行入口

- 研究路线：[`agent-harness-research-roadmap.md`](agent-harness-research-roadmap.md)
- 主实验计划：[`thesis-execution-plan.md`](thesis-execution-plan.md)
- 主实验规范：[`thesis-main-experiment-spec.md`](thesis-main-experiment-spec.md)
- 主分析实现设计：[`main-analysis-executable-design.md`](main-analysis-executable-design.md)
- 架构决策：[`architecture-rationale.md`](architecture-rationale.md)
- 评测报告：[`evaluation-report.md`](evaluation-report.md)
- 失败分析：[`failure-analysis.md`](failure-analysis.md)
