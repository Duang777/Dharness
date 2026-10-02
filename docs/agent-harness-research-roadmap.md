# Evidence Harness 硕士论文研究路线

调研日期：2026-10-01

## 结论

推荐题目：

> 面向 LLM Agent Harness 的协议感知有状态突变测试：完成控制缺陷的可复现检测

英文题目：

> Protocol-Aware Stateful Mutation Testing for LLM Agent Harnesses:
> Reproducible Detection of Completion-Control Failures

这项研究直接测试 Harness 的控制协议，模型能力和工具接口只作为外部条件。方法从真实 journal
恢复深层状态前缀，在模型决策、命令回执、语义审查和隔离证据边界注入结构合法但语义危险的
事件，再用确定性不变量判断 Harness 是否拒绝错误、进入恢复、错误完成或产生不一致状态。
发现失败后，系统缩减状态前缀和突变载荷，输出不依赖真实模型再次生成异常行为的最小复现用例。

截至 2026-10-01，本次检索覆盖 30 篇直接相关论文。已有研究分别覆盖 LLM API 故障注入、
工具响应扰动、Prompt 语义突变、Agent Skill 语义模糊测试、对话工作流边界测试、多智能体
轨迹回放和验证证据判别力。当前检索范围内，尚未发现一项工作同时满足以下条件：

- 把 Harness 控制循环本身作为被测系统。
- 从真实轨迹恢复控制器状态前缀。
- 注入符合数据结构但违反当前状态语义的决策或证据。
- 用完成、预算、隔离和恢复不变量提供确定性判定。
- 自动缩减失败轨迹和突变载荷。

因此，这个题目仍有可辩护的研究空白。创新性应表述为“在当前检索范围内尚未发现直接覆盖”，
不能表述为“从未有人研究”。

## 项目已经具备的实证基础

### 全量评测不是概念验证

当前项目已经在 Terminal-Bench 2.0 固定矩阵上执行全部 89 题，得到 59 passed、
30 failed、0 error，通过率为 66.3%。其中 85 题来自 live trial，4 题来自日志回放。
85 个 live trial 的内部完成判定与外部 verifier 形成如下混淆矩阵：

| 外部结果 | 内部 `verified` | 内部未完成 |
|---|---:|---:|
| reward 1.0 | 45 | 12 |
| reward 0 | 10 | 18 |

由此得到：

- precision：81.8%
- recall：78.9%
- accuracy：74.1%
- 内外部判定错位：22 例

10 个内部假阳性说明“检查通过”不等于“任务完成”。12 个外部通过但内部未完成的案例说明
控制器也可能过度拒绝。这个双向错位比单纯报告 benchmark 分数更适合支撑完成控制研究。

85 个 live trial 共消耗 1,933 turns、2,359 次环境调用、28,565,991 个输入 token 和
2,557,279 个输出 token。89 题累计运行约 20 小时。状态前缀回放可以把大量实验从真实模型
调用改成确定性控制器执行，因此既是研究方法，也是实验成本控制手段。

### 历史修复可以作为真实缺陷种子

项目已有多个由真实轨迹触发的控制缺陷，且修复前后的 commit 可定位：

| 缺陷种子 | 旧行为 | 对应修复 |
|---|---|---|
| reviewer 在检查执行前判断 | reviewer 只能看到提案，不能看到真实回执 | `5f64d66` |
| reviewer 配额耗尽后旁路 | 后续通过检查可绕过语义复核 | `5f64d66` |
| reviewer 超时后继续成功 | 服务故障可能退化成接受 | `5f64d66` |
| 失败的 change 被算作进展 | 停滞计数被错误重置 | `5f64d66` |
| `max_repairs` 多放行一次 | repair budget 存在 off-by-one | `5f64d66` |
| 长命令跨过 finalization 边界 | 最后 10% 墙钟时间被普通工作消耗 | `3a3b83f` |
| completion 证据绑定不足 | attempt、candidate 和 receipt 可能错配 | `289bea4` |

这些案例可以构成历史缺陷基准。实验在修复前 commit 上复现缺陷，再在修复后 commit 上确认
同一突变被拒绝。与纯合成故障相比，这种设计能证明突变算子对应真实工程问题。

### Historical-7 完整结果

截至 2026-10-01，七个缺陷对均已完成真实双版本执行，不是用当前 oracle 对人工 journal
自测。每个 revision 都通过 `git archive` 物化，在独立 Python 进程中导入该 commit 的生产
源码：

| 历史属性 | vulnerable | fixed | 生产入口 |
|---|---|---|---|
| review timeout fails closed | `ba2cbae`: `survived` | `5f64d66`: `killed` | `EvidenceLoop.run` |
| receipts match proposal order | `6f74c19`: `survived` | `289bea4`: `killed` | `EvidenceGate.decide` |
| review sees executed receipts | `ba2cbae`: `survived` | `5f64d66`: `killed` | `EvidenceLoop.run` |
| review quota fails closed | `ba2cbae`: `survived` | `5f64d66`: `killed` | `EvidenceLoop.run` |
| failed changes do not count as progress | `ba2cbae`: `survived` | `5f64d66`: `killed` | `EvidenceLoop.run` |
| repair budget counts granted repairs | `ba2cbae`: `survived` | `5f64d66`: `killed` | `EvidenceLoop.run` |
| work preserves finalization time | `5f64d66`: `survived` | `3a3b83f`: `killed` | `EvidenceLoop.run` |

14 个 cell 均与冻结预期一致。报告绑定 commit、tree、archive commit，以及 manifest、worker、
coordinator、schema 和完整生产源码集的 SHA256。父进程与 worker 独立计算生产源码摘要。
重复运行得到相同报告字节。Historical-7 已达到 `7/7`，支持 RQ1 的项目内历史缺陷
可复现性；它仍不支持跨 Harness 泛化结论。

### Offline campaign 与反例缩减

截至 2026-10-02，离线 campaign 已实现。默认调度按 attempt ordinal 和算子声明顺序运行
四个结构化算子，显式请求同样稳定排序且保留重复项。每行明确分类为
`mutation_not_applicable`、`offline_invalid`、`oracle_equivalent`、
`offline_violation` 或 `other_oracle_change`。只有 baseline 在同一 source-anchored attempt
上未出现预期失败、而突变后新出现该失败时，才记为 `offline_violation`。这些名称只描述离线
oracle，不等同于 Historical-7 对生产 `EvidenceLoop` 或 `EvidenceGate` 的 killed/survived
结论。

缩减器保留 proposal、verified terminal 和 mutation target 的原始行号与行 SHA256，
先做事件批次删除，再做单事件与目标 payload 成员的固定点删除。每个候选连续审计三次，并
要求 invariant 与有序 detail tuple 不变。当前四个算子在共享 schema-2 fixture 上均产生
确定的缩减反例，事件数分别从 15 降为 7、6、5 和 12。报告声明的是
`1-minimal-under-declared-removals`，不是全局最小。

### 现有架构已经暴露合适的测试边界

| 现有模块 | 可复用能力 | 论文原型中的用途 |
|---|---|---|
| `src/evidence_harness/protocol.py` | 严格的决策、回执和状态类型 | 定义类型保持的突变载荷 |
| `src/evidence_harness/run_loop.py` | 完成、repair、预算和 finalization 状态机 | 主要被测系统 |
| `src/evidence_harness/evidence.py` | epoch、顺序、review 和隔离门禁 | 确定性不变量 oracle |
| `src/evidence_harness/replay_agent.py` | source-bound 命令回放 | 扩展为状态前缀与决策回放 |
| `src/evidence_harness/docker_completion_isolation.py` | 候选快照和独立检查容器 | 验证隔离与候选绑定不变量 |
| `evaluation/completion-disagreements.json` | 22 个内外部错位案例 | 真实前缀与案例选择 |
| `evaluation/results-89.json` | 85 个 live trial 和外部 reward | 校准数据与分层抽样 |

项目当前有 233 项测试通过，覆盖率为 84%。三个冻结候选的隔离回放实验均获得外部
reward 1.0，但内部 `verified` 为 0/3。这个结果表明机械证据、语义审查和外部评分是三种
不同信号，不能互相代替。

## 文献矩阵

### Harness 构造、自进化和迁移

| 工作 | 主要贡献 | 对本题的约束 |
|---|---|---|
| [AHE](https://arxiv.org/abs/2604.25850) | 从轨迹和组件可观测性生成可验证的 Harness 修改 | 通用 Harness 自进化已被直接覆盖 |
| [Self-Harness](https://arxiv.org/abs/2606.09498) | 模型根据失败轨迹修改自己的 Harness，并用非退化门禁筛选 | “让 Harness 自己改进”不能单独作为创新 |
| [Life-Harness](https://arxiv.org/abs/2605.22166) | 在不更新模型权重时适配环境契约、动作和轨迹调节 | 状态化协议失配已被用于适配，但未用于测试控制器 |
| [HarnessX](https://arxiv.org/abs/2606.14249) | 类型化组件、轨迹驱动演化和 Harness 与模型协同训练 | 组件化和自进化空间已经较完整 |
| [HarnessBank](https://arxiv.org/abs/2607.13683) | Harness gene bank、组合搜索和 gated verification | 自进化的搜索与筛选也已有直接工作 |
| [MemoHarness](https://arxiv.org/abs/2607.14159) | 从执行经验中检索并按案例调整六类 Harness 控制项 | 经验复用不是独立空白 |
| [Living-Harness](https://arxiv.org/abs/2607.26598) | 用 episodic memory 和 state graph 持久化程序性修复 | 状态图式 Harness 演化已有研究 |
| [One Recipe, Many Harnesses](https://arxiv.org/abs/2608.10178) | 比较跨语言、跨模型的 Harness 演化内容和迁移边界 | 抽象规则可迁移，生态实现仍需本地演化 |
| [Co-Harness](https://arxiv.org/abs/2607.22688) | 交替优化 Harness 和模型参数 | 联合优化需要训练资源，不适合作为本项目主线 |
| [EvoHarnessBench](https://arxiv.org/abs/2609.04280) | 测试工具、Skill 和 Agent 扩展下的保留与适应 | 说明 Harness 演化会引入遗忘和适应冲突 |
| [JIT-Agent](https://arxiv.org/abs/2608.25593) | 训练专用模型按任务生成、修复和演化 Harness | 即时 Harness 生成也已有直接工作 |
| [MOSS](https://arxiv.org/abs/2605.22794) | 根据生产失败证据改写 Harness 源码并回放验证 | 源码级自演化与批量回放已被覆盖 |

### Skill 迁移和优化

| 工作 | 主要发现 | 对备选路线的意义 |
|---|---|---|
| [SkillLens](https://arxiv.org/abs/2605.23899) | 系统比较经验生成、Skill 提取和不同目标模型的消费效果，报告非平凡的负迁移 | 说明 Skill 或 Harness 增益不能由文本质量直接推断 |
| [SkillOpt](https://arxiv.org/abs/2605.23904) | 用执行结果和验证门禁优化 Skill，并测试跨模型、跨 Harness 和跨 benchmark 迁移 | 提供正迁移证据和可比较的验证协议 |
| [EvoSkills](https://arxiv.org/abs/2604.01687) | 用协同演化 verifier 生成 Skill，并报告跨模型族迁移 | 说明模型匹配不是正迁移的必要条件 |
| [SkillCommit](https://arxiv.org/abs/2608.15165) | 用跨实例回放验证 Skill 的兼容范围，再做层次化合并 | 说明迁移可以通过行为兼容性筛选 |

### 测试、故障注入和验证

| 工作 | 注入或验证边界 | 与本题的差别 |
|---|---|---|
| [Agent-Reactive Bugs](https://arxiv.org/abs/2607.15684) | 分析 255 个模型响应触发的 Harness 缺陷 | 提出复现和 oracle 需求，没有实现状态化测试系统 |
| [AgentChaos](https://arxiv.org/abs/2608.06790) | 在 HTTP 层注入 LLM API crash、omission 和 value fault | 主要修改响应完整性与字段值，不理解 Harness 当前状态 |
| [AgentCheck](https://arxiv.org/abs/2607.11098) | 记录并扰动 MCP 工具响应，再回放相同调用 | 测试工具边界，不测试模型决策进入控制器后的协议 |
| [AgentEval](https://arxiv.org/abs/2607.06873) | 挖掘对话工作流图，回放前置路径并测试状态边界 | 与状态前缀思想最接近，但目标是黑盒对话流程 |
| [TDAD](https://arxiv.org/abs/2603.08806) | 对编译后的 Prompt 做语义突变，衡量测试集判别力 | 突变对象是 Prompt，不是 Harness 运行时事件 |
| [ToolFuzz](https://arxiv.org/abs/2503.04479) | 生成输入以发现工具文档的过度或不足约束 | 测试工具说明，不测试控制状态 |
| [HarnessAudit](https://arxiv.org/abs/2605.14271) | 审计完整轨迹中的权限、执行和稳定性 | 提供轨迹级安全指标，不生成状态化协议反例 |
| [BSG-VA](https://arxiv.org/abs/2607.28871) | 在 buggy、candidate 和 gold 三种代码状态重放验证命令 | 已直接覆盖检查判别力，不能再作为主创新 |
| [VP-Control](https://arxiv.org/abs/2609.10969) | 成本感知验证组合与独立证据源 | 已覆盖 verifier 组合和共同失效问题 |
| [SymTrace](https://arxiv.org/abs/2608.25920) | 回放多智能体轨迹前缀，只重生成下游 | 目标是调试和修复多智能体失败，不测试 Harness 不变量 |
| [From Prompts to Contracts](https://arxiv.org/abs/2607.08028) | 对企业 Agent 的确定性契约做七类负向控制 | 验证业务契约，不测试控制循环协议 |
| [Sefz](https://arxiv.org/abs/2605.13044) | 把 Skill 规范转成可达性目标并做语义模糊测试 | 测试 Skill 的自然语言约束，不测试 Harness 生命周期 |
| [Harness-MU](https://arxiv.org/abs/2606.21856) | 用执行钩子强制多用户权限和指令优先级 | 研究治理机制，不研究控制器测试 |
| [AgentChaosBench](https://arxiv.org/abs/2608.14680) | 注入十类运行时故障并从 telemetry 诊断位置和类型 | 研究故障诊断，不生成协议感知突变或最小反例 |

除 AgentChaos 已标注为 ASE 2026 论文并有 DOI 外，这批 2026 年工作多数仍是 arXiv
预印本。文献中的实验数字只能作为作者报告的结果，不能写成已经被独立复现的事实。

## 被排除或降级的方向

### 通用 Harness 自进化

AHE、Self-Harness、HarnessX、HarnessBank、MemoHarness、Living-Harness、JIT-Agent 和
MOSS 已覆盖 Prompt、工具、Skill、记忆、工作流、源码和模型协同优化。继续做“根据失败轨迹
改 Harness”容易只得到工程增量。

### 连续证据衰减

确定性工作区中的证据是否有效，取决于依赖对象是否发生变化，不取决于经过了多少分钟。
时间衰减缺少物理依据，也会把未变化的有效证据错误降权。`work_epoch` 可以继续改进为依赖
感知失效，但不宜把时间衰减作为论文主创新。

### 一般性的检查判别力

BSG-VA 已在 buggy、candidate 和 gold 三种状态上衡量验证命令是否真正区分缺陷。
TDAD 也用 Prompt 语义突变评估测试套件。Evidence Harness 可以把它们作为基线或辅助
实验，不能再把“检查能否识别错误候选”作为唯一贡献。

### 多 verifier 投票

VP-Control 已把模型多样性与证据源多样性分开，并报告独立证据源比换 verifier 模型更重要。
再做简单多数投票或多 reviewer 组合，创新空间有限。

### 通用故障注入与回放

AgentChaos 覆盖 LLM API 故障，AgentCheck 覆盖工具响应故障，AgentChaosBench 覆盖运行时
组件故障，SymTrace 覆盖多智能体失败回放。新工作必须进入它们没有建模的层次，即 Harness
控制协议的合法事件与状态不变量。

## 推荐主线的研究空白

Agent-Reactive Bugs 给出了最直接的研究动机：一些缺陷只在特定模型响应触发 Harness
异常反应时出现，而且模型随机性使缺陷难以复现。AgentChaos 通过 HTTP 包装器固定 API
故障，AgentEval 通过前置路径到达对话边界，SymTrace 通过锚点回放多智能体前缀。这三类
方法仍没有回答下面的问题：

> 当模型返回的数据完全符合 schema，但其含义与 Harness 当前状态冲突时，如何系统生成、
> 重放并缩减能够破坏完成控制不变量的测试？

这里的“结构合法、语义危险”很重要。空响应、截断 JSON 和字段缺失通常在解析边界就被拒绝，
无法进入 `finish`、reviewer 配额、证据 epoch、finalization 或恢复计数等深层状态。真正
难测的缺陷来自看起来正常的决策：

- 检查还没有执行，模型却提出完成。
- review 配额已经用完，模型再次提交相同证据。
- 候选代码修改后，模型复用旧 epoch 的成功回执。
- 隔离检查使用另一个 candidate image，但回执仍然成功。
- 一条失败的 change 命令被描述为进展。
- 普通工作命令在 finalization 边界前启动，却耗尽保留时间。
- 恢复后重复执行先前已经成功的不可逆副作用。

这些事件只有与当前状态组合后才构成错误，因此需要状态前缀和协议前置条件。

## 候选路线评分

评分采用 5 分制。综合分的权重为创新性 35%、实施成本 25%、项目复用度 25%、答辩风险
15%。实施成本分越高表示越容易完成，答辩风险分越高表示风险越低。

| 方向 | 创新性 | 实施成本 | 项目复用度 | 答辩风险 | 综合分 |
|---|---:|---:|---:|---:|---:|
| 协议感知有状态突变测试 | 4.5 | 4.5 | 5.0 | 4.0 | 4.55 |
| 隔离完成验证系统 | 3.0 | 4.5 | 5.0 | 3.5 | 4.00 |
| 检查的反事实判别力 | 2.5 | 4.0 | 4.5 | 2.5 | 3.43 |
| Harness 迁移收益预测 | 4.5 | 2.0 | 3.0 | 2.5 | 3.18 |
| 通用 Harness 自进化 | 2.0 | 3.0 | 4.0 | 2.0 | 2.85 |

协议感知突变测试适合作为主线，因为项目已经有状态机、严格协议类型、journal、真实缺陷、
隔离验证和外部评分。Harness 迁移收益预测的新颖性也较高，但需要 model × harness × task
的大矩阵，API 费用、重复实验和统计功效都更难控制，适合作为备选。

## 研究问题与假设

### RQ1：能否复现真实 Harness 控制缺陷

问题：状态感知突变能否在修复前版本上稳定复现历史缺陷，并在修复后版本上被拒绝？

假设 H1：七类历史缺陷中，至少六类能由确定性状态前缀和协议突变复现。修复后版本对同一
测试的违规率显著下降。

主要指标是历史缺陷复现率、修复确认率和重复运行一致率。

### RQ2：状态信息是否提高有效测试比例

问题：与随机 JSON 突变、AgentChaos 风格 API 故障和无状态语义突变相比，状态感知突变
能否触达更多深层控制状态并发现更多独立不变量违规？

假设 H2：状态感知方法产生的 schema-valid mutation 比例、目标状态到达率和独立不变量
违规数都高于三类基线。

### RQ3：现有测试套件能拦住多少协议突变

问题：当前 220 项测试能够杀死多少协议突变，哪些突变仍然存活？

假设 H3：现有测试对已修复缺陷的同类突变得分较高，但跨 attempt 证据复用、恢复幂等性和
组合预算边界仍会出现存活突变。

### RQ4：前缀回放和缩减是否降低复现成本

问题：与真实模型重跑和完整轨迹回放相比，状态前缀回放与反例缩减能否减少时间、模型调用和
复现步骤？

假设 H4：状态前缀回放不需要模型再次生成触发响应，并能把失败案例缩减为更短的事件序列和
更小的突变载荷。

### RQ5：方法能否迁移到另一个开源 Harness

问题：突变模型和不变量分类能否迁移到 mini-swe-agent，还是只能测试 Evidence Harness？

假设 H5：数据字段需要适配，但完成、证据新鲜度、预算和恢复这四类不变量可以复用。

RQ5 是外部有效性实验，不要求把全部算子迁移。硕士论文的最低范围是迁移四类不变量和至少
三类突变。

## 方法设计

### 把 Harness 建模为带标签的状态转换系统

定义 Harness：

\[
H = (S, E, \delta, I)
\]

其中，\(S\) 是控制状态，\(E\) 是进入控制器的事件，\(\delta\) 是状态转换函数，\(I\)
是不变量集合。Evidence Harness 的状态至少包含：

\[
s = \langle phase, epoch, attempt, budget, receipts, reviewer,
candidate, effects \rangle
\]

一次执行轨迹表示为：

\[
\tau = s_0 \xrightarrow{e_1} s_1 \xrightarrow{e_2} \cdots
\xrightarrow{e_n} s_n
\]

突变算子只在满足前置条件 \(P_m(s_t, e_t)\) 时生效，并把事件 \(e_t\) 替换成结构合法的
\(e'_t\)：

\[
m(s_t, e_t) = e'_t
\]

运行突变轨迹后，只要存在 \(I_k(s_i)=false\)，系统就记录一个控制缺陷。如果 Harness
拒绝突变或恢复到满足全部不变量的状态，该突变被杀死。

### 状态前缀回放

状态前缀回放分为四步：

1. 从 journal 读取 task、模型决策、命令回执、epoch、attempt 和预算事件。
2. 重放到目标状态，但不重新调用真实模型。
3. 在 `ModelGateway.decide`、`ModelGateway.review`、命令回执或隔离证据边界替换一个事件。
4. 继续运行控制器，并让环境访问使用记录值、隔离 fixture 或受控 stub。

每个前缀必须绑定以下来源信息：

- Harness commit。
- 原始 journal SHA256。
- 前缀结束事件序号。
- 候选镜像或工作区摘要。
- 突变算子和参数。
- oracle 版本。

这些字段可以避免测试结果与错误源码、错误候选或错误轨迹混合。

### 协议不变量

| 编号 | 不变量 | 失败表现 |
|---|---|---|
| I1 | `VERIFIED` 必须绑定当前 `work_epoch` | 修改候选后复用旧检查 |
| I2 | `VERIFIED` 必须包含完整、按序且成功的检查回执 | 缺检查、乱序或失败仍完成 |
| I3 | 启用 reviewer 时必须有当前 attempt 的接受结果 | reviewer 缺失、超时或配额耗尽后旁路 |
| I4 | 完成证据必须绑定同一 attempt 和 candidate image | 跨 attempt 或跨候选复用证据 |
| I5 | 完成隔离期间 live candidate 必须保持暂停且不变 | 检查与工作副本发生竞态 |
| I6 | 普通工作不能消耗 finalization reserve | 最后阶段没有完成与修复机会 |
| I7 | 失败命令不能被计为有效进展 | 停滞检测被错误重置 |
| I8 | repair 次数不能超过 `max_repairs` | off-by-one 或重复修复 |
| I9 | 重试与恢复不能重复不可逆副作用 | 恢复后重复写入或提交 |
| I10 | 全局 command sequence 必须单调且无重复 | 回执顺序和审计链断裂 |

I1 至 I8 可以直接映射到当前源码。I9 需要引入副作用账本，I10 可用现有 sequence 字段
实现。论文可以把 I1 至 I8 作为核心范围，把 I9 和 I10 作为扩展。

### 突变算子

| 突变族 | 代表算子 | 状态前置条件 | 目标不变量 |
|---|---|---|---|
| 结构基线 | 空响应、截断、字段缺失、schema 错误 | 任意模型调用 | 解析边界基线 |
| 动作语义 | 把 `observe` 声明成 `change`、重排命令、重复副作用 | 已有可执行决策 | I7、I9、I10 |
| 完成协议 | 提前 `finish`、缺 coverage、重复或乱序检查 | 尚未形成完整证据 | I2、I3 |
| 证据绑定 | 旧 epoch、错误 attempt、错误 candidate digest | 已有历史成功证据 | I1、I4 |
| reviewer | 缺失、超时、旧接受结果复用、错误接受 | 已进入 completion review | I3、I4 |
| 预算 | 长命令跨边界、repair off-by-one、普通工作占用保留调用 | 接近 turn 或墙钟边界 | I6、I8 |
| 恢复 | 失败回执重置停滞、恢复后重复成功副作用 | 已有失败或恢复事件 | I7、I9 |

结构基线只用于与 AgentChaos 风格方法比较，不是主要贡献。论文的主要结果必须来自后六类
状态相关突变。

### 突变结果分类

每次运行输出四种结果：

- `killed`：Harness 拒绝突变，或恢复后仍满足全部不变量。
- `survived`：Harness 接受了危险事件，或结束于不一致状态。
- `invalid`：突变不满足算子前置条件，测试没有到达目标状态。
- `equivalent`：突变改变了表示，但在当前状态下不改变可观察行为。

突变得分为：

\[
MS = \frac{|killed|}{|generated|-|invalid|-|equivalent|}
\]

`invalid` 和 `equivalent` 必须单独报告。把它们从分母删除但不公开数量，会高估方法效果。

### 反例缩减

缩减器分两层工作：

1. 对状态前缀做层次化 delta debugging，删除不影响违规复现的事件批次和单个事件。
2. 对突变载荷做字段级缩减，删除无关 rationale、plan item、command 或 coverage。

缩减必须保持三个条件：

- 目标状态仍然可达。
- 相同不变量仍然失败。
- 失败在至少三次确定性回放中一致。

最终反例包含最短必要前缀、最小突变载荷、首个不变量失败点和修复前后差异。

## 原型结构

建议新增独立包，不把实验逻辑散入生产状态机：

```text
src/evidence_harness_mutation/
  model.py          # StatePrefix、Mutation、Outcome
  journal_loader.py # journal 到可回放事件
  replay.py         # 状态前缀恢复和事件替换
  operators.py      # 类型化突变算子注册表
  invariants.py     # 确定性 oracle
  reducer.py        # 轨迹和载荷缩减
  campaign.py       # 实验编排、种子和资源限制
  report.py         # JSON、CSV 和 Markdown 结果
```

生产包只需要暴露受控测试入口。不要为了实验重写 `EvidenceLoop`。

核心接口可以采用如下形式：

```python
@dataclass(frozen=True, slots=True)
class StatePrefix:
    source_commit: str
    journal_sha256: str
    through_sequence: int
    events: tuple[RecordedEvent, ...]


class MutationOperator(Protocol):
    id: str

    def applicable(self, state: RunState, event: RecordedEvent) -> bool: ...

    def mutate(self, state: RunState, event: RecordedEvent) -> RecordedEvent: ...


class Invariant(Protocol):
    id: str

    def evaluate(self, trace: ReplayTrace) -> InvariantResult: ...
```

突变定义应使用结构化数据，不使用字符串替换：

```yaml
id: stale-evidence-after-change
target: verification_receipt
precondition:
  phase: finalizing
  prior_successful_receipt: true
  candidate_changed_after_receipt: true
mutation:
  work_epoch: previous
expect:
  terminal_outcome: rejected
  invariant: I1
```

## 实验设计

### 实验对象

主实验对象是 Evidence Harness 的三个版本：

- 缺陷版本：每个历史修复 commit 的父版本。
- 修复版本：`5f64d66`、`3a3b83f` 和 `289bea4`。
- 当前版本：实验开始时冻结的 commit。

外部有效性对象选 mini-swe-agent。它的控制循环较小，适合实现薄适配器，也能避免把大部分
时间耗在理解 OpenHands 的完整运行时。OpenHands 只作为时间允许时的扩展。

### 数据集

建议建立三个不重叠的数据集：

| 数据集 | 来源 | 用途 |
|---|---|---|
| Historical-7 | 七类真实历史控制缺陷 | RQ1 修复前后验证 |
| PrefixBench | 新采集的 source-attested schema-2 live trial | RQ2 至 RQ4 |
| CrossHarness | mini-swe-agent 的公开任务和本地轨迹 | RQ5 |

PrefixBench 按 `thinking`、`executing`、`finalizing`、`reviewing` 和 `recovering` 分层。
初始规模可以设为 60 至 100 个前缀，目标产生 400 至 800 个有效突变。前缀和算子参数先在
development split 调整，再冻结 test split。不能根据 test split 的结果继续修改 oracle。

现有 85 个 live trial 只能作为 phase inventory，不能直接组成 PrefixBench。它们全部使用
journal schema 1，没有 canonical journal binding、producer source attestation、
PrefixBench collection profile 或五个显式 phase-entry event。readiness artifact 因此将
89 个任务全部排除，并把状态固定为 `source_cohort_unavailable`。详情见
`docs/prefixbench-design.md` 和 `evaluation/prefixbench-readiness.json`。

task split 已按任务身份冻结，不使用 reward、stop reason 或 journal outcome。固定 SHA-256
分桶得到 28 个 development task 和 61 个 test task，其中 live task 为 27/58。新采集必须
沿用该 task-level split，不能按新运行结果重新分配。

development live collection 已完成。固定的 28 题 cohort 全部通过 source admission，
readiness v2 状态为 `ready`，并覆盖五类显式 phase-entry event；61 题 test cohort 未
运行。`prefixbench-v1` 冻结运行参数，在启动前和 Agent 内部两次核对 Git archive 与
实际 runtime source，并把 producer attestation 写入 schema-2 journal。canonical
schema 2 绑定 journal bytes；readiness v2 分开 source admission 与 development phase
coverage。实现契约见 `docs/prefixbench-collection-design.md`。

development cohort 的确定性离线 mutation campaign 也已完成。每题使用一个完整 journal
prefix，按默认顺序运行四个算子，共保留 168 个 case：77 个
`mutation_not_applicable`、63 个 `oracle_equivalent`、28 个 `offline_violation`，没有
`offline_invalid` 或 `other_oracle_change`。28 个违规都内联 source-anchored 缩减反例。
协议 manifest 绑定算子、oracle、reducer、runner 和依赖源码；重复运行产生完全相同的
canonical bytes。该结果只用于冻结 development 协议，不是 test split 结论。契约见
`docs/prefixbench-mutation-campaign-design.md`。

### 对照方法

| 基线 | 作用 |
|---|---|
| Random-JSON | 衡量不理解 schema 的随机突变有多少能进入控制器 |
| Schema-Valid Random | 衡量只有类型合法但没有状态信息时的效果 |
| AgentChaos-Style | 注入空响应、截断、错误 finish reason 和字段损坏 |
| Stateless Semantic | 使用同一语义算子，但不根据状态选择注入点 |
| Full Method | 状态前缀、协议前置条件、确定性不变量和缩减全部启用 |

### 消融实验

- 去掉状态前置条件，观察 `invalid` 和目标状态到达率。
- 去掉真实前缀，改从初始状态生成完整轨迹。
- 去掉绑定信息，只保留 payload。
- 去掉不变量 oracle，改用终态 pass 或 fail。
- 去掉缩减器，比较复现步骤数和定位时间。
- 分别关闭 reviewer、隔离和 finalization 不变量。

### 指标

检测能力：

- 历史缺陷复现率。
- 修复确认率。
- 独立不变量违规数。
- mutation score。
- 每 100 次执行发现的唯一缺陷数。

输入质量：

- schema-valid mutation 比例。
- 算子前置条件满足率。
- 目标状态到达率。
- `invalid` 和 `equivalent` 比例。

可复现性：

- 三次回放的一致率。
- 不需要真实模型调用的案例比例。
- 环境漂移导致的回放失败率。

缩减效果：

- 事件数量缩减率。
- payload 字段缩减率。
- 首次失败到最小反例的时间。

成本：

- 单个有效突变的墙钟时间。
- 模型调用次数。
- 容器启动次数。
- 与 live rerun 相比节省的 token 和时间。

### 统计方法

同一状态前缀上的方法比较使用配对统计。二元结果采用 McNemar 检验，并报告 bootstrap
置信区间。连续指标先检查分布，再使用 Wilcoxon signed-rank test 和 Cliff's delta。
bootstrap 必须按 task 或状态前缀分组，不能把同一前缀产生的多个突变当作独立样本。

多重比较采用 Holm 校正。除 p 值外，论文必须报告绝对差值、效应量和 95% 置信区间。

## 实验如何避免循环论证

突变算子、不变量和历史缺陷来自同一个项目，容易出现“用已知答案验证已知答案”的质疑。
实验需要四道隔离：

- Historical-7 只验证真实缺陷能否复现，不承担泛化结论。
- PrefixBench 的 test split 在算子和 oracle 冻结后才运行。
- 一部分不变量由状态机设计文档推导，另一部分由历史缺陷推导，结果分开报告。
- mini-swe-agent 适配由公共接口和独立轨迹构建，不复用 Evidence Harness 的字段名。

还要公开所有未激活、等价和无法回放的突变。只展示成功案例会使 mutation score 失去意义。

## 预期贡献

论文可以主张四项贡献：

1. 一套面向 LLM Agent Harness 控制循环的状态化协议故障模型，区分结构故障与依赖当前
   状态才成立的语义故障。
2. 一种基于真实状态前缀、类型化事件替换和确定性不变量的可复现测试方法。
3. 一个自动缩减 Harness 失败轨迹与突变载荷的方法，生成不依赖模型随机复现的最小案例。
4. 一个包含真实历史缺陷、冻结轨迹和跨 Harness 适配的实验基准。

不要把“隔离容器”“`work_epoch`”或“finalization reserve”单独写成学术贡献。它们是支撑
方法的工程机制，也是构造不变量和真实案例的材料。

## 备选路线：预测 Harness 迁移收益

备选题目：

> Predicting Harness Transferability for Terminal Agents

输入是源模型、目标模型、任务族和 Harness 模块，输出是正迁移、负迁移或无显著效果。
低成本 probe 可以测量工具调用格式稳定性、上下文敏感度、验证倾向、预算利用率和恢复行为，
再预测完整 benchmark 的迁移结果。

这个方向来自现有文献的矛盾。Life-Harness、MemoHarness、Living-Harness、SkillOpt、
EvoSkills 和 SkillCommit 报告部分跨模型或跨 Harness 正迁移，HarnessBank 强调模型
特定性，SkillLens 发现 Skill 效用依赖目标模型且存在负迁移。One Recipe, Many Harnesses
发现抽象规则可迁移而生态实现难迁移，EvoHarnessBench 又显示保留与适应可能冲突。

它的主要风险是数据规模。可靠分类至少需要多个模型、多个 Harness 版本和多个任务族，并对
每个单元重复运行。若 API 预算不足，模型会学到任务难度或模型规模，而不是 Harness 与模型
的匹配关系。除非能获得稳定算力和 1000 次以上的受控执行，不建议把它作为主线。

## 八个月计划

| 月份 | 工作 | 验收物 |
|---|---|---|
| 第 1 月 | 冻结题目、综述和术语，定义 I1 至 I8 | 开题报告、文献矩阵、不变量规范 |
| 第 2 月 | 实现 journal loader、状态前缀和确定性 replay | 可重放 10 条历史轨迹 |
| 第 3 月 | 实现突变算子与结果分类 | Historical-7 初步结果 |
| 第 4 月 | 实现不变量 oracle 和反例缩减 | 端到端 mutation campaign |
| 第 5 月 | 构建 PrefixBench，运行基线与消融 | RQ2 至 RQ4 原始数据 |
| 第 6 月 | 适配 mini-swe-agent，完成外部有效性实验 | RQ5 结果 |
| 第 7 月 | 统计分析、误差分析和图表 | 锁定实验章节 |
| 第 8 月 | 写作、预答辩和复现实验 | 论文、代码、数据和答辩材料 |

第 4 月是止损点。如果 Historical-7 不能复现至少五类缺陷，应缩小题目为
“Evidence Harness 完成控制的模型化测试”，不再承诺跨 Harness 通用性。如果状态化方法
与 Schema-Valid Random 的差异很小，应转向备选方向或把贡献改为历史缺陷基准与最小复现。

## 论文目录建议

1. 绪论：Agent Harness 的控制缺陷为什么难测。
2. 背景与相关工作：Harness、自进化、故障注入、状态化测试和验证证据。
3. 问题定义：状态转换系统、协议事件、突变和不变量。
4. 方法：状态前缀回放、突变生成、oracle 和反例缩减。
5. 系统实现：Evidence Harness 集成与跨 Harness 适配层。
6. 实验：历史缺陷、基线、消融、成本和外部有效性。
7. 讨论：等价突变、oracle 完整性、环境漂移和适用边界。
8. 结论。

## 开题口述稿

### 90 秒版本

我的研究直接测试包围模型的 Agent Harness，也就是负责解析模型决策、执行工具、管理预算和
判断完成的控制程序。现有项目在 85 次真实运行中出现了 22 次内部完成判定与外部 verifier
不一致，其中 10 次是 Harness 认为完成但实际失败。

现有研究已经能给 LLM API 注入截断和空响应，也能扰动工具返回、Prompt 和对话状态，但
这类方法很难测试一种更隐蔽的故障：模型返回的数据结构完全合法，却在当前控制状态下违反
协议。比如复用旧证据、在 reviewer 配额耗尽后完成，或让普通命令耗尽最终验证预算。

我准备把 Harness 建模为带不变量的状态转换系统，从真实 journal 恢复到缺陷发生前的状态，
再注入依赖当前状态的语义突变。系统用确定性不变量判断突变是否被正确拒绝，并自动缩减出最小
复现案例。实验会先在七类历史缺陷的修复前后版本上验证，再与随机 JSON、通用 API 故障和
无状态语义突变比较，最后迁移到 mini-swe-agent。这样既能回答方法是否发现真实缺陷，也能
回答状态信息和缩减机制是否真的有用。

### 四段式答辩版本

**实现**，系统读取真实 journal，把 Harness 恢复到指定控制状态。突变器只生成符合协议类型
的事件，不变量引擎检查完成、证据、预算、隔离和恢复属性，缩减器输出最小失败轨迹。

**原理**，Harness 缺陷常由事件与状态的组合触发。只修改一个独立 JSON 响应，通常到不了
review 配额耗尽、旧 epoch 复用或 finalization 边界。状态前缀相当于状态化 API 测试中的
preamble，让测试直接到达目标状态。

**相关工作**，AgentChaos 测 LLM API 故障，AgentCheck 测工具响应，AgentEval 测对话工作流
边界，TDAD 测 Prompt，Sefz 测 Skill 规范，SymTrace 回放多智能体轨迹。本研究测试的是
Harness 控制器对合法但状态冲突事件的反应，并补上确定性不变量和最小反例。

**取舍**，方法不能证明任务语义正确，也不能替代外部 verifier。它只保证已声明的控制不变量。
为了控制硕士论文规模，主实验放在 Evidence Harness，外部系统只迁移核心不变量。这个范围
足以检验通用性，又不会把时间耗在多个大型框架的工程适配上。

## 常见答辩问题

### 这和 AgentChaos 有什么不同

AgentChaos 在通用 HTTP 边界修改 LLM 响应，重点是 crash、omission 和 value fault。
本研究先恢复 Harness 状态，再生成 schema-valid 的协议事件。相同事件在一个状态合法，
在另一个状态可能违反 epoch、attempt、预算或完成顺序。测试 oracle 也不是最终任务分数，
而是控制器不变量。

### 这和 AgentEval 有什么不同

AgentEval 从黑盒对话中挖掘工作流图，回放用户消息到确认或身份验证边界。本研究有白盒 Harness
状态和严格事件类型，测试的是模型决策、回执、reviewer 和隔离证据如何驱动控制器。两者共享
状态前缀思想，但被测对象、事件和 oracle 不同。

### 为什么不用真实模型重复生成异常

模型随机性会把 Harness 修复效果与模型采样差异混在一起。记录并替换触发事件后，同一缺陷可以
在修复前后版本上重复执行。真实模型只用于收集原始轨迹和补充生态有效性，不承担缺陷复现。

### 不变量是不是你自己定义，所以结果一定成立

不变量来自三类来源：公开的 Harness 控制职责、项目设计文档和真实历史缺陷。Historical-7
与冻结 test split 分开，外部 Harness 只使用可映射的不变量。实验还公开无效和等价突变，
避免只保留支持结论的案例。

### 为什么这是一篇论文，不只是补单测

单测固定一个输入和一个预期结果。本研究定义可迁移的状态模型、突变算子、oracle、缩减算法和
比较协议，并衡量状态信息对有效突变率、深层状态覆盖和缺陷发现率的贡献。历史缺陷只是 ground
truth，不是方法本身。

### 最大风险是什么

最大风险是方法只适用于 Evidence Harness。外部 Harness 适配和字段无关的不变量分类用于
检验这个问题。若外部迁移失败，论文必须如实把结论限定为类型化终端 Agent Harness。

## 下一步实施顺序

1. 已完成 `StatePrefix`、`ReplayGateway`、I1 至 I4 和四个结构化算子。
2. 已完成 Historical-7 全部 `7/7` 的修复前后双版本实验。
3. 已完成 source-anchored 反例缩减和确定性 offline campaign。
4. 已完成 PrefixBench readiness census，并冻结 development/test task split。
5. 已完成 producer source attestation、五个显式 phase-entry event 和 28 题 schema-2
   development cohort 采集。
6. 已完成 development cohort 离线 campaign，并冻结算子、oracle、reducer 与 runner
   协议。
7. 按冻结协议运行 PrefixBench test split。
8. 主实验稳定后做 mini-swe-agent 适配。

## 优先精读

1. [Understanding Agent-Reactive Bugs at the Model-Harness Boundary](https://arxiv.org/abs/2607.15684)：
   最直接的研究动机和缺陷分类来源。
2. [AgentChaos](https://arxiv.org/abs/2608.06790)：最强的通用 LLM API 故障注入近邻。
3. [AgentEval](https://arxiv.org/abs/2607.06873)：状态前缀、边界到达和黑盒测试近邻。
4. [TDAD](https://arxiv.org/abs/2603.08806)：语义突变、激活探针和等价突变处理近邻。
5. [BSG-VA](https://arxiv.org/abs/2607.28871)：验证证据重放、反事实判别力和统计设计近邻。

## 证据位置

- 项目成绩与资源：`docs/evaluation-report.md`
- 失败分类与轨迹统计：`docs/failure-analysis.md`
- 完成控制设计：`docs/completion-control-design.md`
- 完成校准：`docs/completion-calibration-design.md`
- 隔离验证设计：`docs/isolated-verification-design.md`
- 隔离实验：`docs/completion-isolation-experiments.md`
- 全量结果：`evaluation/results-89.json`
- 内外部错位案例：`evaluation/completion-disagreements.json`
- 当前控制状态：`src/evidence_harness/protocol.py`
- 当前运行循环：`src/evidence_harness/run_loop.py`
- 当前完成门禁：`src/evidence_harness/evidence.py`
- 当前回放能力：`src/evidence_harness/replay_agent.py`
- 状态化突变原型：`docs/mutation-prototype-design.md`
- 离线 campaign 与缩减契约：`docs/mutation-reduction-design.md`
- PrefixBench readiness 与采集契约：`docs/prefixbench-design.md`
- PrefixBench live collection 实现契约：`docs/prefixbench-collection-design.md`
- PrefixBench readiness 报告：`evaluation/prefixbench-readiness.json`
- PrefixBench development campaign 契约：`docs/prefixbench-mutation-campaign-design.md`
- PrefixBench development campaign 报告：
  `evaluation/prefixbench-v1-development-offline-campaign.json`
