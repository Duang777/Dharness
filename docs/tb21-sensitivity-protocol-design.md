# Terminal-Bench 2.1 敏感性协议设计

## 文档类型

本文解释 `thesis-tb21-sensitivity-v1` 的边界和冻结方式。执行命令与验收顺序以机器可读协议为准。

## 目标

主实验使用 Terminal-Bench 2.0。Terminal-Bench 2.1 修改了同一批 89 个任务中的部分任务，
因此论文需要独立回答一个敏感性问题：

> 在 Terminal-Bench 2.1 的同名 61 题上，RQ2 的五种方法比较是否得到一致方向的结果？

该分析不属于 RQ1 至 RQ4。它不替换 Terminal-Bench 2.0 主结果，不加入主 RQ2 的 Holm
校正族，也不修改任何主 RQ disposition。

本阶段只冻结协议和 cohort。它不运行 Harbor，不读取 Provider 配置，也不生成实验结果。
后续必须先提交独立 executable manifest，才能开始 Provider 执行。

## 固定上游

Terminal-Bench 2.0 使用现有项目已经绑定的源版本：

```text
repository: https://github.com/laude-institute/terminal-bench-2.git
commit: 69671fbaac6d67a7ef0dfec016cc38a64ef7a77c
root tree: 03e5d294753cbd4a6b9b25b6ae8479a8692a1675
tasks: 89
```

Terminal-Bench 2.1 使用完整且尚未包含 leaderboard 文件的任务快照：

```text
repository: https://github.com/harbor-framework/terminal-bench-2-1.git
commit: 5fc7d3b91d27ef0304b4eabe5002e6578160b817
root tree: 45e2c88a5f4a8a4864ee58284b4dc17ef97ee73c
tasks tree: 46241f6de73251d4d9e7a284876e188c026a5a7d
manifest: tasks/dataset.toml
manifest blob: 6e7e030fd37a7cefdbd597badcf8560c8748d995
manifest SHA-256: d90b4389992d07ed6f4ab8de963a70241eaa4b60072eeaec4c3b261b6c4a6dd8
tasks: 89
```

后续提交只修改了 `tasks/README.md`，但协议仍固定上述 commit。执行不能改用 `main` 或
`latest`。

## 两个版本如何对应

`evaluation/matrix-prefixbench-test.json` 是 cohort 的唯一选择输入。生成器读取其中 61 个
名称，并保留原顺序。

每个名称必须同时满足以下条件：

1. 名称是非空 UTF-8，且不含 `/` 或 NUL。
2. Terminal-Bench 2.0 的根目录包含同名任务。
3. Terminal-Bench 2.1 的 `tasks` 目录包含同名任务。
4. Terminal-Bench 2.1 manifest 包含 `terminal-bench/<name>`。

比较按 UTF-8 bytes 精确执行。协议不做大小写折叠、Unicode 归一化、别名映射、模糊匹配或
重排序。

名称只用于建立对应关系。它不是任务身份。每一行分别记录：

- Terminal-Bench 2.0 的 repository、commit、task path 和 Git tree。
- Terminal-Bench 2.1 的 repository、commit、task path、Git tree、registry name 和
  package digest。
- 两个不同 hash domain 产生的 source identity。
- 由名称和两个 source identity 产生的 correspondence identity。

因此，同名任务仍是两个版本化任务。后续代码不能把其中一个版本的 source identity 传给另一个
版本的 runner，也不能用一个版本补齐另一个版本的缺失结果。

## 产物

冻结操作生成两个 canonical JSON 文件：

```text
evaluation/matrix-prefixbench-tb21-sensitivity.json
experiments/prefixbench-v1/tb21-sensitivity-protocol-v1.json
```

matrix 保存 61 行双版本 source identity。protocol 绑定 matrix bytes、现有主协议、held-out
协议、main executable、21 个 protected source 和本阶段实现文件。

JSON 编码固定为：

```text
ensure_ascii: true
indent: 2
sort_keys: true
one trailing newline
```

## 使用方式

冻结时提供两个本地 Git checkout。路径只用于读取固定 Git objects，不写入产物：

```bash
uv run python scripts/tb21_sensitivity_protocol.py freeze \
  --tb20-checkout "$TB20_CHECKOUT" \
  --tb21-checkout "$TB21_CHECKOUT"
```

提交后执行纯仓库检查：

```bash
uv run python scripts/tb21_sensitivity_protocol.py check
```

在设计 executable manifest 前重新验证外部 source：

```bash
uv run python scripts/tb21_sensitivity_protocol.py preflight \
  --tb20-checkout "$TB20_CHECKOUT" \
  --tb21-checkout "$TB21_CHECKOUT"
```

`preflight` 只声明 `ready_for_executable_freeze=true`。它必须同时声明
`ready_for_provider_execution=false`。

Python API 只有：

```python
freeze_tb21_sensitivity_protocol(project_root, repositories)
load_tb21_sensitivity_protocol(project_root)
preflight_tb21_sensitivity_protocol(project_root, repositories)
check_tb21_sensitivity_protocol(project_root)
```

调用者不能传入 task、method、operator、seed、alpha、bootstrap count、revision 或输出路径。

## 固定 Harbor selector

后续 executable 必须从协议构造以下前缀：

```text
harbor run
--repo https://github.com/harbor-framework/terminal-bench-2-1.git@5fc7d3b91d27ef0304b4eabe5002e6578160b817
--registry-path tasks/dataset.toml
--dataset terminal-bench-2-1
```

每个 `--include-task-name` 必须来自 matrix 中的 `tb21.registry_name`，并且每行只执行一次。
后续 preflight 必须在不调用 Provider 的条件下证明该 selector 能解析固定 manifest 和 61 个
package digest。若当前 Harbor 版本不能满足该条件，执行保持阻塞。

## 统计边界

敏感性分析复制主 RQ2 的以下合同：

- 五种方法及其顺序。
- I1 至 I4 的四个 operator。
- attempt-major、operator-minor 的 slot grid。
- 每题 `4 * max(1, projected completion attempts)` 的预算。
- 六类互斥 outcome partition。
- task-level `any target_violation` endpoint。
- 双侧 exact McNemar test。
- paired absolute risk difference。
- 10,000 次 task bootstrap percentile interval。
- 四个基线 contrast 的 Holm correction 和 `alpha=0.05`。

随机选择使用独立 namespace `thesis-tb21-sensitivity-v1`。四个 p value 只进入
`tb21-sensitivity-rq2-v1-four-contrasts`。主 RQ2 的 p value 和 adjusted p value 不重新计算。

跨版本只做并列展示和 disposition 一致性描述。协议不定义跨版本 pooled effect 或显著性检验。

## 结果路径与 non-read gate

协议保留以下结果路径：

```text
runs/terminal-bench-2-1/prefixbench-v1-sensitivity-20261003
evaluation/prefixbench-v1-tb21-sensitivity-canonical.json
evaluation/prefixbench-v1-tb21-sensitivity-readiness.json
evaluation/prefixbench-v1-tb21-sensitivity-offline-campaign.json
evaluation/prefixbench-v1-tb21-sensitivity-method-comparison.json
evaluation/thesis-tb21-sensitivity-v1.json
```

首次冻结还保留：

```text
experiments/prefixbench-v1/tb21-sensitivity-executable-v1.json
```

`freeze` 和 `preflight` 先用 `lstat` 语义检查这些路径是否存在。只有全部不存在时，代码才执行
`git log --all --format=%H -- <path>`。任一路径存在或在可达 ref 中有历史时，操作立即失败。
该 gate 先于协议输入和外部 Git object 读取。

`check` 可以在结果出现后继续验证已提交协议，因此它不执行 non-read gate。

## Git chronology

matrix 和 protocol 必须以相同 bytes 首次出现在同一个 commit `P`。后续 executable manifest
首次出现的 commit 是 `E`。每个结果 producer commit 是 `O`。

```text
P is ancestor of E
E is ancestor of O
```

该证据只证明仓库内的协议和实现先于绑定结果。它不能证明研究者未在仓库外观察结果。

## 故障规则

- 任一上游 commit、tree、manifest blob 或 manifest digest 不符时，冻结失败。
- 两个 89 题 source 的名称集合不同或任一 test 名称缺失时，冻结失败。
- matrix 或 protocol 已存在且 bytes 不同时，CLI 不写任何文件。
- 只有一个产物存在且 bytes 正确时，重复 `freeze` 只补齐缺失产物。
- 61 题未全部完成时，后续敏感性报告不得输出部分推断统计。
- 缺失结果不得重跑替换，只有中断任务可以按相同配置恢复。

## 设计选择

三个候选设计经过交叉评分。候选 1 的 gate 顺序、source identity 和 Harbor 阻塞条件最完整，
因此作为实现基础。

实现吸收候选 2 的 TB2.1 registry package digest 和 matrix 与 protocol 同提交约束。实现还吸收
候选 3 的两项约束：首次冻结时 executable 路径必须没有文件或 Git 历史；后续 executable
必须验证 registry digest 与 task tree 的对应关系。

以下方案不采用：

- 修改现有主协议或 protected source。
- 用任务名作为跨版本 identity。
- 把两个版本的 task rows 或 p value 合并。
- 在协议阶段加入 `run` 命令。
- 在未验证 Harbor selector 前声明 Provider execution ready。
- 为单个固定敏感性分析建立通用多版本实验框架。
