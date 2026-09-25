# Vibe Coding 日志

## 目标

使用 AI 辅助调研、架构比较、实现和验证一个 Terminal-Bench 2.0 Harbor Agent
Harness，同时保留可复查的工程证据。日志记录代表性工作指令、工具表现和人工判断，
不包含凭证、隐藏推理或 benchmark verifier/solution 内容。

## 使用工具

- **TRAE：** 主 Vibe Coding 环境，用于阅读仓库、比较架构、实现、测试、运行 Harbor
  和维护决策日志。
- **并行评审 Agent：** 用于独立审查架构候选、Shell policy 和提交前风险；结论必须由
  主线程用代码、测试或真实 trial 复核。
- **终端工具：** `rg`、Git、`uv`、Ruff、mypy、pytest/coverage、Docker 和 Harbor。
- **模型通道：** GLM 5.3 与 `modelhub/gpt-5.6-terra` 用于真实 benchmark；
  确定性 OpenAI-compatible mock server 用于无费用的集成回归。

## 5 个关键 Prompt

> 1. 调研终端 Agent 的控制循环、验证、长上下文、回滚、工具协议与停止条件，给出多套
> 有实质差异的架构，并按正确性、通用性、成本和 Harbor 适配交叉评审。

> 2. 先建立严格领域协议，再实现命令执行、输出归档、模型边界、prompt 投影、证据门禁
> 和主循环；测试必须断言用户可观察行为，不依赖真实模型。

> 3. 不以单测通过代替真实集成。用本地 fixture、mock OpenAI 服务、Docker、Harbor
> Agent 和官方 verifier 跑完整链路。

> 4. 用固定矩阵中的同一题建立真实基线，只根据运行证据修改一个 Harness 机制，再用
> 同一模型、任务和预算复测，量化 turn、repair、token 和停止原因。

> 5. 剩余题目按并发 1 实际运行；失败必须区分模型、Harness 和基础设施，报告必须区分
> live、replay、failed、error 与 not_run，不能只复述最终状态。

## AI 帮了什么

AI 最有价值的工作不是批量生成代码，而是扩大假设空间并快速构造反例。它并行比较了
外部单 Agent、分层控制器和容器内 CLI，帮助选择单写者架构；为 `work_epoch`、验证
预算、schema repair、模型超时和 heredoc 解析补齐了行为测试；还把真实 trial 的异常
按模型通道、Harness policy、Harbor 编排和宿主虚拟化分层。每项保留的结论都落到了
测试、原始 reward、来源哈希或可重跑命令，而不是只保留对话文本。

## AI 坑了什么

AI 首先把 Harbor 本地 fixture 错写成 `--task`，而当前 0.23.0 实际要求 `--path`。
其次，第一版 Shell 写操作 denylist 修完一个误报后又暴露嵌套 shell 绕过；这说明生成
更多正则不能把通用 Shell 变成安全边界。它还一度把已存在的 CLI 登录状态当作模型可用，
真实最小请求却分别超时和返回 401。最后，completion reviewer 给出的“重新编译并保存
产物”建议与只读 finish policy 冲突，导致外部 reward 1.0 而内部预算耗尽。人工决策是
保留严格边界、记录限制，并把隔离验证工作区列为后续设计，而不是为追求漂亮 stop reason
放开写权限。

## 1. 设计调研

**代表性指令**

> 调研终端 Agent 的控制循环、验证、长上下文、回滚、工具协议与停止条件，形成适用于
> Terminal-Bench Harness 的设计候选。

**产出**

设计候选覆盖完成前验证、长循环预算、结构化定位、固定流水线、工具边界、变更隔离、
阶段化 workflow 和会话恢复。

**工具表现**

并行检索适合扩大设计空间，但不同项目术语不一致。人工归一为五个问题：谁写环境、
上下文如何投影、何时恢复、怎样证明完成、谁决定停止。

## 2. 架构 Arena

**代表性指令**

> 给出多套互相有实质差异的 Harness 架构，按正确性、通用性、可测试性、成本、Harbor
> 适配和实现风险交叉评审，不要只做表面命名变化。

**产出**

候选包括外部单 Agent、分层 Planner/Executor/Critic、容器内 CLI 等。交叉评分选择
Candidate A（53/60），Candidate C 为 32/60。最终采用外部 `BaseAgent` + 单写者循环，
并从其他候选移植完成 reviewer、验证预算和 requirement coverage。

**人工判断**

没有保留持续 Critic 和多 Agent 并行写环境。它们会增加 token 与状态竞争，但在没有
基线数据时无法证明收益。Harbor 已提供 `BaseEnvironment.exec`，容器内再装 Agent CLI
只会扩大故障面。

## 3. 实现

**代表性指令**

> 先建立严格领域协议，再实现命令执行、输出归档、模型边界、prompt 投影、证据门禁和
> 主循环。每个模块通过可观察行为测试，不依赖真实模型。

**产出**

实现 Pydantic 动作协议、预算状态机、命令策略、重复周期检测、脱敏日志、确定性上下文
压缩、LiteLLM Gateway、一次 schema repair、只读 completion reviewer 和 Harbor
adapter。单元测试使用 scripted model 与 fake environment 覆盖完成、拒绝、恢复、
预算和协议错误。

**工具表现**

`rg` 和小范围文件读取用于确认 Harbor 0.23.0 的真实接口。`uv` 保证 Python 3.12
依赖一致。Ruff 发现 import 和表达式风格问题。pytest fake 精确复现状态迁移。

## 4. 端到端 Smoke

**代表性指令**

> 不以单测通过代替真实集成。用本地最小任务、mock OpenAI 服务、Docker 和 Harbor
> verifier 跑完整链路。

**结果**

第一次把本地路径传给 `--task`，Harbor 按 registry task 解析并失败。根据当前
`harbor run --help` 改为 `--path` 后通过：reward 1.0、2 个 executor turns、1 次
reviewer、5 次环境调用，验证证据位于最后一次修改之后。

**工具表现**

mock server 用于隔离 Harness 与模型方差。Docker smoke 发现了单测无法发现的 CLI
参数语义和日志路径问题。仓库没有首个 commit 时 bootstrap 的 `git status` 曾产生
`fatal: bad revision 'HEAD'` 噪声，但 trial 不受影响，首次 commit 后会自然消失。

## 5. Terminal-Bench 矩阵

**代表性指令**

> 从官方 Terminal-Bench 2.0 的公开元数据固定十题，覆盖 easy、medium 和 hard 与多个
> 类别。不得读取 verifier 或 solution。评测和汇总必须可重复。

**结果**

官方 `terminal-bench-sample@2.0` 是 8 medium + 2 hard，不能覆盖 easy。随后只解析
`terminal-bench@2.0` 的 89 个 `task.toml`，得到 4 easy、55 medium、30 hard，并固定
3 道 easy、4 道 medium 和 3 道 hard 的十题矩阵。Harbor dry-run 成功解析 10 trials。

**工具表现**

Harbor registry 与 dataset download 可稳定提供公开元数据。用 TOML parser 提取结构
字段比 shell 正则可靠，也避免误读 verifier 内容。

## 6. 模型可用性预检

**代表性指令**

> 在不读取或输出凭证内容的前提下，检查环境变量和本机合法登录通道。必须用最小真实
> 推理确认，不得只相信 status。

**结果**

环境中没有 LiteLLM 供应商 key。Claude Code 显示 OAuth 登录，但最小推理超过两分钟
无响应。Codex CLI 显示 API key 登录，但真实请求返回 401。随后获得一个 OpenAI 兼容
的 GLM 5.3 端点。结构化推理探针和真实 Harbor smoke 均通过。

**人工判断**

没有临时增加 CLI Gateway，也没有用 mock 结果冒充 benchmark 分数。CLI 登录态不属于
项目可复现依赖。GLM 端点通过现有 LiteLLM 边界接入，不需要改变 Agent 接口。

## 7. 首轮真实评测与优化

**代表性指令**

> 用固定矩阵中的一题建立真实基线。只根据运行证据修改一个 Harness 机制，并用同一
> 模型、任务和预算复测。

**结果**

`fix-git` 基线暴露了验证策略误报。模型完成任务后，五次只读检查被判为写操作。策略从
整段正则扫描改为命令边界匹配和引号感知的重定向分析。同题复测从
`budget_exhausted` 变为 `verified`。turns 从 11 降到 4，repairs 从 5 降到 0。

官方 verifier 在 `apt-get update` 中停留，并在 900 秒后超时。该结果记为 `error`，
不记为任务失败。复测关闭 verifier，只用于比较 Agent 内部状态。

## 8. Verifier 恢复与矩阵扩展

**代表性指令**

> 先恢复官方 verifier，再扩大固定矩阵。每个结果必须保留 live 或 replay 来源，批次
> 中止时不得把未完成任务计分。

**结果**

将 Debian apt 源从 HTTP 改为 HTTPS 后，`fix-git` 的 `nop` verifier 诊断在 51 秒内
结束。新的 live `fix-git` 和 `log-summary-date-ranges` 均获得 reward 1.0。后续批次
又得到 `cobol-modernization` 和 `configure-git-webserver` 两个 live 通过结果，以及
`overfull-hbox` 的 live reward 0。`modernize-scientific-stack` 的已记录决策在独立
HTTPS verifier 中 replay 通过。

当前快照评分 6/10 题，5 题通过、1 题失败。live 子集为 4/5，replay 子集为 1/1。
execution coverage 和 scored coverage 均为 60%。

**人工判断**

并发 2 的批次出现多次空响应和长时间停滞。并发 1 的 `overfull-hbox` 重跑仍在第三次
模型请求停滞，因此并发不是唯一原因。中止的 `qemu-startup`、`cancel-async-tasks`
和没有形成结果的重跑均未计分。剩余四题只在凭证轮换和端点稳定后以并发 1 运行。

## 9. 模型切换与十题收口

**代表性指令**

> 剩余题目按并发 1 实际运行。失败必须区分模型、Harness 和基础设施，不能只复述状态。

**结果**

切换到 `modelhub/gpt-5.6-terra` 后，三个非 benchmark smoke 请求均返回非空正文。
`cancel-async-tasks`、`model-extraction-relu-logits` 和 `overfull-hbox` 随后获得 reward
1.0。GLM 5.3 已完成的 `openssl-selfsigned-cert` 也获得 reward 1.0。

`qemu-startup` 在 Apple Silicon 宿主的 Rosetta amd64 容器中启动 QEMU，立即返回
`Unimplemented syscall number 282`。容器没有 `/dev/kvm`。后续模型请求停滞，Harbor
最终记录 `AgentTimeoutError`，因此该题记为基础设施 `error`。

运行过程还暴露了三个 Harness 缺口：空正文没有进入 schema repair、模型调用缺少独立
超时、heredoc 内的 Python `>` 被误判为 Shell 重定向。三项均已增加回归测试。另有一个
未在本轮放宽的问题：completion reviewer 需要运行会生成文件的检查，但 policy 只允许
只读 finish 检查。该问题需要隔离验证工作区，不能直接允许写任务目录。

## 10. 二十题扩展与恢复

**代表性指令**

> 完成新增十题，失败项要先归因再选择性重跑。最终证据只能为每个任务选择一个来源，
> 不能把失败和恢复 trial 重复计数。

**结果**

新增十题首次运行得到 6 个通过、3 个评分失败和 1 个 Docker 环境错误。恢复批次只重跑
`query-optimize`、`multi-source-data-merger`、`vulnerable-secret` 和
`dna-assembly`。前三个可恢复问题中，查询优化、镜像拉取和引物设计均得到 reward 1.0；
`vulnerable-secret` 再次被供应商 `cyber_policy` 拒绝，保留 reward 0。

冻结器从首批十题、扩展主运行和恢复运行中为每题选取一个唯一 `result.json`，生成
`evaluation/trials-20`。最终结果是 20/20 已尝试、18 passed、1 failed、1 error。

**人工判断**

`dna-assembly` 的首轮 Harness `verified` 不能覆盖外部 reward 0。根因不是 verifier
“挑剔”，而是自写检查和产物共享同一套错误边界解释。prompt 因此要求从保存后的完整
产物按下游消费者语义解析。`query-optimize` 则要求比较至少两个实质不同的候选，但
reviewer 不再索取不可证明的全局最优。

## 当前质量证据

- `harbor==0.23.0`，Python 3.12，Docker server 29.4.0
- 真实 GLM、Terra、Harbor 与 Docker 链路均取得 reward 1.0
- 扩展矩阵 20/20 已运行，18 题通过、1 题评分失败、1 题基础设施错误
- Live 子集 17/19 通过，replay 子集 1/1 通过
- Attempted pass rate 为 90%，scored pass rate 为 94.7%
- Execution coverage 为 100%，scored coverage 为 95%
- HTTPS apt 挂载恢复了受影响任务的 verifier
- QEMU 需要原生 x86_64 或支持嵌套虚拟化的 runner 重跑
- 63 项测试通过，覆盖率 86%
- Ruff、mypy、build 和两个 Agent schema 通过
