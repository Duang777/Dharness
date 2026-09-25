# Terminal-Bench 2.0 评测报告

## 结论

截至 2026-09-25，扩展矩阵 20/20 题均已运行。18 题获得 Harbor reward 1.0，
`vulnerable-secret` 因模型供应商策略拒绝获得 reward 0，`qemu-startup` 因
`AgentTimeoutError` 记为 `error`。

- 数据集：`terminal-bench@2.0`，官方 registry 显示 89 题
- 扩展样本：20 题，4 easy + 10 medium + 6 hard
- Harbor dry-run：通过，解析为 20 trials
- 实时运行：19 题，17 题通过、1 题失败、1 题错误
- 日志回放：1 题，1 题通过
- 已尝试任务通过率：90%
- 已评分任务通过率：94.7%
- 执行覆盖率：100%
- 评分覆盖率：95%
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
- 逐题分析：[首批 10 题](ten-task-analysis.md)、
  [新增 10 题](expanded-ten-analysis.md)
- 独立 `fix-git` replay：[replay-results.json](../evaluation/replay-results.json)、
  [replay-results.md](../evaluation/replay-results.md)

## 评测问题

本次实验回答四个问题：

1. Harness 能否在不同难度和不同领域的终端任务中稳定产出可评分结果？
2. 证据门禁、完成审查和恢复机制是否帮助模型收敛，还是只增加调用成本？
3. 失败来自任务解法、模型通道、Harness 约束还是运行基础设施？
4. 原始运行离开当前开发机后，结果能否从脱敏快照重新计算？

这不是模型排行榜实验。任务分配给两个模型通道的方式不同，其中一题使用 journal replay。
因此模型分组只能说明本次运行的来源和结果，不能证明模型之间的能力差异。

## 实验方法

### 任务选择

首批矩阵选择 3 道 easy、4 道 medium 和 3 道 hard。扩展矩阵再增加 1 道 easy、
6 道 medium 和 3 道 hard，最终形成 20 题。选样只读取公开 `task.toml` 的难度、类别
和标签，不读取 solution 或 verifier。

### 运行控制

所有 live trial 都通过 Harbor 0.23.0 启动独立 Docker 环境。runner 默认并发为 1，
避免供应商端点在并发 2 时出现的成批空响应和停滞。Harness 使用相同的动作协议、
证据门禁和默认预算；任务只在明确的恢复批次中重跑。

### 结果选择

首次扩展批次得到 6 个通过、3 个评分失败和 1 个环境错误。恢复批次只运行这四个未通过
任务。最终冻结过程从首批运行、扩展运行和恢复运行中为每个任务选择一个
`result.json`。冻结器拒绝缺失、矩阵外任务和重复任务，并整体替换输出目录，避免旧
快照残留。

### 统计口径

- reward 等于 1.0 的已评分任务记为 `passed`。
- verifier 返回非满分且 trial 没有异常时记为 `failed`。
- trial 有异常或没有 reward 时记为 `error`。
- 没有 trial 的任务记为 `not_run`。
- Attempted pass rate 的分母是全部已执行任务。
- Scored pass rate 的分母只包含 `passed` 和 `failed`。
- Execution coverage 表示产生 trial 的任务比例。
- Scored coverage 表示获得正常 verifier 评分的任务比例。

## 分层结果

### 按难度

| 难度 | 任务数 | 通过 | 失败 | 错误 | 已尝试通过率 |
|---|---:|---:|---:|---:|---:|
| 简单 | 4 | 4 | 0 | 0 | 100% |
| 中等 | 10 | 8 | 1 | 1 | 80% |
| 困难 | 6 | 6 | 0 | 0 | 100% |

两个未通过结果都在 medium 组，但原因不同。`vulnerable-secret` 是模型供应商策略拒绝，
`qemu-startup` 是宿主虚拟化能力不足。当前样本不能据此推断 medium 任务本身更难。

### 按类别

| 类别 | 任务数 | 通过 | 失败 | 错误 |
|---|---:|---:|---:|---:|
| 软件工程 | 4 | 4 | 0 | 0 |
| 安全 | 4 | 3 | 1 | 0 |
| 数据处理 | 3 | 3 | 0 | 0 |
| 系统管理 | 3 | 2 | 0 | 1 |
| 科学计算 | 2 | 2 | 0 | 0 |
| 调试、文件操作、数学、数据科学 | 4 | 4 | 0 | 0 |

类别结果与失败归因一致。安全类别的损失来自模型通道策略，系统管理类别的损失来自
QEMU 运行条件。其余类别在本次样本中全部通过。

### 按执行来源

| 来源 | 任务数 | 通过 | 失败 | 错误 |
|---|---:|---:|---:|---:|
| GLM 5.3 live | 6 | 5 | 0 | 1 |
| `modelhub/gpt-5.6-terra` live | 13 | 12 | 1 | 0 |
| journal replay | 1 | 1 | 0 | 0 |

19 个 live trial 共使用 254 turns、343 次环境调用、2,704,800 个输入 token 和
768,352 个输出 token，总运行时间约 3 小时 45 分。成本字段由当前端点返回为 0，因此
报告不据此估算真实费用。

### 内部收敛与外部评分

17 个 live 通过任务中，10 个以 Harness `verified` 结束，7 个以
`budget_exhausted` 结束。两组平均环境调用分别为 19.4 和 19.3，几乎相同；平均 turns
分别为 12.9 和 16.6。差异主要来自完成提案、reviewer 反馈和协议修复，而不是执行更多
命令。

这个结果说明 `verified` 不能替代 Harbor reward，Harbor reward 也不能诊断 Harness
是否高效收敛。项目同时保留两个信号：外部 reward 衡量任务结果，内部 stop reason
衡量控制循环质量。

## 前 10 题状态

| 任务 | 难度 | 模型 | 模式 | 状态 | 奖励 | Harness 停止原因 |
|---|---|---|---|---|---:|---|
| `overfull-hbox` | 简单 | `modelhub/gpt-5.6-terra` | 实时 | 通过 | 1 | `budget_exhausted` |
| `fix-git` | 简单 | GLM 5.3 | 实时 | 通过 | 1 | `verified` |
| `cobol-modernization` | 简单 | GLM 5.3 | 实时 | 通过 | 1 | `budget_exhausted` |
| `log-summary-date-ranges` | 中等 | GLM 5.3 | 实时 | 通过 | 1 | `verified` |
| `openssl-selfsigned-cert` | 中等 | GLM 5.3 | 实时 | 通过 | 1 | `verified` |
| `modernize-scientific-stack` | 中等 | 未调用新模型 | 回放 | 通过 | 1 | 不适用 |
| `qemu-startup` | 中等 | GLM 5.3 | 实时 | 错误 | 0 | `AgentTimeoutError` |
| `cancel-async-tasks` | 困难 | `modelhub/gpt-5.6-terra` | 实时 | 通过 | 1 | `verified` |
| `configure-git-webserver` | 困难 | GLM 5.3 | 实时 | 通过 | 1 | `budget_exhausted` |
| `model-extraction-relu-logits` | 困难 | `modelhub/gpt-5.6-terra` | 实时 | 通过 | 1 | `budget_exhausted` |

## 新增 10 题状态

| 任务 | 难度 | 模型 | 模式 | 状态 | 奖励 | Harness 停止原因 |
|---|---|---|---|---|---:|---|
| `prove-plus-comm` | 简单 | `modelhub/gpt-5.6-terra` | 实时 | 通过 | 1 | `budget_exhausted` |
| `regex-log` | 中等 | `modelhub/gpt-5.6-terra` | 实时 | 通过 | 1 | `verified` |
| `nginx-request-logging` | 中等 | `modelhub/gpt-5.6-terra` | 实时 | 通过 | 1 | `budget_exhausted` |
| `extract-elf` | 中等 | `modelhub/gpt-5.6-terra` | 实时 | 通过 | 1 | `budget_exhausted` |
| `query-optimize` | 中等 | `modelhub/gpt-5.6-terra` | 实时 | 通过 | 1 | `verified` |
| `vulnerable-secret` | 中等 | `modelhub/gpt-5.6-terra` | 实时 | 失败 | 0 | `model_failure` |
| `multi-source-data-merger` | 中等 | `modelhub/gpt-5.6-terra` | 实时 | 通过 | 1 | `verified` |
| `fix-code-vulnerability` | 困难 | `modelhub/gpt-5.6-terra` | 实时 | 通过 | 1 | `verified` |
| `password-recovery` | 困难 | `modelhub/gpt-5.6-terra` | 实时 | 通过 | 1 | `verified` |
| `dna-assembly` | 困难 | `modelhub/gpt-5.6-terra` | 实时 | 通过 | 1 | `verified` |

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

## 架构结论

实验结果支持四个架构判断。

第一，模型不适合成为完成状态的唯一权威。`dna-assembly` 证明模型、reviewer 和自写
检查可能共享同一个错误解释。控制器必须重新执行检查，外部 verifier 仍必须保留最终
裁决权。

第二，恢复策略必须按故障域选择。协议空响应可以进入 schema repair，单次请求停滞可以
由模型调用超时截断，Docker 拉取 EOF 可以重试，QEMU 能力缺失则必须更换 runner。
不区分故障域的统一重试只会增加 token 和墙钟时间。

第三，单写者结构值得保留。19 个 live trial 的 343 次环境调用都由
`CommandRunner` 串行执行。reviewer 只读取证据覆盖关系，没有与 executor 竞争环境
状态。当前问题集中在完成标准，而不是并发写入导致的不可重现状态。

第四，下一轮优化应针对完成协议，而不是继续扩展 prompt。7 个外部通过任务没有达到
内部 `verified`。这些任务已经完成，但 reviewer 与只读检查策略无法就证据形式达成
一致。更有效的改进是隔离验证工作区和增量缺口反馈，不是增加更多通用提示。

## 有效性边界

- 样本量只有 20，且选样强调类别和难度覆盖，不代表 Terminal-Bench 2.0 全部 89 题。
- 结果混合两个模型通道和一次 replay，不能用于模型排名。
- 恢复批次使用了首次失败后的诊断信息。最终 18/20 反映工程闭环结果，不是严格的一次性
  pass@1。
- 当前运行位于 Apple Silicon 和 OrbStack。QEMU 结论只适用于该运行环境。
- 部分 provider 成本字段为 0。token 可以复算，真实费用不能从当前快照推导。
- 冻结快照只保留 allowlist 字段和源结果 SHA-256。它支持结果复算和来源核对，但不包含
  完整任务输出或模型推理内容。

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
