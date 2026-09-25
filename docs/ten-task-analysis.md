# 前 10 题逐题分析

## 分析范围

本文分析固定 10 题矩阵。结论来自提交到仓库的脱敏 trial 快照、Harbor reward 和本地
Agent journal。分析不读取 Terminal-Bench 的 solution 或 verifier 实现。

这 10 题全部产生了 Harbor trial。9 题获得 reward 1.0，`qemu-startup` 因运行环境
错误获得 reward 0。9 个通过结果包含 8 个 live trial 和 1 个 journal replay，因此
90% attempted pass rate 不是同一模型的一次完整成绩。

| Task | Reward | Mode | Harness stop | Turns | Env calls | 主要结论 |
|---|---:|---|---|---:|---:|---|
| `overfull-hbox` | 1 | live | `budget_exhausted` | 30 | 31 | 解法正确，完成检查契约不适合会生成文件的验证 |
| `fix-git` | 1 | live | `verified` | 6 | 9 | 证据门禁和 Git 状态恢复配合良好 |
| `cobol-modernization` | 1 | live | `budget_exhausted` | 7 | 13 | 差分逆向有效，模型空响应阻止内部收尾 |
| `log-summary-date-ranges` | 1 | live | `verified` | 7 | 13 | 两套独立计数验证提高了结果可信度 |
| `openssl-selfsigned-cert` | 1 | live | `verified` | 7 | 15 | 文件、密码学属性和脚本行为均有独立检查 |
| `modernize-scientific-stack` | 1 | replay | N/A | N/A | N/A | 轨迹可复现，但不是新的模型调用 |
| `qemu-startup` | 0 | live | `AgentTimeoutError` | 5 | 8 | Apple Silicon 和 Rosetta 无法提供任务所需运行时 |
| `cancel-async-tasks` | 1 | live | `verified` | 13 | 20 | 行为测试覆盖并发上限与取消清理 |
| `configure-git-webserver` | 1 | live | `budget_exhausted` | 11 | 16 | 服务链路正确，reviewer 与最终状态要求不一致 |
| `model-extraction-relu-logits` | 1 | live | `budget_exhausted` | 17 | 17 | 黑盒恢复成功，隔离验证能力不足 |

## `overfull-hbox`

任务只允许用 `synonyms.txt` 中的同义词替换 `input.tex` 的单词，同时要求
`pdflatex` 编译成功且没有 overfull hbox 警告。

Agent 先编译原始文档并定位警告段落，再把段落中的单词映射到允许的同义词集合。它逐步
尝试更短的合法替换，并在临时副本中比较警告数量。最终文档通过 Harbor verifier。

该题的主要问题不是解法。Agent 用了 30 turns、31 次环境调用和 373,003 个输入 token。
completion reviewer 连续五次要求重新编译或检查编译产物，但 `finish` policy 禁止检查
生成 PDF 和日志。Agent 已在隔离目录中成功编译，Harness 仍以
`budget_exhausted/model_protocol` 结束。

这道题说明只读检查不能等同于“进程不写任何文件”。编译器验证天然会产生临时文件。
Harness 需要为 completion check 提供一次性工作目录，并在检查结束后丢弃该目录。直接
允许检查写入 `/app` 会让完成检查改变待评分状态，不可接受。

## `fix-git`

任务要求找回离开分支后丢失的修改并合并到 `master`。Agent 检查 reflog、dangling
commits、分支和 stash，定位到包含目标修改的 dangling commit。合并产生冲突后，Agent
读取 base、ours 和 theirs，选择包含目标内容的版本并创建 merge commit。

最终检查验证三个事实：目标提交是 `master` 的祖先、目标页面包含恢复内容、工作区没有
冲突或未提交修改。Harness 在 6 turns 和 9 次环境调用后进入 `verified`，Harbor
reward 为 1.0。

这道题最能体现 Evidence Harness 的价值。任务状态可以用 Git 图、文件内容和工作区状态
直接证明。修复 `merge-base` 和冲突标记的 policy 误报后，模型不再重复提交同一组完成
检查。对比基线，输入 token 减少 62.9%。

## `cobol-modernization`

任务要求把 COBOL 程序重写为 `/app/program.py`，并保持对 `.DAT` 文件的字节级行为
一致。Agent 先读取 COBOL、输入文件和数据文件，再用 GnuCOBOL 构造多组参考执行。测试
覆盖完整记录、短记录、存在和不存在的 book，以及无符号金额下溢。

Agent 写出 Python 实现后运行了 10 组差分测试，并继续检查两个不一致用例的 stdout、
stderr 和退出码。评测期间模型连续返回三次空正文，Harness 在内部完成声明前耗尽预算。
Harbor verifier 仍给出 reward 1.0，说明最终 Python 文件满足任务要求。

差分执行是这类迁移任务的正确方法，因为 COBOL 的定长字段、空格填充和数值下溢很难仅靠
静态阅读推断。当前缺口是模型通道恢复。空响应修复已让新请求进入 schema repair，但
未来还应保存“最后一次已通过差分测试”的明确 checkpoint，以便模型故障后直接进入完成
审查。

## `log-summary-date-ranges`

任务要求按 today、最近 7 天、最近 30 天、当月和全部数据统计三种日志级别，并生成固定
顺序的 CSV。Agent 先确认日期来自文件名，severity 来自每行的方括号字段。随后写入
汇总脚本并生成 `/app/summary.csv`。

Agent 使用两种独立方法复算 15 个计数。第一种按日期集合配合 `grep`，第二种用 `awk`
重新扫描。它还修复了 CSV 的 CRLF 行尾，并验证文件正好有 16 行。一次模型空响应通过
repair 恢复，最终状态是 `verified`，reward 为 1.0。

该题说明验证命令不应只是再次调用被测脚本。两套独立计数降低了实现和检查共享同一错误
的风险。额外的行尾检查也覆盖了 verifier 常见的字节级要求。

## `openssl-selfsigned-cert`

任务要求生成 RSA-2048 私钥、自签名证书、组合 PEM、验证文本和一个 Python 检查脚本。
Agent 创建文件后，分别验证私钥权限、密钥长度、subject、issuer、365 天有效期、密钥与
证书匹配、自签名和 SHA-256 fingerprint。

completion reviewer 两次拒绝覆盖不足的检查。Agent 随后把“私钥文件本身是 RSA-2048”
和“完整 issuer 等于完整 subject”等条件加入最终检查。Harness 在 7 turns 和 15 次
环境调用后进入 `verified`，Harbor reward 为 1.0。

reviewer 在这道题上产生了实际价值。它没有重做证书生成，而是发现检查只证明了部分属性。
代价是两轮额外审查。后续可把证书类任务的常见检查结构放入 prompt 示例，但不应把
OpenSSL 专用逻辑写死到 Harness。

## `modernize-scientific-stack`

任务要求把 Python 2 气候分析脚本迁移到 Python 3，并使用 pandas、`pathlib.Path` 和
现代配置读取方式。记录的轨迹读取旧脚本、配置和样本 CSV，创建现代脚本与依赖文件，
然后检查语法、运行输出和 Python 2 遗留符号。

该轨迹在原始 live 运行中进入 `verified`。当前矩阵使用 `JournalReplayAgent` 在新容器
重放命令，官方 verifier 返回 reward 1.0。报告将该项标为 `replay`，并保存源 journal
SHA-256。

replay 证明同一组操作可以重建通过状态，但不证明模型端点在当前时间仍能生成同样的决策。
因此该结果可以用于 Harness 和环境复现，不能用于同模型 live 通过率比较。

## `qemu-startup`

任务要求启动 Alpine ISO，并在 `127.0.0.1:6665` 暴露可见登录提示的 telnet 串口。
Agent 检查 QEMU、KVM、ISO 内容和启动参数，提取 kernel 与 initramfs 后启动
`qemu-system-x86_64`。

QEMU 立即返回 `rosetta error: Unimplemented syscall number 282`。容器没有
`/dev/kvm`，因此没有硬件虚拟化回退路径。随后模型请求停滞，Harbor 最终记录
`AgentTimeoutError`。汇总器把该 trial 记为 `error`，而不是普通 `failed`。

根因是运行环境。Apple Silicon 宿主使用 Rosetta 执行 amd64 容器，容器内再次启动 x86
QEMU。模型和 prompt 无法提供缺失的系统调用或 KVM。该题必须在原生 x86_64 Linux
runner 上重跑。单次模型超时只能缩短失败时间，不能修复虚拟化能力。

## `cancel-async-tasks`

任务要求实现有并发上限的异步任务调度，并保证外部取消时已启动任务的 `finally` 清理能
完成。Agent 使用 `asyncio.TaskGroup` 管理子任务，用 `asyncio.Semaphore` 限制并发。

行为测试记录峰值并发并确认全部任务完成。取消测试让两个任务进入等待状态，取消父任务，
再确认两个任务都完成带 `await` 的 `finally` 清理且 `CancelledError` 继续向外传播。
reviewer 三次要求补强模块路径、注解和异步清理证明。最终 Harness 进入 `verified`，
Harbor reward 为 1.0。

实现本身很短，13 turns 和 20 次环境调用主要消耗在完成审查。reviewer 的要求都与任务
契约有关，但存在重复。后续应让 reviewer 引用已覆盖的 requirement ID，并只返回新增
缺口，减少同义改写造成的重复检查。

## `configure-git-webserver`

任务要求把 SSH Git push、bare repository、post-receive 部署和 8080 端口 Web 服务连成
完整链路。Agent 安装 Git、OpenSSH 和 Python，创建用户与 `/git/server`，配置 deploy
hook，启动 sshd 和 HTTP 服务，并运行真实 clone、commit、push、curl 测试。

最终环境能通过 `server` 主机名返回 `hello world`，Harbor reward 为 1.0。completion
reviewer 连续五次改变对“空仓库还是保留测试 push”“最终状态还是可重复操作”的要求，
Harness 最终以 `budget_exhausted/model_protocol` 停止。

该题暴露了验收目标的时间语义。任务要求服务可用，不要求最终仓库为空。端到端 push 已经
证明服务链路，后续清理反而可能撤销证据。Harness 应让 reviewer 区分“证明能力的测试”
和“任务要求的最终状态”，避免在两者之间反复切换。

## `model-extraction-relu-logits`

任务要求只通过 `forward(x)` 查询一层 ReLU 网络，并恢复与 `A1` 在行置换和缩放意义上
等价的矩阵。Agent 最初尝试读取可见模型信息，reviewer 拒绝后改为纯查询方法。最终实现
通过沿多条直线扫描梯度跳变定位 ReLU 分界面，并从梯度差恢复每个隐藏单元的方向。

Agent 使用代理 `forward()` 验证脚本只通过查询获取模型信息，并检查保存矩阵与打印矩阵
一致。它还用不同隐藏宽度的黑盒 fixture 检查宽度推断。Harbor verifier 返回 reward
1.0。

Harness 内部仍以 `budget_exhausted/model_protocol` 结束。reviewer 要求完成检查重新
运行脚本并保存 `.npy`，但 policy 禁止 `finish` 修改任务目录。这与
`overfull-hbox` 是同一类契约问题。隔离验证工作区可以让脚本生成临时矩阵，同时保证
评分目录保持不变。

## 跨题结论

前 10 题显示了三类问题：

- **任务解法问题。** 没有可评分 trial 因最终解法错误而失败。9 个可评分结果全部获得
  reward 1.0。
- **Harness 完成协议问题。** `overfull-hbox`、`configure-git-webserver` 和
  `model-extraction-relu-logits` 的 reviewer 与 policy 不一致。`cobol-modernization`
  受模型空响应影响，未进入内部完成状态。
- **评测环境问题。** `qemu-startup` 缺少所需系统调用和 KVM。该错误不能用 prompt
  优化解决。

下一轮优先级是隔离 completion check 的写入、让 reviewer 只报告新增覆盖缺口，以及在
原生 x86_64 Linux 上重跑 QEMU。新增 10 题继续使用并发 1，并保留
`passed`、`failed`、`error`、`not_run` 和 `live`、`replay` 的区别。扩展运行已完成，
最终 20 题统计和恢复过程见[评测报告](evaluation-report.md)与
[20 题冻结结果](../evaluation/results-20.md)。
