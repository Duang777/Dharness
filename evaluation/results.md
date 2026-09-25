# Terminal-Bench 2.0 评测结果

- 数据集: `terminal-bench@2.0`
- 评分完整: `否`
- 所有任务均已尝试: `是`
- 已执行: `10/10`
- 已评分: `9/10`
- 通过 / 失败 / 错误: `9 / 0 / 1`
- 已尝试任务通过率: `90.0%`
- 已评分任务通过率: `100.0%`
- 执行覆盖率: `100.0%`
- 评分覆盖率: `90.0%`

| 任务 | 难度 | 类别 | 模型 | 模式 | 状态 | 奖励 | 停止原因 |
|---|---|---|---|---|---|---:|---|
| overfull-hbox | 简单 | 调试 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 预算耗尽 |
| fix-git | 简单 | 软件工程 | openai/glm-5.3 | 实时 | 通过 | 1 | 已验证 |
| cobol-modernization | 简单 | 软件工程 | openai/glm-5.3 | 实时 | 通过 | 1 | 预算耗尽 |
| log-summary-date-ranges | 中等 | 数据处理 | openai/glm-5.3 | 实时 | 通过 | 1 | 已验证 |
| openssl-selfsigned-cert | 中等 | 安全 | openai/glm-5.3 | 实时 | 通过 | 1 | 已验证 |
| modernize-scientific-stack | 中等 | 科学计算 | 不适用 (日志回放) | 回放 | 通过 | 1 |  |
| qemu-startup | 中等 | 系统管理 | openai/glm-5.3 | 实时 | 错误 | 0 |  |
| cancel-async-tasks | 困难 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| configure-git-webserver | 困难 | 系统管理 | openai/glm-5.3 | 实时 | 通过 | 1 | 预算耗尽 |
| model-extraction-relu-logits | 困难 | 数学 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 预算耗尽 |

> `error` 任务计入已尝试任务通过率。不计入已评分任务通过率。`not_run` 任务不计入这两个通过率。

> `replay` 运行会执行先前记录的 Agent 决策。不会发起新的模型调用。
