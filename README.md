# Evidence Harness

Evidence Harness 是面向 Terminal-Bench 2.0 的 Harbor 自定义 Agent。它通过外部
`BaseAgent` 控制隔离任务容器，使用单写者执行循环，并把最后一次修改之后产生的
新鲜验证回执作为内部完成条件。

## 环境

需要 Python 3.12、`uv` 和 Docker。

```bash
uv sync --python 3.12
docker info
```

项目固定 `harbor==0.23.0`，避免 Harbor 插件接口升级导致无法复现实验。

## 单题运行

先按 LiteLLM 供应商约定配置 API key，再执行：

```bash
uv run harbor run \
  --dataset terminal-bench@2.0 \
  --include-task-name fix-git \
  --agent evidence_harness.harbor_agent:EvidenceHarnessAgent \
  --model provider/model \
  --n-concurrent 1
```

常用预算通过 `--agent-kwarg` 调整：

```bash
uv run harbor run \
  --dataset terminal-bench@2.0 \
  --include-task-name fix-git \
  --agent evidence_harness.harbor_agent:EvidenceHarnessAgent \
  --model provider/model \
  --agent-kwarg max_turns=40 \
  --agent-kwarg max_environment_calls=80 \
  --agent-kwarg max_wall_time_sec=1800
```

每个 trial 会在 Harbor 的 Agent 日志目录中写入
`evidence-harness/events.jsonl` 和脱敏后的完整命令输出。Harness 提示只携带有界
首尾摘录和摘要哈希。

## 固定 10 题评测

[`evaluation/matrix.json`](evaluation/matrix.json) 固定了 3 easy、4 medium、3 hard
共 10 题。它只依据公开 `task.toml` 元数据分层选样，不读取 verifier 或 solution。

先验证任务选择和 Harbor 配置，不调用模型：

```bash
uv run python scripts/run_evaluation.py \
  --model provider/model \
  --dry-run
```

真实运行：

```bash
uv run python scripts/run_evaluation.py \
  --model provider/model \
  --env-file /absolute/path/to/provider.env
```

汇总结果：

```bash
uv run python scripts/summarize_results.py runs/terminal-bench-2 \
  --json-out evaluation/results.json \
  --markdown-out evaluation/results.md
```

汇总器将未执行任务标为 `not_run`。它们不会被伪装成 benchmark failure，也不会进入
pass-rate 分母。

## 验证

```bash
uv run ruff check .
uv run mypy src scripts tests
uv run pytest --cov=evidence_harness
uv build
uv run harbor agent schema evidence_harness.harbor_agent:EvidenceHarnessAgent
```

本地 smoke test 使用 OpenAI 兼容 mock server 和真实 Docker/Harbor 链路，验证 Agent
适配、执行、证据审查及官方 verifier：

```bash
uv run python scripts/mock_openai_server.py --port 18080

OPENAI_API_KEY=test-key uv run harbor run \
  --path tests/fixtures/hello-world \
  --agent evidence_harness.harbor_agent:EvidenceHarnessAgent \
  --model openai/mock-model \
  --agent-kwarg api_base=http://127.0.0.1:18080/v1 \
  --agent-kwarg max_turns=6 \
  --agent-kwarg max_environment_calls=10 \
  --n-concurrent 1
```

## 文档

- [架构决策](docs/architecture-rationale.md)
- [评测报告](docs/evaluation-report.md)
- [失败分析](docs/failure-analysis.md)
- [后续 10 小时优先级](docs/next-10-hours.md)
- [Vibe Coding 日志](docs/vibe-coding-log.md)
