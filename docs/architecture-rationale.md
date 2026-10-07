# Evidence Harness 架构决策

## 问题

Terminal-Bench 2.0 的任务横跨系统运维、数据处理、服务配置和代码修改。Harness 必须保留通用 shell 能力，同时限制重复操作、上下文膨胀和没有证据的提前结束。Harbor 0.23.0 在宿主进程中加载自定义 `BaseAgent`，Agent 再通过 `BaseEnvironment.exec` 操作隔离环境。这个边界允许控制器直接获得命令退出码，无需在每个任务容器中安装 CLI。

## 核心架构判断

终端 Agent 的主要风险不是模型不会生成命令，而是模型同时承担计划、执行、记忆和完成
判定后，系统没有独立事实来源。一次错误观察、过期测试或模型空响应都可能污染后续判断。
Evidence Harness 因此把模型视为不稳定的决策组件，而不是系统状态的权威。

设计围绕五个不变量展开：

| 不变量 | 约束 | 目的 |
|---|---|---|
| 单一任务写者 | 模型命令只通过 `CommandRunner` 进入任务环境；完成检查只在快照子容器中运行 | 让任务修改可归因，并阻止检查污染待评分状态 |
| 状态由控制器持有 | 预算、epoch、回执和停止原因存入 `RunState` | 模型失忆或重试不会改写事实 |
| 证据必须新鲜 | 修改后递增 `work_epoch`，旧检查立即失效 | 防止“先测试通过，再修改出错” |
| 完成需要双重判定 | Harness 审核证据，Harbor verifier 评分 | 避免内部启发式冒充任务真相 |
| 恢复必须有上限 | repair、recovery、turn、调用和墙钟都有预算 | 失败能够终止、分类和复现 |

这五个不变量决定了模块边界。`protocol.py` 定义模型能说什么，`run_loop.py` 决定状态
如何变化，`shell.py` 是唯一副作用入口，`evidence.py` 决定完成声明是否成立，
`evaluation.py` 只处理运行结束后的评分事实。

## 三条权威边界

系统把一次评测拆成三个彼此独立的权威来源：

1. **控制权威。** `EvidenceLoop` 决定预算、状态迁移和停止原因。模型不能直接修改这些
   字段。
2. **环境权威。** `CommandRunner` 返回真实退出码、输出摘要和观察哈希。模型文本不能
   替代命令回执。
3. **评分权威。** Harbor verifier 决定 reward。内部 `verified` 只表示完成证据通过
   Harness 门禁，不表示 benchmark 已通过。

这个分离解释了两类看似矛盾的结果。`dna-assembly` 首轮内部 `verified`，但外部
verifier 发现产物错误；另有 7 个任务外部 reward 为 1.0，但内部因完成检查契约冲突而
以 `budget_exhausted` 结束。两种信号都要保留，不能互相覆盖。

## 使用方式

Harbor 只需要一个导入路径：

```bash
uv run harbor run \
  -d terminal-bench/terminal-bench-2 \
  -a evidence_harness.harbor_agent:EvidenceHarnessAgent \
  -m provider/model
```

调用方可以通过 `--agent-kwarg` 调整预算，但无需了解内部状态机：

```bash
uv run harbor run \
  -d terminal-bench/terminal-bench-2 \
  -a evidence_harness.harbor_agent:EvidenceHarnessAgent \
  -m provider/model \
  --ak max_turns=36 \
  --ak max_environment_calls=72 \
  --ak max_wall_time_sec=1200
```

核心循环也可以脱离 Harbor 做确定性测试：

```python
loop = EvidenceLoop(
    model=fake_model,
    journal=journal,
    options=options,
    completion_isolation=fake_isolation,
)
report = await loop.run(instruction, fake_environment)
assert report.stop_reason == StopReason.VERIFIED
```

## 系统边界

Harness 运行在 Harbor 宿主进程中。任务命令只通过 Harbor 提供的
`BaseEnvironment.exec` 进入隔离容器。模型从不持有环境对象，也不能绕过控制器执行
命令。

```mermaid
flowchart TB
    subgraph Host[Harbor 宿主进程]
        H[Harbor runner] --> A[EvidenceHarnessAgent]
        A --> L[EvidenceLoop]
        L <-->|AgentDecision 与 ReviewDecision| G[LiteLLMModelGateway]
        L --> P[Policy]
        P --> C[CommandRunner]
        L --> I[DockerCompletionIsolation]
        L --> V[EvidenceGate]
        C --> J[RunJournal]
        I --> J
        L --> J
        L --> X[AgentContext 与 RunReport]
    end

    G <--> M[模型供应商]
    C --> E[Harbor BaseEnvironment]

    subgraph Task[隔离任务容器]
        E --> S[Shell 与任务文件]
    end

    I -->|pause + commit| S
    I -->|每项检查一个无挂载、无网络子容器| K[候选快照]
```

控制路径和评分路径彼此独立。`EvidenceGate` 决定 Harness 能否声明完成，Harbor
verifier 在 Agent 退出后判断任务是否真正通过。Harness 不读取 verifier 文件，也不把
内部门禁结果当作 benchmark 分数。

从职责上看，`EvidenceLoop`、`Policy` 和 `EvidenceGate` 构成控制平面；
`CommandRunner` 与 `BaseEnvironment` 构成执行平面；Harbor verifier 构成评分平面。
控制平面可以拒绝不安全或证据不足的动作，但不能声明评分成功。评分平面可以判断最终
结果，却不参与 Agent 的中间决策。

## 组件职责

`EvidenceHarnessAgent` 是唯一公开入口。它把 Harbor 注入的模型名、日志目录和环境适配成 `EvidenceLoop` 所需的两个端口：`ModelGateway` 和 `ShellEnvironment`。`EvidenceLoop.run` 隐藏预算、重复检测、命令策略、验证票据和停止条件。

| 模块 | 职责 | 不负责 |
| --- | --- | --- |
| `harbor_agent.py` | 连接 Harbor，解析选项，同步 token、成本和运行状态 | 解题策略和命令执行 |
| `run_loop.py` | 推进状态机，维护预算、恢复次数和 `work_epoch` | 供应商协议和最终评分 |
| `protocol.py` | 定义动作、命令、回执、状态和报告的严格类型 | 执行副作用 |
| `prompting.py` | 从权威状态重建有界 executor 和 reviewer 提示 | 保存对话历史 |
| `model_client.py` | 通过 LiteLLM 调用指定模型，解析 JSON，累计用量 | 直接访问任务容器 |
| `policy.py` | 拒绝危险、无效或重复的命令和完成检查 | 充当容器隔离边界 |
| `shell.py` | 串行调用 `BaseEnvironment.exec` 并生成命令回执 | 判断任务是否完成 |
| `completion_isolation.py` | 定义一次完成验证事务及失败类型 | 依赖具体容器运行时 |
| `docker_completion_isolation.py` | 暂停源容器、提交候选快照、隔离执行检查并清理资源 | 支持 sidecar、任务挂载和服务型检查 |
| `source_binding.py` | 对运行时代码与依赖清单生成可复算的聚合指纹 | 判断实现行为是否正确 |
| `evidence.py` | 检查覆盖关系、检查结果和证据时效 | 替代 Harbor verifier |
| `journal.py` | 脱敏并归档事件和完整命令输出 | 把无限输出送入模型上下文 |
| `evaluation.py` | 汇总固定评测矩阵和 Harbor `result.json` | 参与单题运行控制 |

核心数据结构如下：

```python
class AgentDecision(BaseModel):
    action: Literal["execute", "finish", "replan", "stop"]
    rationale: str
    plan: tuple[str, ...]
    commands: tuple[ShellCommand, ...]
    checks: tuple[VerificationCheck, ...]
    coverage: tuple[RequirementCoverage, ...]


class CommandReceipt(BaseModel):
    sequence: int
    command_id: str
    mode: Literal["observe", "change"]
    work_epoch: int
    return_code: int
    stdout: OutputExcerpt
    stderr: OutputExcerpt
    observation_fingerprint: str


class VerificationReceipt(BaseModel):
    work_epoch: int
    checks: tuple[CommandReceipt, ...]
    coverage: tuple[RequirementCoverage, ...]
    isolation: CompletionIsolationEvidence | None
    semantic_assessment: SemanticAssessment | None
    accepted: bool


class EvidenceLoop:
    async def run(
        self,
        instruction: str,
        environment: ShellEnvironment,
    ) -> RunReport: ...
```

`AgentDecision` 在模型边界解析。进入控制器后，代码只处理已验证的领域对象。`ShellEnvironment` 只暴露一次有界命令执行。reviewer 没有环境引用，只能判断完成证据是否覆盖任务要求。

命令执行后，控制器把完整且脱敏的输出写入日志文件。模型只看到长度受限的首尾摘录、退出码、摘要哈希和日志引用。每次模型调用都从权威状态重新构造提示，不累计完整对话。这避免旧输出持续占用上下文，也防止 shell 输出覆盖固定规则。

完成状态要求至少一条非平凡、只读、重新执行的检查。所有检查必须发生在最后一次可能
修改环境的命令之后。完成 reviewer 再按原始任务逐项检查证据覆盖。reviewer 只能返回
接受或修复，不能执行命令，也不能替代 Harbor 的最终 verifier。

## 运行时状态机

```mermaid
stateDiagram-v2
    [*] --> BOOTSTRAPPING
    BOOTSTRAPPING --> THINKING: bootstrap 成功
    BOOTSTRAPPING --> TERMINATED: 环境故障
    THINKING --> EXECUTING: execute
    EXECUTING --> THINKING: 命令批次结束
    THINKING --> THINKING: replan
    THINKING --> VERIFYING: finish 提案通过结构校验
    VERIFYING --> REPAIRING: 隔离检查或机械门禁失败
    VERIFYING --> REVIEWING: 隔离检查通过
    REVIEWING --> TERMINATED: reviewer 与 EvidenceGate 接受
    REVIEWING --> REPAIRING: reviewer 拒绝
    REPAIRING --> THINKING: 修复预算仍充足
    THINKING --> TERMINATED: stop、预算耗尽或模型故障
    EXECUTING --> TERMINATED: 恢复预算耗尽
```

状态机从 `BOOTSTRAPPING` 开始。固定 bootstrap 命令读取工作目录、平台、浅层文件、
Git 状态和常见工具位置，避免模型第一轮重复做相同探测。之后每轮进入
`THINKING`，模型只能返回四种结构化动作：

1. `execute`：执行最多四条有目的、有超时、有读写模式的命令。
2. `finish`：提交只读检查和需求与检查 ID 的覆盖表。
3. `replan`：在重复循环或假设失效后显式改变计划。
4. `stop`：把外部阻塞、不安全条件或不可实现状态显式分类。

`execute` 会推进 `work_epoch`，并立刻使旧验证失效。任何声称完成的动作都必须在当前
epoch 重新执行检查。控制器预留三次环境调用给最终验证，所以模型不能用普通工作命令
耗尽全部预算。命令首次失败后，控制器要求模型读取错误并改变方法。重复观察指纹会
触发强制 replan。超过恢复次数、时间、turn 或环境调用预算后由控制器终止。停止原因是
领域枚举，而不是依赖最后一句自然语言猜测。

### `work_epoch` 如何防止使用旧证据

`work_epoch` 是只增不减的工作批次版本号。固定 bootstrap 在 epoch 0 运行。每个
`execute` 批次开始时，控制器先增加 epoch，再清除 `latest_evidence`。完成检查产生的
每条 `CommandReceipt` 都记录当前 epoch。

```mermaid
sequenceDiagram
    participant L as EvidenceLoop
    participant M as 模型
    participant E as 任务容器
    participant I as 隔离子容器
    participant G as EvidenceGate

    L->>E: bootstrap，epoch 0
    M->>L: execute
    L->>L: work_epoch = 1，清除旧证据
    L->>E: 执行修改命令
    M->>L: finish，提交检查和覆盖表
    L->>E: 暂停并提交候选快照
    L->>I: 每条检查在独立子容器执行，epoch 1
    I-->>L: 返回退出码、输出哈希和文件变化
    L->>G: 校验检查回执、隔离证据与 epoch 1
    G-->>L: accepted
```

如果模型在验证后再次选择 `execute`，`work_epoch` 变为 2。epoch 1 的回执即使退出码
为 0，也不能证明 epoch 2 的环境状态。模型必须重新提交并执行检查。

## 命令与证据边界

Shell 是 Terminal-Bench 的必要通用能力，完全改成固定工具集合会损失覆盖率。这里没有
试图消灭 shell，而是把风险集中在执行边界：

- 拒绝空命令、超长命令、超时越界和明显的宿主逃逸模式。
- 每条命令必须声明 `observe` 或 `change`。保守分类优先把不确定操作视为修改。
- 命令脚本和标准输出先做凭证模式脱敏，再写 journal。
- 大输出保留完整归档，提示中只投影固定字节数的 head/tail、SHA-256 和路径引用。
- 相同命令加相同观察结果形成重复周期时，禁止继续机械重试。

证据门禁不判断任务的业务真相，它只证明 Harness 的完成声明满足最低可审计条件：
检查非空、检查 ID 唯一、覆盖表引用有效、脚本不是单纯 `echo/true`，并且检查发生在
最新修改之后。检查可以创建临时文件，但只能写入候选快照的子容器。`EvidenceGate`
拒绝修改或删除快照中已有的非目录路径，也要求源容器在暂停期间保持相同
`docker diff`。最终正确性仍由 Terminal-Bench verifier 决定。

## 完成验证的五层职责

完成流程把结构、隔离执行、机械证据、语义判断和官方评分分开：

1. `EvidenceGate.validate_proposal` 检查命令是否非空、非平凡，并检查覆盖表引用是否
   有效。危险路径和宿主控制命令仍由 policy 拒绝。
2. `DockerCompletionIsolation` 暂停源容器并提交一次候选镜像。每条检查在各自的
   无挂载、无网络子容器中执行。
3. `EvidenceGate.decide` 校验 `attempt_id`、`work_epoch`、receipt 顺序、观察哈希、
   文件变化、源容器不变和资源清理状态。
4. 启用 completion review 时，reviewer 读取已经执行的 receipts 和隔离证据，再判断
   检查是否覆盖原始要求。reviewer 故障或配额耗尽不能降级成成功。
5. Harbor verifier 在 Harness 退出后独立评分。只有获得 reward 的结果进入 scored
   pass rate。

前四层决定 Harness 能否声明 `verified`。第五层保留 benchmark 的唯一评分权。

### 隔离完成检查的支持边界

当前实现只支持 Harbor 0.23.0 的本地 Linux `DockerEnvironment`。运行时必须只有一个
Compose `main` 容器，并且不能有 sidecar、任务挂载、镜像 `VOLUME`、设备请求、
特权模式或后台进程。服务型完成检查也不支持。任一条件不满足时，Harness 以
`completion_isolation_unsupported` 结束，不会退回源容器执行检查。

每次完成尝试先暂停源容器，再记录源容器的规范化 `docker diff` 并创建一个候选镜像。
每条检查从同一镜像启动独立子容器。子容器使用 `--network none`，不继承 Harbor 的
日志挂载。检查结束后，provider 记录子容器文件变化并删除子容器。最后再次读取源容器
diff，先恢复源容器，再删除候选镜像。普通操作为清理预留 95 秒，全部清理操作共用
90 秒硬截止时间；即使 `docker pause` 的客户端调用超时，也会发送 `unpause` 并检查
源容器状态。
清理失败会使整个尝试失败。

静态支持 census 对 89 个任务的 `task.toml` 和环境目录执行生产代码中的拒绝规则。
它只能证明任务源码没有触发静态拒绝条件，不能证明运行时容器、进程树或检查依赖可用。
CI 从绑定数据集 source commit 和矩阵 SHA-256 的最小源码快照还原这些文件，再运行相同
的生产拒绝规则；维护者也可以直接用完整 Harbor 缓存重建。已提交报告不参与判定。
三项冻结候选的前瞻实验补充了运行时证据：`3/3` 完整重放命令轨迹并保持退出状态，
`2/3` 通过机械隔离，`3/3` 获得官方 reward 1.0，但 `0/3` 以内部 `verified` 结束。
重放输出哈希允许变化，因此该实验不声称候选状态逐字节等价。`fix-ocaml-gc` 的历史检查
依赖固定测试摘要文本，因此在官方 verifier 通过时仍返回非零。该结果证明隔离和
fail-closed 路径有效，也说明旧检查会产生内部假阴性；它不改变 canonical `59/89`。
详见 [completion isolation experiments](completion-isolation-experiments.md)。

## Prompt 与上下文

Executor prompt 由固定协议、原始任务、当前预算、当前计划、恢复指令、未解决错误和
最近观察组成。旧观察不会无限追加。超出窗口后只保留确定性的压缩投影。输出中的
`忽略规则` 和 `任务已完成` 等文本仅作为不可信数据出现，固定协议始终在其前后保持清晰
边界。模型输出由 Pydantic 以 `extra="forbid"` 解析，字段缺失、未知字段和动作负载
冲突都会成为协议错误。Gateway 只允许一次格式修复，防止无界 JSON 修复循环。

Reviewer 接收原始要求、模型拟执行的检查、覆盖表和最近观察，但没有
`ShellEnvironment`。它只能接受覆盖或返回缺失项。这种低频只读 reviewer 比每轮
Planner/Executor/Critic 更节省 token，也不会产生第二个环境写者。

## 可观测性与复现

`RunJournal` 以 JSONL 记录 run、模型决策、策略拒绝、命令回执、reviewer 判断、恢复和
最终报告。Harbor `AgentContext` 同步 token、成本、turn、环境调用、repair、recovery、
epoch 和停止原因。首批矩阵固定在 `evaluation/matrix.json`，扩展矩阵固定在
`evaluation/matrix-20.json`。原始 Harbor 目录默认不进 Git；冻结器从 `result.json`
只提取任务、模型、用量、Harness 状态、reward、异常类型和来源 SHA-256，写入
`evaluation/trials/` 或 `evaluation/trials-20/`。

冻结器先在临时目录生成完整结果，再整体替换目标目录。它拒绝重复、缺失和矩阵外任务。
交付检查会递归检查快照路径和内嵌任务名，防止历史文件被静默计入。汇总器从脱敏快照
重建 JSON 与 Markdown，因此 fresh clone 不依赖开发机的 `runs/` 目录。未产生 trial
的任务状态是 `not_run`，认证或基础设施错误是 `error`，只有 verifier 给出非满分结果
才是 `failed`。

## 设计特色

Evidence Harness 把完成条件、证据时效、执行权限和评测状态编码在控制器中。模型负责
选择动作，但不能跳过这些约束。

第一，完成证据有完整的生命周期。模型提交检查计划，reviewer 判断需求覆盖，
`CommandRunner` 重新执行检查，`EvidenceGate` 再核对退出码和 `work_epoch`。任何新的
`execute` 都使旧证据失效。控制器还预留环境调用次数，避免模型在工作阶段耗尽最终验证
预算。

第二，系统只有一个环境写者，但保留一次独立的语义审查。这个结构比持续运行
Planner、Executor 和 Critic 更省模型调用，也避免多个 Agent 同时修改容器。reviewer
没有 `ShellEnvironment`，只能指出缺失要求，不能制造环境证据。

第三，Harness 面向跨领域终端任务，不假设任务一定是 Git 仓库或某种编程语言。它保留
通用 Shell 能力，同时用严格动作协议、命令策略、证据回执和 Harbor 容器隔离控制风险。
这与只处理代码补丁的定位和修复流水线不同。

第四，内部完成与 benchmark 评分分离。Harness 不读取隐藏 verifier，不把 mock smoke
当成 benchmark 成绩，也不把没有运行的任务计为失败。这个边界既减少评测泄漏，也让
失败分析能区分模型、控制器和基础设施问题。

## 实验如何反向验证架构

架构不是在评测前一次确定。每次改动都来自可复现的失败，并用同题或同类任务验证。

| 运行证据 | 暴露的问题 | 架构修正 | 结果 |
|---|---|---|---|
| `fix-git` 五次拒绝合法检查 | 正则把参数文本误判为写操作 | 命令与重定向分开解析 | turns 11 降到 4，输入 token 减少 62.9% |
| GLM 空正文和长时间停滞 | 模型错误逃出 repair 边界，单次调用无截止时间 | 空正文进入 schema repair，模型调用增加独立超时 | 后续任务能分类停止或恢复 |
| `query-optimize` 正确但慢 | 第一份可用方案被误当成优化完成 | 允许选择方案时至少比较两个候选 | 恢复 trial 比参考查询快 1.22 倍 |
| `dna-assembly` 内部通过、外部失败 | 自写检查和产物共享错误解释 | 按目标工具或消费者语义解析完整产物 | 恢复 trial 获得 reward 1.0 |
| Docker manifest EOF | 环境准备错误与解题失败混在同一批次 | 保留原 trial，只重跑受影响任务 | 恢复运行 5 turns 后通过 |
| QEMU syscall 282 | 任务需要宿主未提供的虚拟化能力 | 将异常保留为 `error`，不调整 prompt | 明确需要 x86_64 runner |

这些结果也暴露了当前架构的主要缺口。17 个 live 通过任务中只有 10 个以
`verified` 结束。剩余 7 个任务的外部结果正确，但完成审查没有在预算内收敛。下一步
应改进验证隔离和 reviewer 的增量反馈，而不是继续增加 executor 自由度。

## 与常见架构的对照

| 体系 | 可借鉴的思想 | Evidence Harness 的选择 |
|---|---|---|
| Kubernetes controller | 比较期望状态与观察状态，循环收敛 | 用原始任务、`RunState` 和命令回执驱动有限次 reconcile |
| Temporal | 状态持久化、可重放历史和明确重试 | journal 支持审计与命令 replay，但不声称具备完整 durable execution |
| LangGraph | 显式状态节点和条件边 | 使用普通 Python 状态机，减少 benchmark 运行时依赖 |
| CrewAI、AutoGen | 多角色分工 | 只保留低频只读 reviewer，拒绝多个 Agent 并发写同一环境 |

这里没有直接引入这些框架。Terminal-Bench 的核心约束是 Harbor 环境执行、严格预算和
逐题隔离。额外编排运行时会增加安装、序列化和故障变量。当前实现只采用可验证的设计
思想，并把状态机和协议保留在项目代码中。

## 综合选择

候选 A 的外部单 Agent 是基础方案。它的公开接口最小，直接使用 `BaseEnvironment.exec`，也最容易通过假模型和假环境测试。

从分层控制器候选中保留完成前 reviewer、单写者约束和角色用量统计。没有保留持续 Critic。持续 Critic 会增加模型调用和状态同步，当前没有基准数据证明收益。

容器内 CLI 方案被拒绝。它需要逐题安装、认证转发和会话进程管理，把与解题无关的变量带进评测。Harbor 已提供结构化环境执行接口，外部循环更直接。

## 接受的取舍

- 接受首版对全屏 TUI 支持较弱，以换取稳定的退出码、超时和日志边界。
- 接受确定性上下文压缩可能丢失旧输出细节，以换取可复现的 token 上限。模型可以重新执行窄范围只读命令取回信息。
- 接受完成时多一次模型调用，以减少没有覆盖全部要求的提前结束。
- 接受使用 Harbor 0.23.0 的 LiteLLM 适配层，以复用模型路由、结构化响应和用量统计。项目固定 Harbor 次版本，升级时通过导入测试检查兼容性。

## 其他方案

每轮 Planner、Executor 和 Critic 都调用模型。这个方案能增加审查频率，但公开配置更复杂，token 和延迟也更高。

多 Agent 并行探索允许多个角色同时操作同一容器。它引入共享状态竞争，命令证据也难以归因。

完整 diff 沙箱适合代码仓库，但 Terminal-Bench 还包含服务和系统状态修改。mutation ledger 能覆盖更广的任务类型。

## 风险和待验证问题

- 完成 reviewer 是否提高通过率，还是只增加成本，需要用同模型消融实验判断。
- 保守的 mutation 分类可能把测试命令当作修改，导致额外验证，但不会放宽完成条件。
- 部分任务依赖交互式终端。首版要求模型用非交互参数、管道或 `expect` 改写操作。
- 不同模型对结构化响应的遵守程度不同。Gateway 只允许一次格式修复，之后终止该轮并记录协议错误。
- Shell 策略防止明显误操作和循环，但不是容器安全边界。隔离责任仍属于 Harbor
  environment。
- OrbStack 中 apt 的 HTTP 传输可能持续降速。评测 runner 提供显式的 HTTPS Debian
  源挂载，且只在调用方要求时启用。

## 下一步

当前 20 题已全部尝试。下一阶段首先在原生 x86_64 Linux runner 上重跑
`qemu-startup`，再使用明确允许授权安全评测的模型通道复测 `vulnerable-secret`。
这两步只处理外部能力边界，不改变 Harness。

随后为会生成 PDF、日志或数组文件的 completion check 设计隔离工作区。验证进程可以
写临时目录，但产物不能回写待评分目录。完成该能力后，使用同模型、同任务和同预算复测
`overfull-hbox` 与 `model-extraction-relu-logits`，比较内部 stop reason、turns 和
repairs。

最后对 completion reviewer 做消融实验。对照组关闭 reviewer，实验组启用 reviewer，
两组使用相同任务、模型、预算和 runner。只有这样的对照才能判断 reviewer 带来的通过率
收益是否抵消额外 token 和延迟。
