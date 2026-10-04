# 论文主实验执行计划

## 目标

本计划把论文主实验分成可独立验证和提交的阶段。最终产物回答四个研究问题：

1. Historical-7 是否能复现修复前缺陷并确认修复后行为。
2. state-aware mutation 是否优于四类等预算基线。
3. source-anchored reducer 是否比 no reduction、flat ddmin 和 HDD 产生更小的反例。
4. 四类控制语义能否迁移到 mini-swe-agent。

`docs/thesis-main-experiment-spec.md` 定义固定研究合同。本文件只说明执行顺序。

## 当前基线

计划从提交 `f5c463b399201911ce9042db559f90f8a6d91337` 开始。该提交已经冻结：

- PrefixBench 的 28 题 development split 和 61 题 test split。
- 61 题 held-out collection 参数和描述性 offline campaign。
- 现有 operator、oracle、reducer 和 source manifest。
- Historical-7 与 development 分析产物。

截至 2026-10-03，以下 held-out 路径不存在：

```text
runs/terminal-bench-2/prefixbench-v1-test-20261002
evaluation/prefixbench-v1-test-canonical.json
evaluation/prefixbench-v1-test-readiness.json
evaluation/prefixbench-v1-test-offline-campaign.json
```

在首次 held-out collection 前，必须完成阶段 0、阶段 1 和阶段 2。

## 全程规则

每个阶段都遵守以下规则：

- 不修改 `experiments/prefixbench-v1/test-mutation-protocol-v1.json` 绑定的 21 个文件。
- 不修改 `pyproject.toml`、`uv.lock` 或 `src/evidence_harness/**/*.py`。
- 不按 reward、status、exception、offline outcome 或初步统计重跑任务。
- 不把 development task 或 development 统计并入确认性分析。
- 不手工抄写统计表。脚本从 canonical JSON 生成论文表格。
- 不把 `offline_violation` 改称 production mutation kill。
- 每完成一个可独立验证的阶段，创建一个提交。
- 协议和结果提交分开。结果提交不能同时修改协议。
- 在原始输入齐全时重建产物并比较 canonical bytes。
- 在原始输入缺失时，只允许执行明确支持的 artifact-only check。

若 provider 凭据尚未准备好，继续完成不依赖凭据的实现和 synthetic tests。不要在日志、提交、
协议或对话中记录凭据内容或凭据文件摘要。

## 阶段状态

| 阶段 | 状态 | 完成标准 |
|---|---|---|
| 0. 冻结执行计划与主实验规范 | 进行中 | 两份文档通过检查并提交 |
| 1. 冻结机器可读主协议 | 待开始 | protocol、loader、preflight、checker 和测试提交 |
| 2. 冻结确认性执行实现 | 待开始 | 基线、reducer 对照、统计和 transfer adapter 在揭盲前提交 |
| 3. 采集 61 题 held-out cohort | 待开始 | 61 个任务各有一个完整结果 |
| 4. 构建 held-out source artifacts | 待开始 | canonical、readiness 和 descriptive campaign 通过检查 |
| 5. 运行 RQ2 等预算比较 | 待开始 | 五种方法的完整 task-level 结果通过检查 |
| 6. 运行 RQ3 reducer 对照 | 待开始 | 四种 reducer 的完整结果通过检查 |
| 7. 运行 RQ4 跨 Harness 实验 | 待开始 | 固定 20 题 cohort 和四族结果通过检查 |
| 8. 生成确认性统计和主报告 | 待开始 | 四个 RQ 的 canonical 报告可重建 |
| 9. 运行 TB2.1 敏感性实验 | 可选 | 独立协议和独立报告通过检查 |
| 10. 生成论文表格和复现包 | 待开始 | 所有表格来自 canonical JSON |

## 阶段 0：冻结执行计划与主实验规范

### 输入

- `docs/agent-harness-research-roadmap.md`
- 已冻结的 Historical-7、PrefixBench development 和 held-out 协议
- 三个独立主分析设计候选及交叉评审结论

### 输出

```text
docs/thesis-execution-plan.md
docs/thesis-main-experiment-spec.md
```

### 验证

```bash
git diff --check
uv run python scripts/prefixbench_test_campaign.py preflight
git status --short
```

### 验收

- 两份文档使用相同的四个 RQ。
- 规范固定所有主要分母、比较方法、排除规则和统计方法。
- 文档明确 development、TB2.1、production mutation score 和 live cost 的边界。
- held-out preflight 仍通过。
- 现有冻结文件没有变化。

### 停止条件

若文档要求修改 21 个已绑定文件，则停止本阶段并改为新增 sibling module。

### 提交

```bash
git add docs/thesis-execution-plan.md docs/thesis-main-experiment-spec.md
git commit -m "Specify thesis main experiment"
```

## 阶段 1：冻结机器可读主协议

### 输入

- 阶段 0 的两份文档
- 已知输入及其固定 SHA-256
- `f5c463b399201911ce9042db559f90f8a6d91337` 的 21 文件 source set

### 输出

```text
src/evidence_harness_mutation/main_analysis_protocol.py
scripts/main_analysis_protocol.py
tests/mutation/test_main_analysis_protocol.py
tests/test_main_analysis_protocol_script.py
experiments/prefixbench-v1/main-analysis-protocol-v1.json
```

### 实现

公开 Python 操作固定为：

```python
freeze_main_analysis_protocol(project_root)
load_main_analysis_protocol(project_root)
preflight_main_analysis_protocol(project_root)
check_main_analysis_protocol(project_root)
```

CLI 固定为：

```bash
uv run python scripts/main_analysis_protocol.py freeze
uv run python scripts/main_analysis_protocol.py preflight
uv run python scripts/main_analysis_protocol.py check
```

`freeze` 按以下顺序执行：

1. 检查所有未来 outcome 路径在工作区中不存在。
2. 使用 `git log --all -- <path>` 检查这些路径未在任何可达 ref 中出现。
3. 读取已知输入并核对固定 SHA-256。
4. 核对 21 个受保护文件与 `f5c463b` 的 bytes 相同。
5. 生成 canonical protocol bytes。
6. 若目标不存在，则原子写入。若目标已存在且 bytes 相同，则不修改。否则失败。

`preflight` 只读。它必须证明：

- 协议已提交。
- 当前 `HEAD` 包含同一协议 bytes。
- 协议首次出现的提交是当前 `HEAD` 的祖先。
- 所有未来 outcome 路径仍不存在，且没有 all-refs history。
- 受保护文件仍与 `f5c463b` 相同。
- 命令没有打开 held-out journal、readiness、campaign 或结果文件。

### 测试

至少覆盖：

- canonical bytes 稳定。
- 已知输入 hash 不符时失败。
- outcome 路径存在时，在读取任何协议输入前失败。
- outcome 路径已删除但仍出现在其他 ref 时失败。
- reader spy 证明 non-read gate 的执行顺序。
- 协议未提交、工作树中的协议 bytes 不同或 ancestor 关系错误时失败。
- 21 个受保护文件中任一 bytes 改变时失败。
- CLI 没有 split、task、method、seed、reducer、统计或输出路径选项。

### 验证

```bash
uv run pytest \
  tests/mutation/test_main_analysis_protocol.py \
  tests/test_main_analysis_protocol_script.py
uv run ruff check \
  src/evidence_harness_mutation/main_analysis_protocol.py \
  scripts/main_analysis_protocol.py \
  tests/mutation/test_main_analysis_protocol.py \
  tests/test_main_analysis_protocol_script.py
uv run ruff format --check \
  src/evidence_harness_mutation/main_analysis_protocol.py \
  scripts/main_analysis_protocol.py \
  tests/mutation/test_main_analysis_protocol.py \
  tests/test_main_analysis_protocol_script.py
uv run mypy \
  src/evidence_harness_mutation/main_analysis_protocol.py \
  scripts/main_analysis_protocol.py
uv run python scripts/main_analysis_protocol.py check
uv run python scripts/prefixbench_test_campaign.py preflight
git diff --check
```

### 验收

- protocol ID 为 `thesis-main-analysis-v1`。
- 协议只绑定已知输入，不假造未来 producer hash。
- 协议固定 RQ2、RQ3、RQ4 的选择规则和结论门槛。
- 协议声明后续 executable source set 的冻结要求。
- 协议不包含凭据路径、凭据值或凭据摘要。

### 停止条件

- 任一未来 outcome 已存在或曾出现在可达 Git ref。
- `f5c463b` 的受保护 source set 已变化。
- 协议需要调用者传入科学决策参数。

### 提交

```bash
git add \
  src/evidence_harness_mutation/main_analysis_protocol.py \
  scripts/main_analysis_protocol.py \
  tests/mutation/test_main_analysis_protocol.py \
  tests/test_main_analysis_protocol_script.py \
  experiments/prefixbench-v1/main-analysis-protocol-v1.json
git commit -m "Preregister thesis main analysis"
```

## 阶段 2：冻结确认性执行实现

### 输入

- 已提交的 `main-analysis-protocol-v1.json`
- synthetic fixtures
- mini-swe-agent commit `04d809ceab9df28f9adaed044884180159172930`

### 输出

新增 sibling modules，建议职责如下：

```text
src/evidence_harness_mutation/main_analysis_baselines.py
src/evidence_harness_mutation/main_analysis_reducers.py
src/evidence_harness_mutation/main_analysis_transfer.py
src/evidence_harness_mutation/main_analysis_report.py
scripts/thesis_main_analysis.py
tests/mutation/test_main_analysis_baselines.py
tests/mutation/test_main_analysis_reducers.py
tests/mutation/test_main_analysis_transfer.py
tests/mutation/test_main_analysis_report.py
tests/test_thesis_main_analysis_script.py
experiments/prefixbench-v1/main-analysis-executable-v1.json
experiments/prefixbench-v1/miniswe-cohort-v1.json
```

### 实现顺序

1. 实现 RQ2 的四类基线和统一 outcome partition。
2. 用 synthetic traces 验证五种方法执行相同 slot 数。
3. 实现 no reduction、flat ddmin 和 HDD。
4. 让四种 reducer 共用一个 witness predicate 和 size function。
5. 实现 exact McNemar、exact Wilcoxon、Holm 和 task-cluster bootstrap。
6. 实现 mini-swe-agent `mini-swe-agent-1.1` trajectory adapter。
7. 冻结 20 题 outcome-blind cohort。
8. 生成 executable source-set manifest。

该阶段不能读取阶段 3 或阶段 4 的任何 outcome。测试必须使用人工 fixture 或 development
fixture。development 数据不能用于修改主 endpoint、门槛或统计方法。

### 验收

- 新代码只位于新增文件。
- 五种 RQ2 方法对每个 task 使用同一 slot grid。
- baseline 不能读取 state-aware 选择出的 source line、attempt、phase 或 audit。
- 四种 reducer 从同一未缩减输入开始。
- reducer 失败不会删除 case。
- mini-swe adapter 不导入 `evidence_harness_mutation.invariants`。
- executable protocol 绑定全部新增实现、测试、cohort 和上游 source。
- executable protocol 的提交早于任何 held-out 或 CrossHarness outcome producer。

### 停止条件

若 held-out 或 CrossHarness outcome 已存在，则不再修改确认性实现。任何必要修改必须新建
协议版本，并把旧版本结果保留为 deviation evidence。

### 提交

使用一个独立提交冻结实现和 executable manifest。不要在该提交中生成真实结果。

## 阶段 3：采集 61 题 held-out cohort

### 前置条件

```bash
uv run python scripts/main_analysis_protocol.py preflight
uv run python scripts/prefixbench_test_campaign.py preflight
```

两个命令都必须通过。provider env 文件必须使用绝对路径。

### 执行

```bash
uv run python scripts/run_full_evaluation.py \
  --model provider/model \
  --env-file "$PROVIDER_ENV_FILE" \
  --matrix evaluation/matrix-prefixbench-test.json \
  --run-name prefixbench-v1-test-20261002 \
  --collection-profile prefixbench-v1
```

同一命令可恢复中断。不要为已经完成的任务创建第二个结果。

### 验收

- `progress.jsonl` 按 matrix 顺序包含 61 个唯一 completion。
- 每个 task 只有一个 completed result。
- 所有 task 共用一个 producer commit、tree 和 runtime source hash。
- result policy 接受每个完整结果，不按 outcome 筛选。

### 停止条件

- run config 与冻结协议不符。
- producer commit 不包含主协议和 held-out 协议的 exact bytes。
- 同一 task 出现第二个 completion。
- 发现 outcome-driven retry。

### 提交

原始 run root 是否进入发布仓库由复现包策略决定。阶段 4 的 canonical source artifacts 必须
提交。

## 阶段 4：构建 held-out source artifacts

### 执行

```bash
uv run python scripts/collect_evaluation_results.py \
  runs/terminal-bench-2/prefixbench-v1-test-20261002 \
  --matrix evaluation/matrix-prefixbench-test.json \
  --collection-profile prefixbench-v1 \
  --manifest-out evaluation/prefixbench-v1-test-canonical.json

uv run python scripts/prefixbench.py build \
  --canonical evaluation/prefixbench-v1-test-canonical.json \
  --matrix evaluation/matrix-prefixbench-test.json \
  --expected-task-count 61 \
  --out evaluation/prefixbench-v1-test-readiness.json

uv run python scripts/prefixbench_test_campaign.py build
uv run python scripts/prefixbench_test_campaign.py check
```

### 输出

```text
evaluation/prefixbench-v1-test-canonical.json
evaluation/prefixbench-v1-test-readiness.json
evaluation/prefixbench-v1-test-offline-campaign.json
```

### 验收

- readiness 接纳 61 个 test task，不含 development identity。
- campaign 保留所有 scheduled cases 和全部 outcome 分类。
- campaign 仍只作 held-out descriptive evidence。
- 重复 build 产生相同 bytes。

### 提交

只提交 source artifacts。不要在同一提交中修改分析协议或执行实现。

## 阶段 5：运行 RQ2 等预算比较

### 执行

计划命令：

```bash
uv run python scripts/thesis_main_analysis.py build-rq2
uv run python scripts/thesis_main_analysis.py check-rq2
```

### 验收

- 61 个 task 全部存在。
- 每个 task 的五种方法具有相同 slot key 和 slot 数。
- 每个 slot 恰好有一个互斥 outcome。
- 所有 parse rejection、not reached、invalid 和 equivalent case 均保留。
- 主 endpoint 先聚合到 task，再执行四个配对比较。
- 输出包含原始 p 值、Holm 校正值、绝对风险差和 95% task bootstrap 区间。

### 停止条件

61 题输入不完整时，RQ2 状态只能是 `incomplete`。不得生成部分确认性结论。

## 阶段 6：运行 RQ3 reducer 对照

### 执行

计划命令：

```bash
uv run python scripts/thesis_main_analysis.py build-rq3
uv run python scripts/thesis_main_analysis.py check-rq3
```

### 验收

- 输入是 RQ2 state-aware 的全部 target violations。
- 每个 case 对四种 reducer 使用同一原始输入和 witness predicate。
- 每个候选连续审计三次。
- 失败的 reducer 以未缩减大小进入主比较，并记录失败原因。
- 主比较只使用 retained canonical bytes。
- event count、payload member count、candidate evaluations 和 audit calls 是次要指标。
- 少于 10 个 case 或少于 5 个 task 时，状态为 `insufficient_cases`，不输出 p 值。

## 阶段 7：运行 RQ4 跨 Harness 实验

### 前置条件

- mini-swe-agent checkout 位于固定 commit。
- 20 题 cohort manifest 已提交。
- transfer outcome 路径不存在，且没有 all-refs history。

### 执行

计划命令：

```bash
uv run python scripts/thesis_main_analysis.py transfer-preflight \
  --mini-swe-checkout "$MINISWE_CHECKOUT" \
  --programbench-checkout "$PROGRAMBENCH_CHECKOUT"

uv run python scripts/thesis_main_analysis.py transfer-collect \
  --mini-swe-checkout "$MINISWE_CHECKOUT" \
  --programbench-checkout "$PROGRAMBENCH_CHECKOUT" \
  --env-file "$PROVIDER_ENV_FILE"

uv run python scripts/thesis_main_analysis.py build-rq4
uv run python scripts/thesis_main_analysis.py check-rq4
```

### 验收

- 报告保留四个原语义族作为固定分母。
- reviewer acceptance 族记为 `unsupported_by_design`。
- 三个 mapped 族分别报告 mapped、executable、applicable 和 deterministic violation。
- adapter 失败、source preexisting violation 和 not applicable 均保留。
- 迁移结果不与 PrefixBench 数值合并。
- 结果只支持 trajectory schema 和控制语义迁移，不支持任务成功率结论。

## 阶段 8：生成确认性统计和主报告

### 执行

计划命令：

```bash
uv run python scripts/thesis_main_analysis.py build
uv run python scripts/thesis_main_analysis.py check
```

### 输出

```text
evaluation/thesis-main-analysis-v1.json
```

### 验收

- 主报告按 RQ1、RQ2、RQ3、RQ4 固定顺序输出。
- RQ1 明确标记为 locked retrospective evidence。
- RQ2 和 RQ3 只含 61 题 test split。
- RQ4 只含固定 CrossHarness cohort。
- development 仍为 `descriptive_not_evaluated`。
- TB2.1、production mutation score 和 live cost 没有独立协议时为 `not_evaluated`。
- protocol commit 是每个 outcome producer commit 的祖先。
- raw inputs 齐全时，`check` 重建并逐字节比较。
- raw inputs 全部缺失时，`check` 只执行协议允许的 artifact-only validation。
- 任一输入组部分存在时，`check` 失败。

### 提交

结果、生成脚本和论文表格分别提交。协议文件不得在结果提交中变化。

## 阶段 9：运行 Terminal-Bench 2.1 敏感性实验

该阶段可选，不阻塞四个 RQ。

运行前必须新增并提交独立协议。协议固定 TB2.1 revision、exact-overlap 规则、matrix、producer、
source set 和输出路径。结果不能：

- 替换 TB2.0 主结果。
- 并入 RQ2 的 p 值或 Holm family。
- 与 task identity 不同的同名任务合并。
- 修改四个主 RQ 的 disposition。

## 阶段 10：生成论文表格和复现包

### 输出

- 四个 RQ 的正文表格。
- 完整 outcome partition 附录。
- 协议偏差表。
- source binding 和 Git chronology 表。
- 一键 artifact checker。
- 原始数据发布清单和不能发布的数据说明。

### 验收

- 每个表格单元格可追溯到 canonical JSON path。
- 文本中没有手工维护的重复统计值。
- 复现说明区分 full rebuild 和 artifact-only check。
- 论文主张不超过 `docs/thesis-main-experiment-spec.md` 的 claim boundary。

## 偏差和版本规则

在 outcome 产生前发现协议错误时：

1. 保留 v1。
2. 新增 v2。
3. 记录修改原因和 v1 到 v2 的字段差异。
4. 重新执行 preflight。
5. 只有最新的未揭盲版本可以作为确认性协议。

在 outcome 产生后发现协议错误时：

1. 不覆盖原协议或原结果。
2. 新增 deviation artifact。
3. 说明错误何时发现、影响哪些 RQ 和哪些行。
4. 将受影响结论降为 exploratory 或 inconclusive。
5. 只有不依赖已观察 outcome 的机械修复可以保留确认性标签。

Git chronology 只能证明仓库内可验证的先后关系。它不能证明研究者在仓库外从未查看结果。
