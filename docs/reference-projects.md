# 参考项目与设计取舍

## 调研范围

本文对照用户提供的 Agent 项目和补充资料，回答两个问题：

1. Evidence Harness 从哪些公开设计中吸收了机制？
2. 哪些组合是当前实现的主要差异，哪些能力还没有实现？

对照只引用项目官方仓库、官方文档或论文作者提供的 artifact。本文不做排行榜式优劣
判断。各项目面向的任务不同，功能数量也不能直接代表 Terminal-Bench 通过率。
资料核对日期为 2026-09-24。

## 名称核对

以下项目可以从公开一手资料确认身份：

| 名称 | 官方来源 |
| --- | --- |
| AutoCodeRover | [AutoCodeRoverSG/auto-code-rover](https://github.com/AutoCodeRoverSG/auto-code-rover) |
| Agentless | [OpenAutoCoder/Agentless](https://github.com/OpenAutoCoder/Agentless) |
| Goose | [aaif-goose/goose](https://github.com/aaif-goose/goose) |
| Crush | [charmbracelet/crush](https://github.com/charmbracelet/crush) |
| Plandex | [plandex-ai/plandex](https://github.com/plandex-ai/plandex) |
| OpenDev | [opendev-to/opendev](https://github.com/opendev-to/opendev) |
| ForgeCode | [tailcallhq/forgecode](https://github.com/tailcallhq/forgecode) |
| Meta-Harness | [论文代码](https://github.com/stanford-iris-lab/meta-harness)和 [Terminal-Bench 2.0 artifact](https://github.com/stanford-iris-lab/meta-harness-tbench2-artifact) |

`Terminal Agent`、`OpenLoop` 和 `WolfBench` 缺少仓库或论文链接。公开搜索不能把这些
名称与用户描述的机制唯一对应。本文把 Verify-First、heartbeat、baseline、retry
budget 和五指标评估视为用户提供的设计输入，不把它们写成某个公开项目已经实现的事实。

## 已确认项目对照

| 项目 | 一手资料确认的机制 | 对本项目的影响 | 当前未采用的部分 |
| --- | --- | --- | --- |
| AutoCodeRover | 使用结构感知的代码搜索 API，按 AST 中的方法和类定位上下文。有测试时可做统计故障定位 | 提醒我们避免无目的遍历，并把观察结果作为下一步决策依据 | 没有引入 AST 索引。Terminal-Bench 任务不全是代码仓库 |
| Agentless | 固定执行定位、修复和补丁验证三个阶段，并用测试结果重排候选补丁 | `finish` 被设计成独立阶段，验证失败后返回修复 | 没有固定完整解题流水线，保留动态 Shell 以处理未知任务 |
| Goose | 通过 MCP 扩展连接工具，并支持多个模型供应商 | 保持模型层与环境执行层分离 | Harbor 已提供单一环境接口，首版不增加 MCP 生命周期和权限配置 |
| Crush | 支持多会话、LSP、MCP、权限控制，以及多个客户端共享 workspace 状态 | 强化了会话状态与执行进程应分离的判断 | Benchmark trial 是无头、一次性运行，不需要 TUI、共享 workspace 或常驻服务 |
| Plandex | 用累计 diff review sandbox 隔离 AI 修改，并支持计划版本和分支 | 采用先执行、再审查的思路，所有命令进入 journal | Git diff 无法完整表达进程、服务、软件包和系统配置状态 |
| OpenDev | 把 Execution、Thinking、Compaction、Critique 和 VLM workflow 分开，并允许各自绑定模型 | 保留 executor 与完成时 reviewer 两个职责 | 没有持续 Critic、多模型路由和并发 Agent，减少 token、延迟和共享状态竞争 |
| ForgeCode | 保存 conversation，支持 resume、retry、dump、compact、clone，并提供独立 worktree sandbox | `RunJournal` 和确定性上下文裁剪借鉴了可恢复、可检查的会话设计 | 每个 trial 从新容器开始，不支持跨 trial 恢复、克隆或交互式会话 |
| Meta-Harness | 官方 artifact 报告 Claude Opus 4.6 在 89 题、每题 5 次运行中取得 76.4%。启动前收集环境快照并注入初始 prompt | 实现固定 bootstrap，并保留运行日志和固定评测矩阵供后续迭代 | 没有自动生成、搜索和选择 Harness 候选 |

AutoCodeRover 和 Agentless 的边界最清楚。它们优化软件缺陷修复。Evidence Harness
必须同时处理系统运维、安全、数据处理和代码任务，因此不能把 AST 或 Git diff 当作
统一状态模型。

Goose、Crush、OpenDev 和 ForgeCode 更接近完整开发者产品。它们需要插件生态、多会话、
界面和长期记忆。Evidence Harness 只服务一个隔离 trial，因此选择更少的角色和更小的
运行时。

Meta-Harness 与当前任务最接近。它证明 Harness 本身可以成为优化对象，也提供了
environment bootstrapping 的直接依据。Evidence Harness 已采用固定 bootstrap，但
76.4% 是 Meta-Harness artifact 的报告结果，不是本项目成绩。

## Evidence Harness 的主要差异

### 完成是控制协议

模型不能只声明任务已完成。`finish` 必须包含只读检查和 requirement coverage。
reviewer 先判断检查能否覆盖原始要求，随后 `CommandRunner` 在任务容器中重新执行这些
检查。只有 `EvidenceGate` 能把内部状态设为 `verified`。

这种设计吸收了用户提供的 Verify-First 思路，也保留了 Agentless 的阶段边界。区别是
验证对象不限于代码测试，可以是文件、数据、构建结果或服务状态。

### 证据有版本

每个 `execute` 批次都会推进 `work_epoch` 并清除 `latest_evidence`。检查回执也记录
epoch。旧检查即使成功，也不能证明后续操作后的环境。

在本次查阅的一手文档中，没有发现其他项目公开描述同等粒度的证据版本规则。这个判断
只针对上述资料，不代表其他项目源码中一定没有相似实现。

### 验证预算独立保留

`verification_environment_reserve` 从环境调用总预算中预留最终检查次数。普通工作命令
不能消耗这部分预算。预算控制因此不只负责停止长循环，还保证 Agent 在停止前仍有机会
证明结果。

### 单写者与只读 reviewer

`EvidenceLoop` 是唯一环境写者。completion reviewer 没有 `ShellEnvironment`，只能
检查要求覆盖并建议修复。该结构保留第二次语义判断，同时避免多 Agent 并发修改同一个
容器。

### 动态 Shell 与严格结果并存

AutoCodeRover、Agentless 和 Plandex 都有清晰的代码任务状态模型。Terminal-Bench 的
服务配置和系统管理任务无法只用补丁描述。Evidence Harness 因此保留通用 Shell，但
要求所有动作通过 Pydantic 协议，并记录命令模式、退出码、输出哈希和失败类别。

### 内部验证不等于 benchmark 评分

内部 `verified` 只说明完成检查通过。Harbor verifier 仍是最终评分者。Harness 禁止
读取隐藏 verifier 和 solution，并把 `not_run`、`error` 与 `failed` 分开统计。mock
smoke 只证明集成链路可用，不进入 Terminal-Bench 通过率。

## 明确未做的能力

- 没有 AutoCodeRover 的 AST 和调用关系检索。
- 没有 Plandex 或 ForgeCode 的 Git worktree、diff sandbox 和回滚。
- 没有 Goose 和 Crush 的 MCP 扩展生态。
- 没有 OpenDev 的多模型 workflow 和并发 Agent。
- 没有 Crush 和 ForgeCode 的交互界面、长期会话和跨客户端状态。
- 没有 Meta-Harness 的自动候选生成与搜索循环。

这些能力不是遗漏清单。只有同模型、同任务和同预算的消融实验能证明某项能力值得加入
当前 Harness。

## 补充设计依据

- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
  建议先使用可组合的简单模式，只在收益明确时增加复杂 Agent 结构。
- [Harbor](https://github.com/harbor-framework/harbor) 提供 Agent、环境、trial 和结果的
  运行框架。
- [Terminal-Bench 2.0](https://github.com/harbor-framework/terminal-bench-2) 提供隔离
  终端任务和独立 verifier。当前项目通过 Harbor 运行 `terminal-bench@2.0`。

## 待补来源

如果后续获得 `Terminal Agent`、`OpenLoop` 或 `WolfBench` 的准确链接，应先核对其
README、设计文档和源码，再把用户提供的摘要转成正式项目结论。在此之前，架构文档只把
这些内容作为设计输入。
