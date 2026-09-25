# Terminal-Bench 2.0 评测报告

## 结论

截至 2026-09-25，扩展矩阵 20/20 题均已运行。18 题获得 Harbor reward 1.0，
`vulnerable-secret` 因模型供应商策略拒绝获得 reward 0，`qemu-startup` 因
`AgentTimeoutError` 记为 `error`。

- 数据集：`terminal-bench@2.0`，官方 registry 显示 89 题
- 扩展样本：20 题，4 easy + 10 medium + 6 hard
- Harbor dry-run：通过，解析为 20 trials
- Live trials：19，17 题通过、1 题失败、1 题错误
- Replay trials：1，1 题通过
- Attempted pass rate：90%
- Scored pass rate：94.7%
- Execution coverage：100%
- Scored coverage：95%
- 普通 verifier 失败数：1
- 基础设施错误数：1
- 未运行：0
- 当前阻塞：`qemu-startup` 需要兼容的 x86_64 runner；`vulnerable-secret` 需要允许该
  授权安全评测的模型通道

`qemu-startup` 已执行并返回 reward 0，但同一 trial 带有 `AgentTimeoutError`。汇总器
按 `error` 处理，因此它进入 attempted pass rate，不进入 scored pass rate。矩阵包含
一个 replay，逐题结果保留 `execution_mode`，不会把 replay 写成新的模型调用。
`vulnerable-secret` 没有 Harbor 异常，reward 0，因此记为普通 `failed`。

当前汇总文件：

- 20 题矩阵与汇总：[matrix-20.json](../evaluation/matrix-20.json)、
  [results-20.json](../evaluation/results-20.json)、
  [results-20.md](../evaluation/results-20.md)
- 20 个脱敏 Harbor trial：[evaluation/trials-20](../evaluation/trials-20)
- 首批 10 题快照：[results.json](../evaluation/results.json)、
  [results.md](../evaluation/results.md)
- 独立 `fix-git` replay：[replay-results.json](../evaluation/replay-results.json)、
  [replay-results.md](../evaluation/replay-results.md)

## 前 10 题状态

| Task | Difficulty | Model | Mode | Status | Reward | Harness stop reason |
|---|---|---|---|---|---:|---|
| `overfull-hbox` | easy | `modelhub/gpt-5.6-terra` | live | passed | 1 | `budget_exhausted` |
| `fix-git` | easy | GLM 5.3 | live | passed | 1 | `verified` |
| `cobol-modernization` | easy | GLM 5.3 | live | passed | 1 | `budget_exhausted` |
| `log-summary-date-ranges` | medium | GLM 5.3 | live | passed | 1 | `verified` |
| `openssl-selfsigned-cert` | medium | GLM 5.3 | live | passed | 1 | `verified` |
| `modernize-scientific-stack` | medium | no new call | replay | passed | 1 | N/A |
| `qemu-startup` | medium | GLM 5.3 | live | error | 0 | `AgentTimeoutError` |
| `cancel-async-tasks` | hard | `modelhub/gpt-5.6-terra` | live | passed | 1 | `verified` |
| `configure-git-webserver` | hard | GLM 5.3 | live | passed | 1 | `budget_exhausted` |
| `model-extraction-relu-logits` | hard | `modelhub/gpt-5.6-terra` | live | passed | 1 | `budget_exhausted` |

## 新增 10 题状态

| Task | Difficulty | Model | Mode | Status | Reward | Harness stop reason |
|---|---|---|---|---|---:|---|
| `prove-plus-comm` | easy | `modelhub/gpt-5.6-terra` | live | passed | 1 | `budget_exhausted` |
| `regex-log` | medium | `modelhub/gpt-5.6-terra` | live | passed | 1 | `verified` |
| `nginx-request-logging` | medium | `modelhub/gpt-5.6-terra` | live | passed | 1 | `budget_exhausted` |
| `extract-elf` | medium | `modelhub/gpt-5.6-terra` | live | passed | 1 | `budget_exhausted` |
| `query-optimize` | medium | `modelhub/gpt-5.6-terra` | live | passed | 1 | `verified` |
| `vulnerable-secret` | medium | `modelhub/gpt-5.6-terra` | live | failed | 0 | `model_failure` |
| `multi-source-data-merger` | medium | `modelhub/gpt-5.6-terra` | live | passed | 1 | `verified` |
| `fix-code-vulnerability` | hard | `modelhub/gpt-5.6-terra` | live | passed | 1 | `verified` |
| `password-recovery` | hard | `modelhub/gpt-5.6-terra` | live | passed | 1 | `verified` |
| `dna-assembly` | hard | `modelhub/gpt-5.6-terra` | live | passed | 1 | `verified` |

新增批次首次得到 6 个通过、3 个评分失败和 1 个环境错误。随后只重跑四个未通过任务：

- `query-optimize` 从比参考查询慢约 33% 改进到中位数快 1.22 倍，6 项 verifier 测试通过。
- `multi-source-data-merger` 首次在拉取 Docker manifest 时遇到 EOF，重跑后 3 项测试通过。
- `dna-assembly` 首次引物 Tm 差值为 7.60°C，超过 5°C 上限；重跑使用完整扩增产物、
  终端 BsaI 位点、线性模板顺序和环形拼接语义验证，最终通过。
- `vulnerable-secret` 两次都在模型调用阶段收到 `cyber_policy`，因此保留 reward 0。

扩展矩阵位于 [`evaluation/matrix-20.json`](../evaluation/matrix-20.json)。选样只使用公开
`task.toml` 中的难度、类别和标签，没有读取 verifier 或 solution。Harbor reward 决定
可评分 trial 的最终状态，因此 `overfull-hbox`、
`cobol-modernization`、`configure-git-webserver` 和
`model-extraction-relu-logits` 在 Harness 内部耗尽预算后仍记为通过。

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

## Verifier 恢复

`fix-git` 镜像使用 HTTP Debian apt 源。在相同的 1 CPU、2 GB 限制下，一次更新在
12 秒完成，随后三次都在 90 秒超时，未完成的 Packages 索引只有 0.36 到 0.97 MB。
当时容器 CPU 接近空闲，说明瓶颈不在索引解压。

同一 8.79 MB URL 通过宿主 curl 和镜像内 Python 分别在 8.4 秒和 9.0 秒完成。将 apt
源从 HTTP 改为 HTTPS 后，四次更新均在 7 到 19 秒完成。随后用 `nop` Agent 运行原始
任务和官方 verifier，51 秒内得到预期的 reward 0，没有异常。这证明 verifier 链路
已恢复，reward 0 只因为 `nop` 没有修改任务。

评测脚本提供 `--debian-https-sources`，通过 Harbor 的只读 bind mount 注入
[`debian-https.sources`](../evaluation/debian-https.sources)。该参数不修改任务、
solution 或 verifier。

为避免再次调用已经暴露凭证，`JournalReplayAgent` 把策略修复后保存的 8 条
`agent_decision/execute` 命令原样重放到全新任务容器。源 journal SHA-256 为
`feee916c234682d08fad78f909a9865965d742ae1d354d96aa5800436befece7`。Harbor 官方
verifier 在 52.8 秒内返回 reward 1.0。该结果证明已记录的 GLM 轨迹能够通过任务，
但不代表一次新的模型调用，也不并入当前 live 子集。

## 首批十题聚合

HTTPS apt 挂载启用后，GLM 5.3 产生 5 个通过的 live trial：

- `fix-git`
- `cobol-modernization`
- `log-summary-date-ranges`
- `openssl-selfsigned-cert`
- `configure-git-webserver`

`modernize-scientific-stack` 使用已记录的 GLM 决策在独立 HTTPS verifier 中 replay，
获得 reward 1.0。当前快照明确标为 `replay`。

GLM 5.3 的 `overfull-hbox` 首次结果受到空正文和请求停滞影响。切换到
`modelhub/gpt-5.6-terra` 前，三个非 benchmark smoke 请求均返回 HTTP 200 和非空正文。
随后以 `--n-concurrent 1` 逐题运行：

| Task | Reward | Harness result | Duration |
|---|---:|---|---:|
| `cancel-async-tasks` | 1 | `verified` | 3 分 45 秒 |
| `model-extraction-relu-logits` | 1 | `budget_exhausted/model_protocol` | 7 分 25 秒 |
| `overfull-hbox` | 1 | `budget_exhausted/model_protocol` | 8 分 37 秒 |

`cancel-async-tasks` 的内部状态与外部 reward 一致。另两题的最终任务状态正确，但
completion reviewer 要求运行会产生文件的检查，而 policy 禁止 completion check 修改
任务状态，因此 Harness 没有进入 `verified`。这属于完成检查契约缺口，不影响已返回的
Harbor reward。修复不能简单放开写操作，后续应在隔离工作区运行这类检查。

## QEMU 环境错误

`qemu-startup` 已真实运行。容器内存在 `qemu-system-x86_64`，但没有 `/dev/kvm`。
QEMU 启动时立即返回：

```text
rosetta error: Unimplemented syscall number 282
```

当前宿主是 Apple Silicon，amd64 容器由 Rosetta 执行，容器内再次启动 x86 QEMU。该
嵌套模拟缺少 QEMU 所需的系统调用。启动失败后，下一次 GLM 请求又停滞，最终触发 Harbor
的 `AgentTimeoutError`。汇总器因此把 trial 记为 `error`，不是 `failed`。更换模型不能
补齐该运行时能力；需要原生 x86_64 Linux 或支持所需系统调用与嵌套虚拟化的 runner。

## Harness 修正

扩展批次暴露了三个可复现的 Harness 问题：

1. 空正文抛出的 `ModelProtocolError` 位于 schema repair 边界之外。现在空正文会使用
   一次结构修复机会。
2. 模型调用没有独立超时。现在 executor 和 reviewer 都受
   `max_model_call_timeout_sec` 限制，默认 360 秒，且模型能在预算提示中看到该上限。
3. Shell lexer 会把 heredoc 中的 Python `>` 当成输出重定向。现在重定向检查先移除
   heredoc 正文，并保留对起始 Shell 行真实重定向的拒绝。

`tests/test_policy.py` 的 heredoc 回归用例在修复前失败，修复后通过。模型空正文和单次
调用超时也有独立回归测试。

## 可复现命令

仅预检：

```bash
uv run python scripts/run_evaluation.py \
  --model openai/gpt-4.1 \
  --dry-run \
  --job-name preflight-2026-09-24
```

首批矩阵输出：

```text
Dry run OK - 10 trial(s); nothing was run.
```

扩展矩阵预检：

```bash
uv run python scripts/run_evaluation.py \
  --matrix evaluation/matrix-20.json \
  --model openai/mock-model \
  --dry-run
```

使用可用的 OpenAI 兼容模型端点运行：

```bash
uv run python scripts/run_evaluation.py \
  --model provider/model \
  --env-file /absolute/path/to/provider.env \
  --include-task-name fix-git \
  --debian-https-sources \
  --n-concurrent 1
```

原始 Harbor job 默认位于被 Git 忽略的 `runs/`。先冻结允许公开的字段，再从提交到
仓库的快照生成逐题和总分报告：

```bash
uv run python scripts/freeze_evaluation.py runs/terminal-bench-2/<job-name>
uv run python scripts/summarize_results.py evaluation/trials \
  --json-out evaluation/results.json \
  --markdown-out evaluation/results.md

uv run python scripts/summarize_results.py evaluation/trials-20 \
  --matrix evaluation/matrix-20.json \
  --json-out evaluation/results-20.json \
  --markdown-out evaluation/results-20.md

uv run python scripts/verify_delivery.py
```

汇总规则为：reward 等于 1.0 记 `passed`。已运行但 reward 非 1.0 记 `failed`。异常或
缺少 reward 记 `error`。找不到 trial 记 `not_run`。一个 job 里同一任务存在多个
trial 时，汇总器拒绝生成结果。报告必须同时给出两种通过率、任务状态、execution
coverage 和 scored coverage。冻结快照还记录模型名称、原始 `result.json` SHA-256
和 replay journal SHA-256；API base、凭证、绝对路径、任务输出及 traceback 不进入
快照。
