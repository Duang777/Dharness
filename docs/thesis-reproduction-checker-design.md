# 论文复现包检查器设计

## 问题

阶段 10 需要一个只读入口，统一判断当前仓库仍处于实验前状态，还是已经形成可验证的完整
复现包。现有 checker 分别验证单个阶段，但没有统一状态机。RQ2、RQ3 和 RQ4 的 checker
也只检查单个 raw anchor，不能证明协议规定的 raw input group 是全有或全无。

TB2.1 sensitivity 后续增加了独立协议和 executable。统一入口还需要验证这条冻结链，并在
不改变主复现包状态定义的前提下报告其 outcome 尚未开始、部分存在或完整。

检查器不得运行 Provider、Docker、模型或采集命令，不得写入或修复产物，也不得修改主协议、
executable manifest 绑定的源码。

## 调用方式

命令行只有一个固定入口：

```bash
uv run python scripts/check_thesis_reproduction.py
```

它不接受项目路径、模式、输出路径或科学参数。脚本从自身位置确定项目根目录，并向 stdout
写一个 canonical JSON 结果。

当前输出 schema 为 v2。v2 增加 `tb21_sensitivity`，保留原有 `state`、主协议提交、
主 executable 提交和四组 raw input 的含义。

Python 调用者只需一个操作：

```python
from evidence_harness_mutation.thesis_reproduction import (
    ReproductionState,
    check_thesis_reproduction,
)

result = check_thesis_reproduction(project_root)
if result.state is ReproductionState.PARTIAL_INVALID:
    raise RuntimeError("\n".join(result.errors))
```

CLI 对 `pre_collection` 和 `complete` 返回 0，对 `partial_invalid` 返回 1。非法参数由
`argparse` 返回 2。

## 状态

检查结果只有三种状态：

- `pre_collection`：冻结 gate 和采集前 preflight 均通过，所有下游 artifact 与 raw root
  都不存在，也没有相关 Git history。
- `partial_invalid`：冻结 gate 失败、artifact 集不完整、raw input group 部分存在，或任一
  内容检查失败。
- `complete`：九个必需 artifact 全部存在且有效，四个 raw input group 均可明确归类为
  `artifact_only` 或 `full_rebuild`。

九个必需 artifact 是：

```text
evaluation/prefixbench-v1-test-canonical.json
evaluation/prefixbench-v1-test-readiness.json
evaluation/prefixbench-v1-test-offline-campaign.json
evaluation/prefixbench-v1-test-method-comparison.json
evaluation/prefixbench-v1-test-reducer-comparison.json
evaluation/miniswe-agent-transfer-v1.json
evaluation/thesis-main-analysis-v1.json
evaluation/thesis-tables-v1.json
docs/thesis-tables-v1.md
```

Terminal-Bench 2.1 与 auxiliary 报告仍是可选证据，不属于 v1 完整包的必要集合。
TB2.1 的完成状态单独出现在 `tb21_sensitivity`，不会把主复现包从 `pre_collection`
改成 `complete`。

## 冻结 gate

`check_thesis_reproduction()` 首先调用现有的 `load_main_analysis_protocol()` 和
`load_main_analysis_executable()`。两者验证 canonical bytes、固定 source binding、当前
Git 内容以及 `protocol commit -> executable commit -> HEAD` 的祖先关系。

随后检查器调用 `tb21_sensitivity.load()`。该 loader 验证 TB2.1 matrix、协议、
executable、绑定源码以及它自己的 `protocol commit -> executable commit -> HEAD`
祖先关系。

这些 loader 会读取已冻结的 Historical-7、development 和 TB2.1 设计输入，但不会读取
held-out、RQ、CrossHarness 或 TB2.1 outcome。全部 loader 成功后，检查器才探测下游路径。
私有 `_FrozenContext` 是后续 outcome helper 的必需参数，使调用顺序在函数签名中可见。

若 gate 失败，检查器立即返回，不探测或读取下游路径。测试通过替换 topology reader 证明
该顺序。

## TB2.1 sensitivity 状态

`tb21_sensitivity` 固定包含 protocol commit、executable commit、六个预期 outcome entry
的数量和当前状态：

- `not_started`：六个 outcome entry 全部缺失。
- `partial`：存在一至五个 entry。
- `complete`：六个 entry 全部存在。

六个 entry 是 raw root、canonical、readiness、campaign、method comparison 和 final
report。它们必须按 executable 声明的依赖顺序形成前缀。raw root 必须是普通目录，其余
entry 必须是普通文件；symlink、类型错误或跳过依赖都会使顶层状态变成
`partial_invalid`。

`not_started` 和 `partial` 只检查路径拓扑，不读取 outcome 内容。`complete` 复用
`tb21_sensitivity.check()`，逐字节重建并检查五个派生产物，同时验证 outcome 提交位于
executable commit 之后。该路径不会运行 Harbor preflight、Provider、Docker 或采集命令。

## 四组 raw input

完整 artifact 闭包通过 schema 和基础结构检查后，检查器从已绑定的 canonical rows 推导
raw path，不扫描目录猜测输入。

| 组 | 文件 |
|---|---:|
| `held_out_collection` | 61 个 result、61 个 config、61 个 journal、run-config、progress，共 185 个 |
| `rq2_method_comparison` | campaign 绑定的 61 个 journal |
| `rq3_reducer_comparison` | 同一组 61 个 journal |
| `cross_harness_collection` | 固定 cohort 对应的 20 个 trajectory |

每组独立应用以下规则：

- raw root 和所有成员均缺失：`artifact_only`。
- raw root 是普通目录，所有成员均为普通文件：`full_rebuild`。
- 其他情况：该组无 mode，整个包为 `partial_invalid`。

symlink、目录代替文件、越出固定 raw root 的绑定，以及重复或数量错误的 path 都属于
invalid。RQ2 和 RQ3 虽然共享 journal，仍分别声明 mode，因为主协议把它们定义为两个重建
问题。

## 完整包检查

artifact-only 和 full-rebuild 都执行以下检查：

1. `check_prefixbench_test_campaign()` 验证 held-out protocol、canonical、readiness、
   campaign、producer 和 task binding。
2. 检查 RQ2 task 与 campaign 的 task 顺序和 identity 完全一致。
3. 检查 RQ3 case 正好来自 RQ2 state-aware target violations。
4. 检查 RQ4 task 与固定 cohort 一致，20 个 trajectory binding 均存在于报告中，且
   `collection_complete=true`。
5. 在内存中重建 main report，并逐字节比较。
6. `check_thesis_tables()` 重建 table bundle 和 Markdown，并验证全部 source pointer。

当某组是 `full_rebuild` 时，检查器增加对应重建：

- held-out collection 从 run root 重建 canonical manifest，并由现有 campaign checker
  重建 readiness 和 campaign。
- RQ2、RQ3、RQ4 分别调用现有纯 builder，在内存中逐字节比较报告。

当某组是 `artifact_only` 时，检查器不调用它的 raw builder。table checker 仍会重新计算
RQ2、RQ3、RQ4 的嵌套 analysis，并验证跨 artifact binding 和 Git lineage。输出中的 mode
明确说明该组没有重跑模型、环境、campaign 或 reducer。

## 模块边界

新增文件：

```text
src/evidence_harness_mutation/thesis_reproduction.py
scripts/check_thesis_reproduction.py
tests/mutation/test_thesis_reproduction.py
tests/test_check_thesis_reproduction_script.py
docs/thesis-reproduction-checker-design.md
```

库模块拥有状态机、raw path 规则和验证顺序。CLI 只拥有固定根目录、JSON 输出和退出码。
检查器没有 writer、repair 或 mode 参数。

## 设计合成

三个候选都同意使用单一 orchestrator、三态状态机和四组 raw 声明。最终设计以直接复用现有
冻结 loader 的候选为基础，因为当前禁止提前读取的是 held-out 和 CrossHarness outcome，
而 loader 只读取协议已冻结的历史与 development 输入。

从另两个候选吸收：

- held-out canonical 必须从 raw result 重新构建，不能只检查 readiness 和 campaign。
- artifact-only RQ4 必须要求 20 个 trajectory binding 和 `collection_complete=true`。
- gate 失败后的 path-access spy、partial raw fail-fast，以及 machine-readable canonical
  JSON 输出。

没有采用自建 freeze validator。它会复制固定 hash、source-set 和 Git chronology 逻辑，
形成第二套协议实现。也没有要求最终 outcome 在运行 checker 前已经提交，因为项目流程需要
先验证产物，再创建结果提交。

## 取舍

- 接受一个较大的领域模块，以换取单一公共操作和固定调用顺序。
- 接受 RQ2、RQ3 对同一组 journal 的重复声明，以准确表达两个研究问题的重建能力。
- 接受 Git 作为运行前提，因为主协议和 executable 本身依赖提交内容与祖先关系。
- artifact-only 只能证明 canonical graph 自洽，不能重新证明已省略的 raw payload；结果
  用 mode 明确记录这一限制。
- 接受 TB2.1 partial 状态只证明依赖拓扑。内容校验需要 61 个 terminal task 齐全，因此只在
  complete 状态调用现有 checker。
