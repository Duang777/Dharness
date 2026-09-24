# 后续 10 小时优先级

## 当前起点

固定矩阵已评分 6/10 题。当前快照为 5 题通过、1 题失败，execution coverage 和
scored coverage 均为 60%。5 个结果来自 live 模型调用，1 个结果来自 replay。剩余任务
是 `openssl-selfsigned-cert`、`qemu-startup`、`cancel-async-tasks` 和
`model-extraction-relu-logits`。

## 0 至 1 小时：恢复模型通道

轮换此前暴露的模型凭证。运行一个不接触 benchmark 的最小结构化推理探针，确认端点能
连续返回三次非空响应。任何一次请求停滞或返回空内容时，停止评测并保留诊断记录。

## 1 至 5 小时：补齐四题

使用 `scripts/run_evaluation.py` 逐题运行剩余四题。每题使用独立 job name，并固定
`--debian-https-sources --n-concurrent 1`。模型、prompt 和预算保持不变。每题结束后
检查 Harbor reward、Harness stop reason、模型协议错误和日志脱敏，再开始下一题。

中止或没有 reward 的 trial 记为 `error`。没有形成 trial 的任务保持 `not_run`，不能
记为 verifier 失败。

## 5 至 6 小时：更新矩阵快照

把新增 trial 与当前六题结果放入一个无重复任务的聚合目录。运行
`summarize_results.py` 重写 `evaluation/results.json` 和 `evaluation/results.md`。
逐题核对模式、reward、状态和原始 Harbor 结果。

## 6 至 8 小时：分析失败

先分析 `overfull-hbox`。该 trial 同时存在三次空模型响应和 reward 0，不能直接归因于
模型解题能力。按模型输出、环境回执、evidence gate 和 verifier 顺序还原因果链。只有
稳定端点上的同配置证据支持某个 Harness 根因时，才修改代码。

若新增任务失败，分别归类为模型能力、Harness 设计或评测基础设施。每次只验证一个假设，
并至少重跑一题已通过任务作为回归。

## 8 至 9.5 小时：回归与质量门禁

运行 Ruff、全量 pytest、覆盖率、类型检查、build、Agent schema 和本地 Docker smoke。
审查 journal 是否含凭证，确认评测汇总与 Harbor 原始 reward 一致，检查 README 命令
可以从干净环境复现。

## 9.5 至 10 小时：发布

冻结结果和失败分析，提交并推送。最终报告分别给出 live、replay 和组合快照。仍有缺失
trial 时继续报告 execution coverage 和 scored coverage，不把 `error` 或 `not_run`
算作 verifier 失败。
