# Terminal-Bench 2.0 评测报告

## 结论

截至 2026-09-24，GLM 5.3 已通过结构化推理探针和真实 Harbor smoke。固定矩阵中的
`fix-git` 已运行一次完整 Agent 阶段。官方 verifier 在 `apt-get update` 中停滞，并在
900 秒后返回 `VerifierTimeoutError`，因此该 trial 状态是 `error`，没有 reward。

- 数据集：`terminal-bench@2.0`，官方 registry 显示 89 题
- 固定样本：10 题，3 easy + 4 medium + 3 hard
- Harbor dry-run：通过，解析为 10 trials
- 已启动真实 benchmark trials：1/10
- 获得 verifier reward 的 trials：0/10
- Attempted pass rate：0%
- 可评分通过率：N/A
- Execution coverage：10%
- Scored coverage：0%
- 任务失败数：0
- 基础设施错误数：1
- 当前阻塞：`fix-git` 官方镜像中的 `apt-get update` 在本机 OrbStack 环境中超时

Attempted pass rate 把 `error` 计入分母，所以当前是 0%。Scored pass rate 只统计获得
reward 的 trial，所以当前是 N/A。两个值必须同时报告。

当前汇总文件：

- [`results.json`](../evaluation/results.json)
- [`results.md`](../evaluation/results.md)

## 逐题状态

| Task | Difficulty | Category | Status | Reward |
|---|---|---|---|---:|
| `overfull-hbox` | easy | debugging | not_run | N/A |
| `fix-git` | easy | software-engineering | error | N/A |
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

本地 `hello-world` fixture 使用真实 GLM 5.3、Harbor 0.23.0、Docker 环境、自定义
`EvidenceHarnessAgent` 和 shell verifier 完成端到端 smoke：

- Harbor reward：1.0
- Executor turns：2
- Completion reviewer：1 次
- Environment calls：5
- Work epoch：1
- Fresh evidence：accepted
- Input tokens：7,772
- Output tokens：5,729

该结果只证明适配层、控制循环、日志、验证门禁和 Harbor verifier 能连通，不代表
Terminal-Bench 2.0 成绩，也不进入十题通过率。

## 首轮优化

`fix-git` 基线运行发现，验证策略把两种只读命令误判为写操作：

- `git merge-base --is-ancestor`
- `grep` 搜索 `<<<<<<<` 和 `>>>>>>>` 冲突标记

模型连续五次提交完成检查，五次都被策略拒绝。Agent 最终以 `budget_exhausted` 停止，
failure category 是 `model_protocol`。代码把整段 Shell 交给正则扫描，正则无法区分
命令、参数和引号内容。

策略把写命令和输出重定向分开判断。Git 子命令使用完整 token 边界，因此
`merge-base` 不再匹配 `merge`。输出重定向使用 Python `shlex` 区分操作符和引号内容。
修复还补上了原策略漏掉的 `sed -i`。

同一模型、任务和预算的复测结果如下。复测关闭了官方 verifier，因为基线已经证明该
verifier 会在当前环境中超时。复测只比较 Agent 内部控制结果。

| 指标 | 基线 | 修复后 |
| --- | ---: | ---: |
| Stop reason | `budget_exhausted` | `verified` |
| Repair count | 5 | 0 |
| Turns | 11 | 4 |
| Environment calls | 12 | 12 |
| Input tokens | 91,405 | 33,938 |
| Output tokens | 37,041 | 25,602 |
| Agent runtime | 9 分 00 秒 | 6 分 00 秒 |

修复后第一次 `finish` 通过策略检查。completion reviewer 接受了覆盖关系，三条只读检查
全部成功。输入 token 减少 62.9%，输出 token 减少 30.9%，Agent 运行时间减少 33.3%。

实验决策和证据路径记录在
[`benchmark-optimization.tsv`](../.audit/benchmark-optimization.tsv)。

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

使用可用的 OpenAI 兼容模型端点运行：

```bash
uv run python scripts/run_evaluation.py \
  --model provider/model \
  --env-file /absolute/path/to/provider.env
```

生成逐题和总分报告：

```bash
uv run python scripts/summarize_results.py runs/terminal-bench-2/<job-name> \
  --json-out evaluation/results.json \
  --markdown-out evaluation/results.md
```

汇总规则为：reward 等于 1.0 记 `passed`。已运行但 reward 非 1.0 记 `failed`。异常或
缺少 reward 记 `error`。找不到 trial 记 `not_run`。一个 job 里同一任务存在多个
trial 时，汇总器拒绝生成结果。报告必须同时给出两种通过率、任务状态、execution
coverage 和 scored coverage。
