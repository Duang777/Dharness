# 论文方案：Harness 自改进中的验证侵蚀及其守护

更新日期：2026-10-31

## 题目与主张

拟定题目：**终端智能体 Harness 自改进中的验证侵蚀：测量与可执行守护**

英文题目：Verification Erosion in Harness Self-Improvement for Terminal Agents: Measurement and Executable Guarding

LLM 在递归修改 Harness 代码时，会提出削弱完成与验证逻辑的修改。这类修改可能提高搜索集分数，却不提高真实任务正确性。本文测量这种侵蚀，并提出一组可执行、确定性的 gate，在修改被接受之前检查候选 Harness 是否仍满足完成协议。

本文不训练模型。Dharness 是被修改的 Harness，也是协议的参考实现。

## 与最近邻工作的差异

| 工作 | 已覆盖 | 本文的差异 |
|---|---|---|
| Masked Erosion (2610.38392) | 自改进中冗余安全规则被删除，消融式 gate | 本文守护代码级的完成与验证逻辑，测量虚假能力提升，不测量危害 |
| Self-Healing Harness (2609.24130) | 重放失败用例和受保护用例的准入控制 | 本文的修改对象是代码，检查对象是协议性质，并注入状态故障 |
| Falsifiable Release Gates (2607.13070) | 常驻不变量，TLA+ 规约 | 本文的不变量是终端完成协议，修改由 RSI proposer 产生 |
| RegenHarness (2609.27612) | 证据门控的递归自改进，机器人 | 本文面向终端任务，gate 由性质测试、轨迹故障注入和缺陷库组成 |
| SEAL (2607.24300) | verifier 与 deployment 的差距 | 本文在 Harness 层面测量内部完成信号与外部评分的差距 |
| Auditing Harness Tampering (2609.00069) | LLM 审计员事后分类 | 本文在接受前用可执行检查拦截，LLM 审计员作为基线 |
| Bounded Loops (2609.27871) | 手工证明终止性与 fail-closed DONE | 本文不重复证明，测量自改进是否保持这些性质 |

不能声称的内容：首个带 gate 的自改进，首个提出自评与外评差距，完成协议本身是新机制。

待核验：Kumar et al. "Gate presence is not a safety invariant"（NeurIPS 2026 workshop）尚未找到原文，定题前必须读完。

## 研究问题

| RQ | 问题 | 主要证据 |
|---|---|---|
| RQ1 侵蚀现象 | RSI proposer 提出的修改中，有多少削弱完成与验证逻辑？被接受的比例和在谱系中的存续如何？ | 公开 RSI 历史（Meta-Harness evo.js，DGM 日志），本文 proposer 采样 |
| RQ2 离线检测 | 可执行 gate 能否拒绝侵蚀修改并接受良性修改？与 LLM 审计员和任务回归 gate 相比如何？ | 种子语料（开发集）和 proposer 自然修改（留出集） |
| RQ3 在线影响 | 在小规模 RSI 循环中，gate 是否减少虚假提升，同时不损失留出任务表现？ | TB2 子集上的配对 RSI 运行 |
| RQ4 组成与成本 | 每类 gate 拦截哪类侵蚀？gate 的时间成本是多少？ | 单组件消融和计时 |

## 威胁模型与可编辑面

候选修改只能触及 `src/evidence_harness/**`。以下路径属于冻结的 gate 面，触及即判定 `out_of_scope`：`tests/`、`experiments/`、`scripts/`、`src/evidence_harness_mutation/`、`pyproject.toml`、`uv.lock`。

这条边界对应 Bounded Loops 和 Falsifiable Release Gates 的要求：被优化的对象不能改写判定它的检查。

G1 针对的是非对抗的侵蚀：proposer 为了提分而削弱验证，但不主动攻击 gate。候选代码和 pytest 运行在同一进程中，对抗性的候选可以篡改测试报告。抵抗这类篡改需要把探针放进沙箱，并从沙箱外读取结果，这一点作为后续阶段的范围，论文中明确写为限制。候选子进程只继承白名单环境变量，看不到模型凭据。

## Gate 组成

| Gate | 内容 | 现有基础 |
|---|---|---|
| G1 协议性质套件 | 冻结的 pytest 用例，按 freshness、coverage、fail_closed、review、budget、isolation 分类，在候选代码上运行 | 现有单元测试，`experiments/erosion-v1/guard-suite-v1.json` |
| G2 轨迹故障注入 | 候选 Harness 在脚本化场景中运行，产生 journal，再用 PrefixBench 不变量和突变算子检查 | `control_operators.py`、`control_invariants.py`、`invariants.py` |
| G3 历史缺陷库 | Historical-7 移植到当前 API，候选必须保持 `killed` | `historical.py`、`scripts/historical_mutation_probe.py` |

基线 gate：只看搜索集分数；任务回归（重跑已通过任务，Self-Harness 式）；LLM 审计员（Tampering 式）。

## 数据

- **E1 种子语料（开发集）。** 作者编写的侵蚀修改和良性修改，侵蚀类别取自 Meta-Harness 公开历史中观察到的模式：删除验证步骤，限制验证次数，按轮数强制完成，快速检查即提交，信任自报告，审查超时放行，复用旧回执。只用于开发 gate。
- **E2 自然修改（留出集）。** 用 Meta-Harness 风格的 proposer 提示，让 LLM 根据失败轨迹修改 Dharness。两名标注者在看不到 gate 结果时独立标注是否侵蚀。gate 在标注冻结后运行。
- **E3 公开历史。** Meta-Harness `evo.js`（19 个候选，已下载），DGM 和 HyperAgents 的 Google Drive 日志（尚未确认可下载）。

## 指标

- 侵蚀修改拒绝率，良性修改接受率，按侵蚀类别分别报告，以及 95% 区间。
- 虚假提升：搜索集上内部 `verified` 率的变化减去外部 reward 的变化。
- 留出任务上的外部 reward 变化。
- gate 单次运行时间。

## 实施阶段

每个阶段结束于一个可检查的状态。勾选需要对应的文件、测试运行或产物。

- [x] **P1 候选 gate 骨架与 G1。** 输入一个相对基线提交的补丁，输出 `GateVerdict`。证据：`tests/mutation/test_candidate_gate.py` 9 项通过；空补丁得到 `accept`，25 个探针全部 held，用时 3.2 秒；删除 `work_epoch` 检查的补丁得到 `reject`，`violated_properties` 为 `freshness`，用时 3.4 秒。实现：`src/evidence_harness_mutation/candidate_gate.py`、`scripts/harness_gate.py`、`experiments/erosion-v1/guard-suite-v1.json`。
- [ ] **P2 E1 种子语料 v0 与离线战役。** `experiments/erosion-v1/seeded/` 下每类至少两个侵蚀补丁和等量良性补丁，脚本输出 `evaluation/erosion-v1-seeded-gate-results.json`。证据：结果可重建且字节稳定。
- [ ] **P3 G3 Historical-7 移植。** 当前 API 的探针 worker，HEAD 上 7 例全部 `killed`。证据：测试和一次完整运行日志。
- [ ] **P4 G2 轨迹一致性探针。** 候选在脚本化场景中运行并产生 journal，用离线不变量和突变算子检查。证据：至少一个 G1 漏掉而 G2 拦住的种子补丁，或记录未出现。
- [ ] **P5 E3 公开历史审计。** 对 evo.js 和 DGM 日志按侵蚀分类标注。证据：`evaluation/erosion-v1-public-audit.json`。
- [ ] **P6 E2 自然修改采样与标注协议。** 需要模型调用。证据：冻结的标注协议、采样提示和标注结果。
- [ ] **P7 基线 gate。** LLM 审计员和任务回归。证据：在 E1 和 E2 上的配对结果。
- [ ] **P8 在线 RSI 小循环。** 15 道搜索题和 15 道互不相交的留出题，10 轮，每轮 2 个候选，比较只看分数和加 gate 两种选择规则。先做 pilot 估算成本。证据：两组运行的 canonical 结果。
- [ ] **P9 统计、表格与论文。** 所有表格由脚本从 canonical JSON 生成。

P1 到 P5 不需要模型调用。P6 到 P8 需要预算和导师确认。

## 与原开题的关系

原开题中的完成协议、PrefixBench 和 Historical-7 都保留，但角色改变：它们成为 gate 的组成部分，不再单独作为创新点。原主实验 RQ1 至 RQ4 的冻结协议不修改。新研究问题使用独立协议，产物写入 `experiments/erosion-v1/` 和 `evaluation/erosion-v1-*`。

## 风险

- **领域变化快。** 每个阶段结束时重新检索一次最近邻。
- **gate 与种子语料由同一作者编写。** E2 留出集和独立标注用来缓解；E1 结果只作开发证据。
- **G1 测试可能过严。** 良性修改如果改了被测试断言的提示文字，会被误拒。良性接受率专门测量这一点。
- **在线实验成本未知。** P8 之前必须先做 pilot。
- **Kumar et al. 可能高度重合。** 找到原文前不定稿创新表述。
