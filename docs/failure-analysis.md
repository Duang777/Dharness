# 失败分析

当前没有真实 Terminal-Bench trial，因此不能编造“模型解题失败”。以下三项是开发和
评测预检阶段实际发生的失败，分别归因到认证、模型通道和 Harbor 调用层。真实十题运行
后，应使用同样结构补充任务级失败。

## 1. Codex 登录状态与真实可用性不一致

**现象**

`codex login status` 返回已使用 API key 登录，但最小结构化推理请求连续返回 HTTP
401 `invalid_api_key`。请求没有产生模型输出。

**归因**

这是模型认证失败，不是模型能力失败，也不是 Evidence Harness 决策循环失败。状态命令
只证明本机保存过一种认证配置，不能证明凭证当前有效。

**根因**

评测预检最初把“凭证存在”误当成“模型可调用”。两者之间缺少一次不会操作 benchmark
环境的最小推理探针。

**修正**

真实评测前同时检查三层：凭证变量或认证状态、最小推理请求、Harbor dry-run。任何一层
失败都停止，不创建 benchmark trial，不报告 0 分。

**后续防护**

runner 不自动读取或打印凭证，只接受供应商环境或 `--env-file`。结果汇总器把没有
reward 的 trial 记为 `error`，把根本没有产生的 trial 记为 `not_run`。

## 2. Claude Code OAuth 推理无响应

**现象**

`claude auth status` 显示 OAuth 已登录，但禁用工具、关闭会话持久化的最小
`claude -p` 请求在两分钟内没有返回。进程被终止，没有可验证输出。

**归因**

这是模型通道可用性问题。由于当前 Agent 使用 LiteLLM 结构化调用，Claude CLI 本来也
不是已支持的生产后端；临时把 CLI 包装为 Gateway 会额外引入进程生命周期、CLI 版本、
登录态和输出协议风险。

**根因**

“本机有可执行文件且显示已登录”不足以构成稳定模型依赖。CLI 可能受网络、账户策略、
服务端排队或本地配置影响，且这些状态不在项目锁文件内。

**修正**

放弃在本轮中临时增加 Claude CLI Gateway，保留单一 LiteLLM 边界。待获得明确供应商
API key 后，用 `provider/model` 和最小请求验证，再运行矩阵。

**后续防护**

不把开发机的交互式 Agent 登录态当作 benchmark 基础设施。若未来正式支持 CLI
backend，需要独立协议、硬超时、stderr 脱敏、结构化输出测试和固定 CLI 版本。

## 3. Harbor 本地任务参数误用

**现象**

第一次 smoke 命令把本地 fixture 传给 `--task/-t`，Harbor 将它解释为 registry task，
因此解析失败。改用 `--path/-p tests/fixtures/hello-world` 后，同一 fixture 完成真实
Docker trial 并获得 reward 1.0。

**归因**

这是评测编排错误，属于 Harness 外围集成缺陷，不是模型能力问题。修正后无需改动 Agent
循环即可通过，说明根因位于 CLI 参数语义。

**根因**

沿用了旧版本或直觉化的参数理解，没有先以当前 `harbor run --help` 为权威来源。在
Harbor 0.23.0 中，`--task` 明确表示 registry `org/name`，本地任务使用 `--path`。

**修正**

README 和 smoke 命令统一使用 `--path`；正式 benchmark 使用
`--dataset terminal-bench@2.0` 加十个 `--include-task-name`。runner 生成命令，避免
每次手工重写参数。

**后续防护**

项目固定 Harbor 0.23.0，并在交付前执行 Agent schema 检查、固定矩阵 dry-run 和真实
本地 smoke。Harbor 升级时先过这三道兼容性检查，再运行付费 benchmark。
