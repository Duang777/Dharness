# 文档索引

`docs/` 保存 Evidence Harness 的研究设计、系统决策、实验协议、结果分析和工程记录。文档按用途分组，不按创建时间排列。

## 先读这几份

| 目的 | 文档 |
| --- | --- |
| 了解论文研究路线和研究空白 | [`agent-harness-research-roadmap.md`](agent-harness-research-roadmap.md) |
| 了解论文主实验如何分阶段执行 | [`thesis-execution-plan.md`](thesis-execution-plan.md) |
| 查看主实验的固定输入、方法、统计和结论规则 | [`thesis-main-experiment-spec.md`](thesis-main-experiment-spec.md) |
| 查看论文当前定位、核心贡献和投稿门槛 | [`thesis-positioning-and-contribution-plan.md`](thesis-positioning-and-contribution-plan.md) |
| 了解系统架构和关键取舍 | [`architecture-rationale.md`](architecture-rationale.md) |
| 查看当前 Terminal-Bench 结果 | [`evaluation-report.md`](evaluation-report.md) |

## 研究路线与论文定位

- [`agent-harness-research-roadmap.md`](agent-harness-research-roadmap.md)：研究空白、候选路线、研究问题和总体实施顺序。
- [`thesis-positioning-and-contribution-plan.md`](thesis-positioning-and-contribution-plan.md)：硕士论文与 B 类会议的定位、贡献层次、证据要求和完成门槛。
- [`thesis-execution-plan.md`](thesis-execution-plan.md)：主实验的阶段、前置条件、输出和验收规则。
- [`thesis-main-experiment-spec.md`](thesis-main-experiment-spec.md)：主实验的 preregistration 合同，包括 RQ1 至 RQ4、分母、统计和 non-read gate。
- [`thesis-reproduction-checker-design.md`](thesis-reproduction-checker-design.md)：论文复现包和产物检查规则。
- [`thesis-tables-design.md`](thesis-tables-design.md)：论文表格的输入、生成和检查规则。

## 系统设计与架构决策

- [`architecture-rationale.md`](architecture-rationale.md)：Evidence Harness 的边界、状态机、证据门禁、隔离检查和取舍。
- [`completion-control-design.md`](completion-control-design.md)：receipt-aware completion control 的设计。
- [`completion-calibration-design.md`](completion-calibration-design.md)：内部完成信号与外部 reward 的校准语料和分析。
- [`isolated-verification-design.md`](isolated-verification-design.md)：隔离 completion check 的运行边界和接受条件。
- [`isolated-verification-runtime-research.md`](isolated-verification-runtime-research.md)：Harbor 0.23.0 和 Docker 隔离能力的调研。
- [`wall-time-finalization-research.md`](wall-time-finalization-research.md)：墙钟预算和 finalization 阶段的设计依据。

## Mutation 与 PrefixBench 实验

- [`mutation-prototype-design.md`](mutation-prototype-design.md)：有状态突变原型。
- [`mutation-reduction-design.md`](mutation-reduction-design.md)：离线突变 campaign 和反例缩减。
- [`prefixbench-design.md`](prefixbench-design.md)：PrefixBench readiness、任务分层和冻结规则。
- [`prefixbench-collection-design.md`](prefixbench-collection-design.md)：PrefixBench live collection 和 source attestation。
- [`prefixbench-mutation-campaign-design.md`](prefixbench-mutation-campaign-design.md)：development offline campaign。
- [`prefixbench-test-mutation-campaign-design.md`](prefixbench-test-mutation-campaign-design.md)：held-out test campaign。
- [`prefixbench-analysis-design.md`](prefixbench-analysis-design.md)：development analysis 的指标和主张边界。
- [`main-analysis-executable-design.md`](main-analysis-executable-design.md)：RQ2、RQ3、RQ4 的可执行分析实现。

## 评测、失败分析与结果

- [`evaluation-report.md`](evaluation-report.md)：Terminal-Bench 2.0 的 89 题结果、资源和分层分析。
- [`failure-analysis.md`](failure-analysis.md)：30 个失败任务、10 个内部假阳性和预算边界分析。
- [`completion-isolation-experiments.md`](completion-isolation-experiments.md)：completion isolation 实验。
- [`completion-isolation-support.md`](completion-isolation-support.md)：89 题隔离支持 census。
- [`ten-task-analysis.md`](ten-task-analysis.md)：首批 10 题逐题分析。
- [`expanded-ten-analysis.md`](expanded-ten-analysis.md)：新增 10 题逐题分析。
- [`historical-7-design.md`](historical-7-design.md)：Historical-7 真实缺陷复现设计。

## 背景调研与工程记录

- [`terminal-agent-research.md`](terminal-agent-research.md)：Terminal-Agent Harness 相关机制调研。
- [`terminal-agent-isolation-research.md`](terminal-agent-isolation-research.md)：终端 Agent 隔离和结项机制调研。
- [`completion-calibration-research.md`](completion-calibration-research.md)：完成验证和校准的相关实现调研。
- [`tb21-harbor-runtime-design.md`](tb21-harbor-runtime-design.md)：Terminal-Bench 2.1 Harbor 运行时兼容设计。
- [`tb21-sensitivity-protocol-design.md`](tb21-sensitivity-protocol-design.md)：TB2.1 敏感性分析协议。
- [`tb21-sensitivity-executable-design.md`](tb21-sensitivity-executable-design.md)：TB2.1 敏感性分析的可执行实现。
- [`vibe-coding-log.md`](vibe-coding-log.md)：AI Coding 工程日志、决策和人工纠偏记录。
- [`next-10-hours.md`](next-10-hours.md)：上一阶段的短期执行清单，历史记录，不替代主实验计划。

## 当前推荐阅读顺序

如果你要向导师解释论文主线，按下面的顺序阅读：

1. [`agent-harness-research-roadmap.md`](agent-harness-research-roadmap.md)
2. [`thesis-positioning-and-contribution-plan.md`](thesis-positioning-and-contribution-plan.md)
3. [`architecture-rationale.md`](architecture-rationale.md)
4. [`thesis-main-experiment-spec.md`](thesis-main-experiment-spec.md)
5. [`thesis-execution-plan.md`](thesis-execution-plan.md)
6. [`evaluation-report.md`](evaluation-report.md)
7. [`failure-analysis.md`](failure-analysis.md)

如果你要实际运行论文主实验，先读 `thesis-main-experiment-spec.md`，再读 `thesis-execution-plan.md`。执行顺序和统计规则以这两份文档为准。
