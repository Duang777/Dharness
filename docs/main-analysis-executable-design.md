# 主分析可执行实现设计

## 问题

阶段 2 必须在任何 PrefixBench test 或 CrossHarness outcome 出现前冻结 RQ2、RQ3 和
RQ4 的可执行语义。实现只能新增文件，不能修改 held-out protocol 绑定的 21 个源文件、
`pyproject.toml` 或 `uv.lock`。

最难的约束不是算法本身，而是可审计的隔离：

- RQ2 baseline 不能看到 state-aware 的 attempt、target line、audit 或 outcome。
- RQ3 四种 reducer 必须共享 witness predicate 和 size function。
- RQ4 adapter 和 oracle 不能依赖 Evidence Harness 的 mutation 类型或 invariant 实现。
- executable manifest 必须先于所有确认性 outcome，并绑定完整实现与上游 revision。

## 调用者视角

```bash
uv run python scripts/thesis_main_analysis.py freeze-executable \
  --mini-swe-checkout /absolute/path/to/mini-swe-agent \
  --programbench-checkout /absolute/path/to/programbench

uv run python scripts/thesis_main_analysis.py executable-preflight \
  --mini-swe-checkout /absolute/path/to/mini-swe-agent \
  --programbench-checkout /absolute/path/to/programbench

uv run python scripts/thesis_main_analysis.py build-rq2
uv run python scripts/thesis_main_analysis.py check-rq2
uv run python scripts/thesis_main_analysis.py build-rq3
uv run python scripts/thesis_main_analysis.py check-rq3
uv run python scripts/thesis_main_analysis.py transfer-collect \
  --mini-swe-checkout /absolute/path/to/mini-swe-agent \
  --programbench-checkout /absolute/path/to/programbench \
  --env-file /absolute/path/to/provider.env
uv run python scripts/thesis_main_analysis.py build-rq4
uv run python scripts/thesis_main_analysis.py check-rq4
uv run python scripts/thesis_main_analysis.py build
uv run python scripts/thesis_main_analysis.py check
```

路径参数只定位外部运行时输入。调用者不能选择 task、split、method、seed、operator、
reducer、alpha、bootstrap 次数或输出路径。

## 模块

```text
scripts/thesis_main_analysis.py
  -> main_analysis_executable.py
  -> main_analysis_report.py
       -> main_analysis_baselines.py
       -> main_analysis_reducers.py
       -> main_analysis_statistics.py
       -> main_analysis_transfer.py
       -> main_analysis_transfer_runtime.py
```

`main_analysis_transfer.py` 只直接导入标准库和 Pydantic。它与其他阶段 2 模块同位于
`evidence_harness_mutation`，避免新增文件改变 held-out producer 对
`src/evidence_harness/**/*.py` 的冻结 runtime source hash。
`main_analysis_transfer_runtime.py` 负责上游动态导入和 collection，不参与 adapter 或
oracle 判定。

## RQ2

调度器从 projected attempt 数生成一次完整 slot grid。每个 slot 包含 task identity、
attempt ordinal、operator、target invariant、slot ordinal 和 synthetic-attempt 标记。
一个 task row 按固定顺序持有五种方法的结果，因此 unequal budget 不能表示为合法报告。

四个 baseline selector 只接收各自的窄输入：

- `random_json` 接收 canonical event bytes。
- `schema_valid_random` 接收 event schema 验证后的单字段候选。
- `agentchaos_style` 接收 canonicalized agent response bytes。
- `stateless_semantic` 接收有序 event kind 和允许的固定 edit site。

这些输入不含 project root、phase、audit、attempt object、source line、state-aware target
或 outcome。baseline 全部完成后，builder 才能加载 state-aware campaign。

选择使用固定 NUL 分隔 hash material、seed `20261003`、SHA-256 前 8 bytes 和模运算。
失败后不重抽。统一 classifier 按固定顺序生成六种互斥 outcome：

```text
input_rejected
target_not_reached
oracle_invalid
oracle_equivalent
target_violation
other_oracle_change
```

## RQ3

每个 RQ2 state-aware target violation 都从原 journal 和 `MutationRequest` 重建同一个
未缩减 `AppliedMutation`。四种 reducer 各自使用新的计数器，但共享：

- source-anchored witness。
- 连续三次、canonical-byte 相等的 audit predicate。
- event、recursive payload member 和 compact canonical JSON byte size。
- source-line、object-key、list-index 的候选顺序。

`no_reduction` 只验证 witness。`flat_ddmin` 在稳定 atom list 上执行 ddmin。`hdd`
按 trace、event 和 payload 层次执行 ddmin。`source_anchored` 执行 event batch、
single-event 和 target-payload fixed point。

任何 reducer 异常或 witness 丢失都产生 `failed_retained`。结果保留原 trace，且
`after == before`，case 不从分母删除。

## 统计

统计模块只接收 typed task rows，并只用 Python 标准库。

- exact McNemar 使用 discordant pair 的二项分布双侧尾概率。
- exact Wilcoxon 删除零差，ties 使用平均秩，并用整数化秩的动态规划枚举符号分布。
- Holm 使用 exact fractions、协议顺序打破相同 p 值，并执行单调累计校正。
- bootstrap 使用 SHA-256 counter draws，不使用全局随机状态。
- 10,000 次重采样的 95% percentile interval 固定取 one-based rank 250 和 9,750。
- RQ3 每次重采样取完整 task cluster，再计算 pooled retained-byte fraction。

报告保存精确分子、分母和稳定十进制文本，不能用二进制浮点作为统计输入。

## RQ4

冻结两套独立上游身份：

```text
mini-swe-agent
commit: 04d809ceab9df28f9adaed044884180159172930
tree:   05aef37afee93a52cea7d571b00d435f28c81bd6

ProgramBench v1.2.4
commit: 963063c9271cc40fa179977356782ea4582e0b0c
tree:   0662e455d08e8c6e8d326a615d0cd4c6e448cf72
```

ProgramBench catalog 排除 `testorg__*` fixture 后包含 200 个 instance。catalog bytes
是按 UTF-8 byte order 排序、每行一个 ID、含最后 LF 的 UTF-8 文本：

```text
bytes: 5021
sha256: 1727a2e958a5ab9fe11c0218c79225e9ea673f0de02618a155c3c600bb9de938
```

cohort 以 `SHA256("miniswe-transfer-v1\0" + instance_id)` 和 instance ID 排序，固定前
20 个 ID 及各自 digest。选择器只读取 task directory identity 和 `task.yaml` 是否存在。

标准 mini-swe trajectory 没有 workspace tree digest。独立 runtime 在初始状态、每个
action 后和 terminal 时执行只读 workspace digest，并把 attestation 写入独立字段。
adapter 把 trajectory 与 attestation 转成：

```text
Decision -> Action -> Observation -> CandidateState -> Terminal
```

缺失、重复或错序 attestation 必须拒绝，不能根据 shell 文本猜测 workspace 是否变化。
I1、I2 和 I4 使用独立 transfer oracle；I3 固定为 `unsupported_by_design`。

## Executable manifest

`freeze-executable` 和 `executable-preflight` 在读取协议、源文件、catalog 或 checkout
前，依次执行：

1. 对所有确认性 outcome 路径执行 filesystem absence 检查。
2. 对可提交 outcome 路径执行 `git log --all --format=%H -- <path>`。

manifest 使用显式路径列表和 `sha256-length-framed-path-content-v1`，绑定：

- 主协议 bytes 和首次提交。
- 21 个 protected source 的冻结摘要。
- 全部阶段 2 实现、测试、CLI 和 cohort。
- mini-swe-agent 与 ProgramBench 的 commit、tree 和选定 source blobs。
- catalog 规则、bytes、SHA-256 和固定 cohort。
- hash draw、bootstrap 和 percentile 实现规则。
- 所有 outcome 路径。

manifest 不递归绑定自身。Git 中首次出现相同 manifest bytes 的提交记为 `E`。loader
验证 `P` 是 `E` 的祖先；后续每个确认性 producer 必须验证 `E` 是其祖先。

## 设计合成

候选 A 是基础，因为它覆盖了 freeze、preflight、collection、RQ2、RQ3、RQ4 和主报告的
完整调用链，并为 RQ3 保留可重验的 reduced trace。

从候选 B 吸收：

- baseline 的隔离执行边界和无 project-root 请求。
- 六分支 discriminated outcome union。
- slot 的 target invariant 与 synthetic-attempt 字段。
- immutable reduction input 和每个 reducer 独立计数器。
- 排除 ProgramBench fixture 后的 200-ID catalog。

拒绝以下设计：

- 通用实验插件或 DSL。
- 单文件实现。
- wheel-only ProgramBench 身份。
- 复用现有 reducer 的私有 witness 作为唯一 source-anchored 路径。
- 只保存 reduced digest、不保留可重验 reduced trace。
- 根据 action command 文本推断 workspace change。

## 接受的取舍

- 接受多个按知识归属拆分的模块，以换取可测试的信息隔离和短调用链。
- 接受每个 reducer 重复三次 audit，以换取相同 witness 成本和确定性证据。
- 接受 workspace digest 的额外只读调用，以换取可观察的 freshness 和 candidate binding。
- 接受标准库精确统计的实现成本，以避免修改冻结依赖文件。

## 实现顺序

先实现纯统计和 RQ2 窄输入边界，再实现统一 reducer kernel、transfer adapter/runtime，
最后生成 cohort 与 executable manifest。manifest 必须在所有新增源文件和测试稳定后冻结。
