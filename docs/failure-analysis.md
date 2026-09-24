# 失败分析

固定矩阵 10/10 题均已运行。9 题获得 reward 1.0，`qemu-startup` 因运行环境异常记为
`error`。以下问题来自评测预检、真实运行和扩展批次，并分别归因到模型通道、Harness
或评测基础设施。

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
的 trial 才能证明任务是否通过。原始超时 trial 保持不变，`nop` 诊断结果不计入模型
成绩。启用 HTTPS 挂载后的 live `fix-git` 已获得 reward 1.0。

恢复 verifier 后，`JournalReplayAgent` 将策略修复后的原始模型命令重放到全新容器。
源 journal SHA-256 与记录值一致，官方 verifier 返回 reward 1.0。该结果确认原始解法
正确，但报告仍将 replay 与新的模型运行分开。

## 6. 模型端点空响应和停滞

**现象**

GLM 5.3 在并发 2 的批次中多次返回空正文或长时间不返回。`overfull-hbox` 首次运行
因此耗尽预算并获得 reward 0。并发 1 没有消除单请求停滞。`qemu-startup` 在遇到环境
错误后，下一次模型请求一直等待，最终由 Harbor 的 1800 秒 Agent 超时终止。

**归因**

端点不稳定是外部问题，但 Harness 有两个放大因素。`LiteLLMModelGateway._call` 在调用
`_request` 后才进入 schema 修复的 `try`，所以空正文直接终止当前决策，不能使用已有的
一次结构修复机会。`EvidenceLoop` 也没有单次模型调用超时，只能等待整个 trial 的墙钟
预算结束。

**修正**

模型请求现在位于 schema 修复的异常边界内。空正文会形成明确的
`ModelProtocolError`，并触发一次带原始错误的结构修复。`EvidenceLoop` 对 executor 和
reviewer 调用使用独立的 `max_model_call_timeout_sec`，默认 360 秒，并把模型与命令超时
上限写入 executor 的预算提示。

切换到 `modelhub/gpt-5.6-terra` 前，连续三次非 benchmark 请求均返回 HTTP 200 和非空
正文。随后使用并发 1 重跑 `cancel-async-tasks`、`model-extraction-relu-logits` 和
`overfull-hbox`，三题均获得 reward 1.0。原始失败 trial 仍保留，聚合结果选择新的
可评分 trial。

## 7. Heredoc 正文被误判为 Shell 重定向

**现象**

完成检查使用 `python3 - <<'PY'` 运行只读 Python 断言。Python 正文中的
`if score > 0.5` 被 `validate_check` 判为输出重定向，检查在执行前被拒绝。

**根因**

`policy._has_output_redirection` 把完整 Shell 脚本交给 `shlex`。lexer 不理解 heredoc
边界，因此继续把 heredoc 中的 Python 比较运算符按 Shell 标点切分。

**修正与验证**

`_without_heredoc_bodies` 在重定向分析前移除 heredoc 正文，只保留 Shell 行。起始行上的
真实 `> output.txt` 仍会被拒绝。只读的 `/dev/null` 重定向和文件描述符重定向也允许
通过。回归测试在修复前复现失败，修复后 `tests/test_policy.py` 的 21 项测试全部通过。

## 8. 外部通过但 Harness 内部未完成

**现象**

Terra 运行的 `overfull-hbox` 和 `model-extraction-relu-logits` 都获得 reward 1.0，但
Harness 内部状态是 `budget_exhausted/model_protocol`。前者用了 5 次 repair，后者也
用了 5 次。

**根因**

completion reviewer 要求在 finish 阶段重新编译 LaTeX，或在隔离黑盒中重新运行并保存
恢复矩阵。这些检查会生成 PDF、日志或 `.npy` 文件。`validate_check` 又要求 completion
check 不得修改任务状态。reviewer 的证据要求与 policy 的只读限制无法同时满足。

这不是任务解法失败。Harbor reward 已证明最终任务状态正确，但 Harness 无法为这类
“验证过程天然产生文件”的任务给出内部 `verified`。

**处理**

本轮只修复了 heredoc 和无害重定向误报，没有放开任意临时写入。直接放开会允许完成检查
改变待评分状态。后续需要为 completion check 提供隔离的临时工作区或环境快照，使验证
产生的文件无法回写任务目录。

## 9. QEMU 在 Rosetta 中无法启动

**现象**

`qemu-startup` 的容器可以找到 `qemu-system-x86_64`，但没有 `/dev/kvm`。Agent 提取并
检查 Alpine 内核和 initramfs 后启动 QEMU，进程立即退出：

```text
rosetta error: Unimplemented syscall number 282
```

随后一次模型请求停滞，Harbor 最终记录 `AgentTimeoutError`。verifier 返回 reward 0，
但汇总器因 trial 同时带异常而将其记为 `error`，不记为普通评分失败。

**根因**

宿主是 Apple Silicon。amd64 任务容器通过 Rosetta 运行，容器内又启动 x86 QEMU，形成
嵌套模拟。Rosetta 不支持 QEMU 启动所需的 syscall 282，且容器没有 KVM 可回退。模型
选择或 Harness prompt 无法补齐这个运行时能力。

**处理**

该题需要在原生 x86_64 Linux runner，或支持所需系统调用和嵌套虚拟化的环境中重跑。
新的单次模型超时能避免环境错误后的模型停滞耗尽整个 trial，但不能修复 QEMU 启动条件。

## 10. 最终状态

- 10/10 题已尝试，execution coverage 为 100%。
- 9 题获得 reward 1.0，0 题为普通 verifier 失败。
- `qemu-startup` 是唯一 `error`，因此 attempted pass rate 为 90%。
- 9 个可评分 trial 全部通过，scored pass rate 为 100%，scored coverage 为 90%。
- 9 个通过结果中有 8 个 live trial 和 1 个 replay trial。
