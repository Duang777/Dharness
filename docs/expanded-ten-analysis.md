# 新增 10 题逐题分析

## 分析范围

本文分析 20 题矩阵中的后 10 题。结论来自脱敏 trial 快照、Agent journal、命令回执和
Harbor reward。本文不记录恢复出的密码或 flag，也不把 Harness 内部 `verified` 当作
外部评分。

新增 10 题最终有 9 题通过、1 题失败。首次运行得到 6 个通过、3 个评分失败和 1 个环境
错误。恢复批次只重跑四个未通过任务，其中 3 题恢复通过，`vulnerable-secret` 再次被
供应商策略拒绝。

| 任务 | 奖励 | Harness 停止原因 | Turns | 环境调用 | 核心结论 |
|---|---:|---|---:|---:|---|
| `prove-plus-comm` | 1 | `budget_exhausted` | 16 | 18 | Coq 证明正确，完成审查重复追加要求 |
| `regex-log` | 1 | `verified` | 22 | 35 | Python 消费者语义和边界矩阵覆盖完整 |
| `nginx-request-logging` | 1 | `budget_exhausted` | 17 | 20 | 服务状态正确，审查在运行时证明上反复加码 |
| `extract-elf` | 1 | `budget_exhausted` | 18 | 20 | ELF 内存映像恢复正确，内部完成协议未收敛 |
| `query-optimize` | 1 | `verified` | 21 | 25 | 集合式查询通过等价性与性能检查 |
| `vulnerable-secret` | 0 | `model_failure` | 4 | 6 | 两次运行均被供应商 `cyber_policy` 拒绝 |
| `multi-source-data-merger` | 1 | `verified` | 5 | 8 | 首次镜像拉取失败，恢复运行快速通过 |
| `fix-code-vulnerability` | 1 | `verified` | 8 | 15 | 定位并修复 CWE-93，完整测试通过 |
| `password-recovery` | 1 | `verified` | 18 | 29 | 通过文件系统残留和 CRC 证据恢复结果 |
| `dna-assembly` | 1 | `verified` | 22 | 25 | 首轮自证错误，改用消费者语义后通过 |

## `prove-plus-comm`

任务要求补全自然数加法交换律的 Coq 证明，并生成 `plus_comm.vo`。Agent 使用归纳法，
在基础分支应用 `plus_n_O`，在后继分支结合归纳假设和 `plus_n_Sm`。`coqc` 编译和 Coq
内核检查均成功，Harbor reward 为 1.0。

Harness 没有进入 `verified`。reviewer 连续要求证明源码没有 `Admitted`、定理类型准确、
编译产物比源码新且没有额外假设。检查本身合理，但 5 次 repair 中有多项是同一完成事实
的不同表达。

这道题说明 reviewer 应返回增量缺口，而不是每轮重新描述完整理想检查。对于证明助手，
源码无逃逸、目标定理类型和内核接受已经构成足够的三层证据。

## `regex-log`

任务要求生成一个供 Python `re.findall(..., re.MULTILINE)` 使用的正则。它必须在含合法
IPv4 的行中提取最后一个合法日期，并拒绝前后紧邻字母数字字符的伪匹配。

Agent 没有只检查正则能否编译。最终行为矩阵覆盖了每个 IPv4 octet 的 0 至 255、
前导零、越界值、各月份日期上限、任意四位年份、日期与 IP 的先后顺序，以及一行多个
日期时只返回最后一个。检查还确认正则只有一个捕获组，避免 `findall` 返回 tuple。

该题在 22 turns、35 次环境调用后进入 `verified`。高调用数主要来自 reviewer 逐步补齐
边界域。架构上应把“目标消费者如何解释产物”作为第一轮检查设计输入，而不是在审查阶段
逐项追加。

## `nginx-request-logging`

任务要求安装并启动 Nginx，配置 8080 端口、静态文件、详细访问日志、错误日志、每 IP
限速和自定义 404 页面。Agent 完成配置后验证 `nginx -t`、监听端口、首页和 404 响应，
并检查访问日志中的方法、状态码和带双引号的 User-Agent。

Harbor reward 为 1.0，但 Harness 在 5 次 repair 后以 `budget_exhausted` 结束。
reviewer 先要求证明配置文件存在，再要求证明配置已加载，最后要求用并发请求证明限速
实际生效。后两项比静态配置检查更强，但 reviewer 没有复用上一轮已覆盖的要求。

服务配置任务需要区分“声明式配置存在”和“运行时行为生效”。两者都需要证据，但完成
审查应只返回尚未覆盖的差额，否则正确任务也会消耗完审查预算。

## `extract-elf`

任务要求实现 `extract.js`，从 ELF 二进制中输出内存地址到整数值的 JSON 映射，并覆盖
参考结果的至少 75%。Agent 实现了 ELF32 和 ELF64、大小端、`PT_LOAD` segment、文件
字节和 `p_memsz - p_filesz` 零填充。目标文件最终输出 700 个完整的 32 位内存值。

验证没有调用实现自身作为唯一 oracle。独立检查重新解析 program header，并逐项比较
地址和值。Harbor reward 为 1.0。Harness 仍因 reviewer 对对齐和尾部字节语义的重复
追问而耗尽 repair。

该题支持保留完整产物语义检查，但也说明 reviewer 需要接受等价的独立实现证据。重复
要求第三种解析器不会改变结论，只会增加 token。

## `query-optimize`

任务要求优化 Open English Wordnet SQLite 查询，同时保持输出完全一致。首次解法通过
正确性检查，但官方测试中 solution 中位耗时为 0.996 秒，参考查询为 0.750 秒，因此
reward 为 0。

恢复运行比较多个候选，最终使用集合式聚合替换相关标量子查询。Agent 的绑定回执在单次
测量中记录了更大的加速，但官方 verifier 的五轮交替测试更保守：solution 中位耗时
0.669 秒，参考查询 0.817 秒，加速 1.22 倍。6 项官方测试全部通过。

架构上的结论不是要求模型证明全局最优。合理标准是输出严格等价、执行计划消除已知瓶颈，
并在相同设置下获得稳定改进。Prompt 已把这三项写成优化任务的证据要求。

## `vulnerable-secret`

任务要求在隔离容器中分析本地可执行文件并把提取结果写入任务文件。初次运行和恢复运行
都在模型调用阶段收到 `BadRequestError/cyber_policy`。第二次运行在 4 turns、6 次环境
调用后以 `model_failure/model_service` 结束，Harbor reward 为 0。

Harness 已明确声明这是授权评测，只允许操作当前一次性沙箱，也禁止访问外部系统。供应商
仍拒绝请求，因此本轮没有继续改写任务描述或反复重试。

该题体现了模型能力与模型通道策略的区别。最终状态保留为 `failed`，因为 trial 正常结束
且 verifier 返回 0；根因分析则明确标记为 provider policy。后续只能换用明确允许此类
授权评测的通道。

## `multi-source-data-merger`

任务要求读取 JSON、CSV 和 Parquet 三种来源，统一字段名，按来源优先级合并用户，并生成
Parquet 结果和 JSON 冲突报告。首次 trial 在 Docker Hub manifest 拉取阶段遇到 EOF，
Agent 尚未启动，也没有 reward。

恢复运行完成 4 个用户和 3 个字段冲突的合并。独立检查重新读取三种来源，验证字段映射、
优先级、日期格式、整数 ID、Parquet schema 和冲突报告内容。Harness 在 5 turns、
8 次环境调用后进入 `verified`，3 项官方测试全部通过。

这道题说明容器准备错误应与 Agent 失败分开。恢复策略保留原 trial，只替换最终矩阵中的
选定来源，既恢复执行又保留故障证据。

## `fix-code-vulnerability`

任务要求识别 Bottle 响应头处理中的 CWE，并修改实现，使非法输入抛出准确异常。Agent
定位到响应头名称和值缺少 CR、LF 和 NUL 校验，对应 CWE-93。修复覆盖构造函数、赋值、
`set_header` 和 `add_header`，同时生成要求的 `report.jsonl`。

完成检查验证报告格式和 CWE ID，验证四条入口都对控制字符抛出 `ValueError`，并运行
完整项目测试。Harness 在 8 turns、15 次环境调用后进入 `verified`，Harbor reward
为 1.0。

该题适合当前架构。漏洞位置、错误类型和回归行为都能由确定性测试证明，reviewer 只需
检查需求覆盖，不需要推断隐藏状态。

## `password-recovery`

任务要求从已删除文件的磁盘残留中恢复一个满足固定格式的密码，并写入结果文件。Agent
从残留 ZIP 结构中关联 local header 和 central directory，使用文件名、长度、偏移和
CRC 约束重建候选。本文不记录恢复出的具体密码。

最终检查证明输出只有一行，满足长度、字符集、前缀和后缀约束，并与 CRC 支持的文件记录
一致。Harness 在 18 turns、29 次环境调用后进入 `verified`，Harbor reward 为 1.0。

该题说明取证结果不能只靠格式匹配。格式只能缩小候选范围，来源位置和 CRC 才能把输出
绑定到被删除文件。

## `dna-assembly`

任务要求为一个环形载体和三个线性片段设计最少数量的 BsaI-HF v2 Golden Gate 引物。
首轮 Agent 生成 4 对引物并通过自写拼接检查，Harness 也进入 `verified`。官方 verifier
随后发现 EGFP 引物对的 Tm 差值为 7.60°C，超过 5°C 上限。

问题不在缺少检查，而在检查使用了与生成逻辑相同的片段边界假设。恢复运行改为从
`primers.fasta` 的完整字节重新解析每条引物，按消费者约定计算退火区，验证完整扩增产物
只有两个终端 BsaI 位点，禁止线性模板环绕，并重新构造最终环形序列。

恢复 trial 在 22 turns、25 次环境调用后进入 `verified`，官方 verifier 通过。这个案例
直接推动了 Prompt 规则：生成结构化产物后，必须使用目标工具或消费者语义解析完整产物，
不能用第二套硬编码解释证明模型原本想生成的值。

## 跨题结论

新增 10 题给出四个结论：

1. 明确、可执行的产物契约最容易形成内部和外部一致的通过结果。ETL、安全修复和取证任务
   都能把要求映射为独立检查。
2. reviewer 对证明、服务和二进制格式任务容易重复提高证据标准。7 个 live 通过任务在
   全部 20 题统计中仍以 `budget_exhausted` 结束，完成协议是下一阶段的主要优化目标。
3. 外部 verifier 能发现自写检查的共同假设。`dna-assembly` 说明检查数量不能替代检查
   独立性。
4. 恢复动作必须服从故障分类。算法不足需要换方案，网络 EOF 可以重试，provider policy
   不能绕过，宿主虚拟化缺失必须换 runner。
