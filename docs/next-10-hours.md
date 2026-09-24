# 后续 10 小时优先级

## 0-1 小时：恢复真实模型通道

优先级 P0。配置一个明确支持 LiteLLM 的有效供应商 API key，用最小 JSON 请求验证模型
名、认证、结构化输出和限流。成功标准不是“环境变量存在”，而是模型返回可被
`AgentDecision` 解析的对象。失败则停在这里，不消耗 Docker 和 benchmark 时间。

## 1-2 小时：两题校准

先跑 `fix-git`（easy）和 `log-summary-date-ranges`（medium），并发设为 1。逐条检查
Harbor reward、Harness stop reason、fresh evidence、模型协议错误和日志脱敏。若任一题
是 infrastructure/model-service error，先修运行环境；若 verifier 为 0，再分析解题
轨迹。

## 2-5 小时：执行固定 10 题

通过 `scripts/run_evaluation.py` 跑完整矩阵，并发最多 2，禁止中途更换模型、prompt、
预算或任务。保存原始 Harbor job 目录，随后用 `summarize_results.py` 生成 JSON 和
Markdown。这个阶段只收集基线，不边跑边调参。

## 5-7 小时：任务级失败分析

选择最有代表性的 2-3 个失败，按“环境事实 -> 模型计划 -> 命令回执 -> evidence gate
-> verifier”还原因果链。每项明确归类：

- 模型能力：知识、推理、命令构造或错误理解不足。
- Harness 设计：上下文投影、预算、恢复、策略或完成门禁造成。
- 评测基础设施：认证、镜像、网络、超时或 verifier 异常。

只修可由证据支持的根因，不根据最终 reward 反推故事。

## 7-8.5 小时：单变量改进与消融

优先比较 completion reviewer 开/关，其次评估 `recent_observation_count` 或命令批大小。
保持模型、任务、随机条件和预算一致。每次只改一个变量，至少重跑失败题和一题已通过
回归，避免把模型方差误认为架构收益。

## 8.5-9.5 小时：回归与质量门禁

运行 Ruff、全量 pytest、覆盖率、类型检查、build、Agent schema 和本地 Docker smoke。
审查 journal 是否含凭证，确认评测汇总与 Harbor 原始 reward 一致，检查 README 命令
可以从干净环境复现。

## 9.5-10 小时：发布

冻结结果和失败分析，创建本地 commit。确认 GitHub 仓库名称和可见性后再创建远端并
push。最终报告只引用真实结果：完整运行给出总通过率；仍有缺失 trial 则同时报告
execution coverage，不把缺失项算作 0 分。
