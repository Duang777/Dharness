# 失败分析

当前已经运行一个真实 Terminal-Bench trial，但该 trial 没有获得 verifier reward。
以下问题来自评测预检和首轮真实运行。每项问题分别归因到模型通道、Harness 或评测
基础设施。

## 1. Codex 登录状态与真实可用性不一致

**现象**

`codex login status` 返回已使用 API key 登录，但最小结构化推理请求连续返回 HTTP
401 `invalid_api_key`。请求没有产生模型输出。

**归因**

这是模型认证失败，不是模型能力失败，也不是 Evidence Harness 决策循环失败。状态命令
只证明本机保存过一种认证配置，不能证明凭证当前有效。

**根因**

评测预检最初把凭证存在误当成模型可调用。两者之间缺少一次不会操作 benchmark
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
不是已支持的生产后端。临时把 CLI 包装为 Gateway 会额外引入进程生命周期、CLI 版本、
登录态和输出协议风险。

**根因**

本机有可执行文件且显示已登录，不足以构成稳定模型依赖。CLI 可能受网络、账户策略、
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

README 和 smoke 命令统一使用 `--path`。正式 benchmark 使用
`--dataset terminal-bench@2.0` 加十个 `--include-task-name`。runner 生成命令，避免
每次手工重写参数。

**后续防护**

项目固定 Harbor 0.23.0，并在交付前执行 Agent schema 检查、固定矩阵 dry-run 和真实
本地 smoke。Harbor 升级时先过这三道兼容性检查，再运行付费 benchmark。

## 4. 只读验证被误判为写操作

**现象**

`fix-git` 的任务修改已经完成，但五次 `finish` 都被 `EvidenceGate` 拒绝。被拒绝的
检查包含 `git merge-base --is-ancestor`，以及用 `grep` 搜索 Git 冲突标记的命令。
Agent 最终以 `budget_exhausted` 停止，failure category 是 `model_protocol`。

**归因**

这是 Harness 策略缺陷，不是模型解题失败。模型已恢复丢失提交、解决冲突并生成合并
提交。错误发生在完成检查执行之前。

**根因**

`_CHECK_MUTATION` 用正则扫描整段 Shell。正则把 `merge-base` 的 `merge` 前缀当成
`git merge`，也把引号内的 `>>>>>>>` 当成输出重定向。相同正则还漏掉了 `sed -i`。

**修正**

`validate_check` 现在分别判断写命令和输出重定向。Git 子命令使用完整 token 边界。
输出重定向使用 Python `shlex` 区分操作符和引号内容。该策略是完成门禁，不是容器
安全边界。

**验证**

同题复测从 11 turns、5 repairs 和 `budget_exhausted` 改善到 4 turns、0 repairs 和
`verified`。输入 token 从 91,405 降到 33,938。45 项测试和所有项目门禁通过。

## 5. Terminal-Bench verifier 超时

**现象**

`fix-git` 的 Agent 阶段结束后，verifier 在 `apt-get update` 中停留。Harbor 在 900 秒
后返回 `VerifierTimeoutError`。trial 没有 reward。

**归因**

这是 verifier 基础设施错误。它既不是模型失败，也不是 Harness 完成门禁失败。结果状态
是 `error`，不是 `failed`。

**根因**

`alexgshaw/fix-git:20251031` 中 apt 使用 HTTP Debian 源。相同镜像和资源限制下，
一次更新在 12 秒完成，随后三次都在 90 秒超时。下载停在 0.36 到 0.97 MB，容器 CPU
接近空闲。相同 8.79 MB 文件通过宿主 curl 和镜像内 Python 都在约 9 秒完成，因此
问题位于 apt 的 HTTP 传输路径，不是 Harbor 调度、容器网络整体故障或索引解压。

**修正**

将同一 Debian 官方源改为 HTTPS 后，四次 apt 更新均在 7 到 19 秒完成。评测脚本的
`--debian-https-sources` 参数通过 Harbor 只读挂载替换源配置，不修改 benchmark 任务
或 verifier。使用该挂载运行 `nop` Agent 时，官方 verifier 在 51 秒内正常结束并返回
预期的 reward 0。

**后续防护**

评测报告分别显示 `passed`、`failed`、`error` 和 `not_run`。只有 verifier 返回 reward
的 trial 才能证明任务是否通过。原始超时 trial 保持不变；下一步使用轮换后的模型凭证
重跑 `fix-git`，不能把 `nop` 诊断结果计为模型成绩。

恢复 verifier 后，`JournalReplayAgent` 将策略修复后的原始模型命令重放到全新容器。
源 journal SHA-256 与记录值一致，官方 verifier 返回 reward 1.0。该结果确认原始解法
正确，但报告仍将 replay 与新的模型运行分开。
