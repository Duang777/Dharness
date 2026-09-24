# Terminal-Bench 2.0 评测报告

## 结论

截至 2026-09-24，固定 10 题矩阵已完成 Harbor 元数据预检，但真实模型评测尚未启动。
当前机器没有可用的 LiteLLM API key：环境中未发现支持的供应商变量；Claude Code
OAuth 推理探针超过两分钟没有响应；Codex CLI 的真实推理请求返回 401。为避免把认证
问题伪装成模型或 Harness 失败，本报告将十题全部标记为 `not_run`。

- 数据集：`terminal-bench@2.0`，官方 registry 显示 89 题
- 固定样本：10 题，3 easy + 4 medium + 3 hard
- Harbor dry-run：通过，解析为 10 trials
- 已执行真实 benchmark trials：0/10
- 总通过率：N/A
- 真实 benchmark 失败数：0
- 基础设施阻塞：模型认证/可用性

`N/A` 与 `0%` 含义不同。前者表示没有有效分母，后者表示任务真实执行后全部被 verifier
判定失败。当前只能报告前者。

## 逐题状态

| Task | Difficulty | Category | Status | Reward |
|---|---|---|---|---:|
| `overfull-hbox` | easy | debugging | not_run | N/A |
| `fix-git` | easy | software-engineering | not_run | N/A |
| `cobol-modernization` | easy | software-engineering | not_run | N/A |
| `log-summary-date-ranges` | medium | data-processing | not_run | N/A |
| `openssl-selfsigned-cert` | medium | security | not_run | N/A |
| `modernize-scientific-stack` | medium | scientific-computing | not_run | N/A |
| `qemu-startup` | medium | system-administration | not_run | N/A |
| `cancel-async-tasks` | hard | software-engineering | not_run | N/A |
| `configure-git-webserver` | hard | system-administration | not_run | N/A |
| `model-extraction-relu-logits` | hard | mathematics | not_run | N/A |

矩阵位于 [`evaluation/matrix.json`](../evaluation/matrix.json)。选样只使用公开
`task.toml` 中的难度、类别和标签，没有读取 verifier 或 solution。

## 已完成的真实链路验证

本地 `hello-world` fixture 使用真实 Harbor 0.23.0、Docker 环境、自定义
`EvidenceHarnessAgent`、OpenAI 兼容 mock model 和 shell verifier 完成端到端 smoke：

- Harbor reward：1.0
- Executor turns：2
- Completion reviewer：1 次
- Environment calls：3
- Work epoch：1
- Fresh evidence：accepted

该结果只证明适配层、控制循环、日志、验证门禁和 Harbor verifier 能连通，不代表
Terminal-Bench 2.0 成绩，也不进入十题通过率。

## 可复现命令

仅预检：

```bash
uv run python scripts/run_evaluation.py \
  --model openai/gpt-4.1 \
  --dry-run \
  --job-name preflight-2026-09-24
```

本次输出：

```text
Dry run OK - 10 trial(s); nothing was run.
```

取得有效供应商凭证后运行：

```bash
uv run python scripts/run_evaluation.py \
  --model provider/model \
  --env-file /absolute/path/to/provider.env
```

生成逐题和总分报告：

```bash
uv run python scripts/summarize_results.py runs/terminal-bench-2 \
  --json-out evaluation/results.json \
  --markdown-out evaluation/results.md
```

汇总规则为：reward 等于 1.0 记 `passed`；已运行但 reward 非 1.0 记 `failed`；异常或
缺少 reward 记 `error`；找不到 trial 记 `not_run`。总通过率以全部实际执行 trial
为分母，`not_run` 不进入分母，同时单独报告 execution coverage。
