# Vibe Coding 日志

## 目标

使用 AI 辅助调研、架构比较、实现和验证一个 Terminal-Bench 2.0 Harbor Agent
Harness，同时保留可复查的工程证据。日志记录代表性工作指令、工具表现和人工判断，
不包含凭证、隐藏推理或 benchmark verifier/solution 内容。

## 1. 参考系统调研

**代表性指令**

> 调研成熟终端 Agent 和编码 Agent 的控制循环、验证、长上下文、回滚、工具协议与停止
> 条件；提取能迁移到 Terminal-Bench Harness 的机制。

**产出**

比较了 Terminal Agent、OpenLoop、AutoCodeRover、Agentless、Goose、Crush、Plandex、
OpenDev 和 ForgeCode。可迁移机制包括验证先行、心跳和预算、AST 定位、固定流水线、
MCP 边界、TUI/daemon 分离、diff 沙箱、阶段化 workflow、会话分支与重放。

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
> 主循环；每个模块通过可观察行为测试，不依赖真实模型。

**产出**

实现 Pydantic 动作协议、预算状态机、命令策略、重复周期检测、脱敏日志、确定性上下文
压缩、LiteLLM Gateway、一次 schema repair、只读 completion reviewer 和 Harbor
adapter。单元测试使用 scripted model 与 fake environment 覆盖完成、拒绝、恢复、
预算和协议错误。

**工具表现**

`rg` 和小范围文件读取用于确认 Harbor 0.23.0 的真实接口；`uv` 保证 Python 3.12
依赖一致；Ruff 很快发现 import 和表达式风格问题；pytest fake 能精确复现状态迁移。

## 4. 端到端 Smoke

**代表性指令**

> 不以“单测通过”代替真实集成；用本地最小任务、mock OpenAI 服务、Docker 和 Harbor
> verifier 跑完整链路。

**结果**

第一次把本地路径传给 `--task`，Harbor 按 registry task 解析并失败。根据当前
`harbor run --help` 改为 `--path` 后通过：reward 1.0、2 个 executor turns、1 次
reviewer、3 次环境调用，验证证据位于最后一次修改之后。

**工具表现**

mock server 很适合隔离 Harness 与模型方差；Docker smoke 发现了单测无法发现的 CLI
参数语义和日志路径问题。仓库没有首个 commit 时 bootstrap 的 `git status` 曾产生
`fatal: bad revision 'HEAD'` 噪声，但 trial 不受影响，首次 commit 后会自然消失。

## 5. Terminal-Bench 矩阵

**代表性指令**

> 从官方 Terminal-Bench 2.0 的公开元数据固定十题，覆盖 easy/medium/hard 与多个
> 类别；不得读取 verifier 或 solution；评测和汇总必须可重复。

**结果**

官方 `terminal-bench-sample@2.0` 是 8 medium + 2 hard，不能覆盖 easy。随后只解析
`terminal-bench@2.0` 的 89 个 `task.toml`，得到 4 easy、55 medium、30 hard，并固定
3/4/3 的十题矩阵。Harbor dry-run 成功解析 10 trials。

**工具表现**

Harbor registry 与 dataset download 可稳定提供公开元数据。用 TOML parser 提取结构
字段比 shell 正则可靠，也避免误读 verifier 内容。

## 6. 模型可用性预检

**代表性指令**

> 在不读取或输出凭证内容的前提下，检查环境变量和本机合法登录通道；必须用最小真实
> 推理确认，不得只相信 status。

**结果**

环境中没有 LiteLLM 供应商 key。Claude Code 显示 OAuth 登录，但最小推理超过两分钟
无响应；Codex CLI 显示 API key 登录，但真实请求返回 401。没有启动十题真实评测，
pass rate 保持 N/A。

**人工判断**

没有临时增加 CLI Gateway，也没有用 mock 结果冒充 benchmark 分数。CLI 登录态不属于
项目可复现依赖；应先恢复有效模型通道，再收集基线。

## 当前质量证据

- `harbor==0.23.0`，Python 3.12，Docker server 29.4.0
- 真实 Harbor/Docker smoke reward 1.0
- 固定十题 Harbor dry-run 通过
- Ruff、pytest、coverage、build 和 Agent schema 由交付前门禁统一复查
- 真实 Terminal-Bench 通过率：N/A，原因是模型认证阻塞，不是任务失败
