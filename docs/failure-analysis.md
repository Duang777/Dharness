# 失败分析

Terminal-Bench 2.0 全量 canonical 结果为 59 passed、30 failed、0 error。全部 89 题
都有正常 Harbor reward。下表按最终采用的 trial 记录 30 个 reward 0，不把内部
`verified`、模型自述或恢复意图当作通过证据。

## 全量 30 个失败

| 任务 | 内部停止 | 官方失败证据 | 根因 |
|---|---|---|---|
| `break-filter-js-from-html` | `model_failure` | 浏览器没有触发 alert | 4 turns 后触发供应商 `cyber_policy`，绕过产物未完成 |
| `build-pov-ray` | `budget_exhausted` | 2/3 通过，缺少 `file_id.diz` | 只保留了可构建源码，未保留 verifier 要求的完整官方源码树 |
| `caffe-cifar-10` | `model_failure` | 2/6 通过，无 500 次训练模型 | 数据获取和训练耗尽墙钟，solver 仍为 GPU，下一次模型调用只剩 0.001 秒 |
| `code-from-image` | replay | 输出只有 `bee26a` | OCR 只恢复了目标字符串前缀，重放 45 条命令后仍缺少其余字符 |
| `dna-assembly` | `verified` | 两条引物 Tm 相差 7.32°C | 内部检查没有复现 verifier 的完整引物 Tm 计算 |
| `dna-insert` | `model_stopped` | 两条引物 Tm 相差 9.93°C | 模型因找不到本地 `oligotm` 停止，保留了未验证的引物 |
| `extract-moves-from-video` | `budget_exhausted` | `solution.txt` 不存在 | 视频棋局识别未形成最终交付文件 |
| `feal-linear-cryptanalysis` | `model_failure` | `plaintexts.txt` 不存在 | 6 turns 后触发供应商 `cyber_policy` |
| `filter-js-from-html` | `verified` | XSS 漏过且干净 HTML 被改写 | 过滤规则同时存在漏报和破坏性改写，内部样例覆盖不足 |
| `gcode-to-text` | `budget_exhausted` | 输出 `JUST-PRINTING`，不是 flag | 40 turns 后仍未解出 G-code 隐藏文本 |
| `gpt2-codegolf` | `budget_exhausted` | 程序输出包含非法 UTF-8 | 小于 5 KB 的实现能运行，但 token 解码输出不符合接口 |
| `install-windows-3.11` | `verified` | QEMU monitor socket 不存在 | 内部检查没有覆盖 verifier 的键盘与屏幕交互路径 |
| `make-doom-for-mips` | `model_stopped` | 3/3 失败，无 `frame.bmp` | 缺少本地 MIPS 交叉工具链，模型删除了不可信的手写 ELF 后停止 |
| `make-mips-interpreter` | `budget_exhausted` | 缺少预期初始化文本，找不到 `tnt.wad` | VM 能启动但没有正确装载要求的 DOOM 数据与执行路径 |
| `model-extraction-relu-logits` | `budget_exhausted` | 权重矩阵第 15、26 行不匹配 | 恢复算法接近正确，但 completion 协议耗尽后仍留下两行误差 |
| `path-tracing` | `budget_exhausted` | 图像相似度 0.97163，要求 0.99 | 逆向实现的场景或采样参数不够精确 |
| `path-tracing-reverse` | `budget_exhausted` | 图像相似度 0.994823，要求 0.995 | 结果接近阈值，但低位数值或随机序列仍不一致 |
| `polyglot-c-py` | `model_stopped` | 目录含 `cmain` 和 receipt | 产物功能未进入最终断言，额外构建文件先违反单文件契约 |
| `polyglot-rust-c` | `verified` | 目录含 4 个额外构建文件 | 内部检查验证编译运行，却漏掉目录只能有 `main.rs` 的要求 |
| `protein-assembly` | `verified` | donor 序列未找到，蛋白顺序失败 | 生成的 gBlock 没有编码完整的指定融合蛋白 |
| `query-optimize` | `verified` | 1.066 秒对 0.738 秒，超过 1.05 倍上限 | 正确性通过，但最终查询比参考慢约 44.6% |
| `raman-fitting` | `verified` | G 与 2D 峰参数大幅偏离 | 拟合使用了错误尺度或峰选择，内部检查没有使用目标参数 |
| `regex-chess` | `budget_exhausted` | 1/4 通过，三个棋局 FEN 不匹配 | 输出省略或错误维护 castling、en-passant 等 FEN 状态字段 |
| `sam-cell-seg` | `model_stopped` | 1/9 通过，转换命令退出 2 | 模型认为依赖和权重不可用后停止，最终脚本不能生成 CSV |
| `sanitize-git-repo` | `verified` | secret 仍在、替换错误、改了额外文件 | 内部检查没有扫描完整目标集合，也没有约束修改范围 |
| `schemelike-metacircular-eval` | `verified` | 58/63 通过 | 简单 I/O、闭包和计算器等 5 个解释器用例仍失败 |
| `torch-pipeline-parallelism` | replay | world size 2 反向传播最大差 0.01778 | AFAB pipeline 的跨 rank 梯度时序或累积不一致 |
| `train-fasttext` | `budget_exhausted` | `model.bin` 不存在 | 训练未在 1800 秒内产出模型 |
| `video-processing` | `verified` | 起跳或落地帧偏 1 帧 | 边界检测规则与 verifier 的闭区间定义不一致 |
| `vulnerable-secret` | `model_failure` | 3/3 失败，`results.txt` 不存在 | 2 turns 后触发供应商 `cyber_policy` |

### 归因汇总

| 失败边界 | 数量 | 说明 |
|---|---:|---|
| 内部 `verified` 假阳性 | 10 | Harness 接受的检查没有覆盖官方消费者语义 |
| 预算耗尽 | 10 | 9 个 `harness_control`，1 个 `model_protocol` |
| 模型服务失败 | 4 | 三次 `cyber_policy`，一次墙钟余量耗尽 |
| 模型主动停止 | 4 | 三个 `model_blocked`，一个 `model_impossible` |
| Replay 后确认产物缺陷 | 2 | replay 只恢复评分，不改变原解法 |

这 30 个结果不是同一种“模型没做出来”。最直接的 Harness 缺口是 10 个假阳性：
completion reviewer 接受了局部或自写检查，而官方 verifier 仍找到内容、性能、文件布局
或端到端行为错误。预算耗尽的 10 题则需要更早选择决定性验证和更少的重复探索。模型服务
与依赖缺失应继续单独报告，不能通过增加 prompt 文字解决。

## 控制循环轨迹

`scripts/analyze_failure_traces.py` 从 `evaluation/canonical-89.json` 追到原始 Harness
journal，并跳过没有 Harness metadata 的 replay trial。它只分析上述 10 个内部
`verified` 假阳性和 10 个 `budget_exhausted`：

| 指标 | `verified` 假阳性 | `budget_exhausted` |
|---|---:|---:|
| 任务数 | 10 | 10 |
| finish 尝试 | 31 | 12 |
| completion review | 20 | 6 |
| reviewer 要求 repair | 18 | 6 |
| completion rejection | 21 | 12 |
| 到达过 finish 的任务 | 10 | 5 |
| 首次 finish 中位回合 | 8 | 35 |
| 首次 finish 时剩余回合中位数 | 32 | 5 |
| 最终仍剩余的环境调用中位数 | 48.5 | 36.5 |
| 最后五次决策仍有 change 的任务 | 5 | 10 |
| 出现重复 plan item 的任务 | 2 | 9 |

这组数据排除了“环境调用额度普遍不足”作为主因。假阳性组有 31 次 finish，却只有 20 次
review，说明两次 review 上限在旧控制器中变成了语义检查旁路。预算组的环境调用仍充足，
但一半任务从未进入 finish，另一半首次 finish 已到第 35 回合。

基于该证据，后续控制器改为先执行 completion checks，再让 reviewer 读取实际 receipt；
启用 review 时，配额耗尽或 reviewer 故障都失败关闭。最后三个 executor 决策进入
controller 强制的 finalization，保留一次 finish、一次定向 repair 和一次 revised
finish 的路径。失败的 change receipt 不再重置停滞计数，`max_repairs=N` 只允许 N 次
repair。设计与取舍见
[receipt-aware completion control](completion-control-design.md)。

这些是 canonical 评测完成后的控制器修正，尚未产生新的 89 题分数。当前 59/89 仍只代表
已冻结 trial。

可复算命令：

```bash
uv run python scripts/analyze_failure_traces.py \
  --json-out /tmp/harness-failure-traces.json
```

## 20 题阶段性问题记录

以下案例记录评测过程中的 Harness 和基础设施修正。它们解释最终全量运行的配置来源，
不替代上面的 89 题终态。

每道题的任务目标、执行方法、内部停止状态和设计启示见
[前 10 题逐题分析](ten-task-analysis.md)。

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

## 9. QEMU 阶段性失败与最终恢复

**现象**

阶段性 `qemu-startup` trial 的容器可以找到 `qemu-system-x86_64`，但没有
`/dev/kvm`。Agent 提取并检查 Alpine 内核和 initramfs 后启动 QEMU，进程立即退出：

```text
rosetta error: Unimplemented syscall number 282
```

随后一次模型请求停滞，Harbor 最终记录 `AgentTimeoutError`。该 trial 属于阶段性
20 题记录，汇总器因其带异常而将它记为 `error`，不记为普通评分失败。

**根因**

宿主是 Apple Silicon。amd64 任务容器通过 Rosetta 运行，容器内又启动 x86 QEMU。该次
尝试命中了 Rosetta 不支持的 syscall 282，且容器没有 KVM 可回退。这个证据只能解释该
次失败，不能证明任务在当前宿主上必然无法完成。

**修正与验证**

全量恢复阶段为 `qemu-startup` 和 `qemu-alpine-ssh` 使用任务级 Bullseye main 软件源
重新评分。前者在 36 turns、35 次环境调用后进入 `verified`，后者在 40 turns、45 次
环境调用后达到预算边界；两题的官方 verifier 都以 1/1 通过，最终 reward 均为 1.0。

第一次全量 QEMU 重跑遗漏了 `api_base`，两题均在 turn 0 以 `model_failure` 结束。该次
运行是无效配置探测，未进入 canonical。最终清单只选择配置完整的
`full89-terra-qemu-rescore2-20260927` 结果。

## 10. 查询正确但性能不达标

**现象**

`query-optimize` 首次运行通过了结果一致性、数据库只读和 SQL 格式检查，但 solution
中位耗时为 0.996 秒，参考查询为 0.750 秒。它超过 verifier 允许的 5% 波动范围，最终
reward 为 0。Harness 内部也因 reviewer 反复要求证明“尽可能快”而耗尽预算。

**根因**

首次解法停在“结果正确且结构看起来合理”，没有把至少两个实质不同的查询方案放在等价
条件下比较。另一方面，reviewer 把主观最高级误解为需要证明全局最优，提出了无法在有限
预算内完成的要求。

**修正与验证**

executor 现在要求优化任务比较至少两个不同候选；reviewer 接受可信的实测改进和结构
证据，不要求证明全局最优。恢复 trial 的 solution 中位耗时为 0.669 秒，参考查询为
0.817 秒，快 1.22 倍。6 项 verifier 测试全部通过，Harness 以 `verified` 结束。

## 11. Docker 镜像拉取 EOF

**现象**

`multi-source-data-merger` 首次运行尚未启动 Agent。Docker 在读取 Docker Hub manifest
时返回 EOF，Harbor 记录 `RuntimeError`，没有 reward。

**归因与处理**

这是任务容器准备阶段的瞬时网络错误，不是模型、任务解法或 Harness 控制循环失败。保留
原 trial 后，对该题执行一次独立恢复运行。镜像正常启动，Agent 在 5 turns、8 次环境
调用后进入 `verified`，官方 verifier 的 3 项测试全部通过。20 题冻结快照选择恢复
trial，并通过原始 `result.json` SHA-256 保留来源。

## 12. 模型供应商安全策略拒绝

**现象**

`vulnerable-secret` 的初次和恢复运行都在模型调用阶段返回
`BadRequestError/cyber_policy`。第二次运行只完成 4 turns 和 6 次环境调用，Harness
随后以 `model_failure/model_service` 结束，Harbor reward 为 0。

**归因**

任务在隔离 benchmark 沙箱内执行，但供应商仍按请求内容触发网络安全策略。Harness 已在
prompt 中明确“授权评测、仅操作当前一次性沙箱、不得访问外部系统”，重跑仍被拒绝。这是
当前模型通道的策略边界，不能通过重复请求或弱化任务描述绕过。

**处理**

最终报告把该题记为普通 `failed`，不伪装成基础设施 `error`，也不尝试规避供应商策略。
若要复测，应使用明确获准执行此类安全评测的模型通道。

## 13. 生成产物的自证与消费者语义不一致

**现象**

`dna-assembly` 首次运行在 31 turns 后得到 Harness `verified`，但官方 verifier 返回
reward 0。失败点是 EGFP 引物对的 Tm 差值为 7.60°C，超过任务要求的 5°C。Agent 自己的
检查曾报告 BsaI 位点和环形拼接都通过。

**根因**

完成检查用一套手写逻辑解释引物边界，并验证模型期望的片段；它没有完全复现下游消费者
对完整引物、退火区和线性模板顺序的解析方式。检查与产物共享了同一个错误假设，因此
Harness 得到假阳性的内部 `verified`。

**修正与验证**

executor 和 reviewer 现在要求从已保存字节读取生成产物，并使用目标工具或格式消费者的
约定解析完整结构，不能硬编码“预期片段”自证。恢复 trial 检查了完整扩增产物、每个产物
仅有两个终端 BsaI 位点、三个线性模板不环绕，以及最终环形拼接序列。Harness 在
22 turns、25 次环境调用后进入 `verified`，官方 verifier 通过。

## 14. 阶段性 20 题状态

- 20/20 题已尝试，execution coverage 为 100%。
- 18 题获得 reward 1.0，`vulnerable-secret` 是唯一普通 `failed`。
- `qemu-startup` 是唯一 `error`，attempted pass rate 为 90%。
- 19 个可评分 trial 中 18 个通过，scored pass rate 为 94.7%，scored coverage 为 95%。
- 18 个通过结果中有 17 个 live trial 和 1 个 replay trial。

## 15. 全量 89 题状态

- 89/89 题已执行并评分，execution coverage 和 scored coverage 均为 100%。
- 59 题获得 reward 1.0，30 题获得 reward 0，通过率为 66.3%。
- Canonical `error` 为 0，`evaluation/matrix-89-errors.json` 不含待重试任务。
- 85 个 live trial 得到 57 passed 和 28 failed。
- 4 个 replay trial 得到 2 passed 和 2 failed，均保留源 journal SHA-256。
- `evaluation/canonical-89.json` 固定每题采用的原始结果，`evaluation/trials-89/` 保存
  对应的脱敏快照。
