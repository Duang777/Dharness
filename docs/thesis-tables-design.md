# 论文表格生成设计

## 问题

阶段 10 要把确认性分析产物转换为论文正文表格、完整 outcome partition 附录、偏差表、
source binding 与 Git chronology 表，以及原始数据发布清单。生成过程必须可重复，也不能
重新执行模型、环境、mutation campaign、reducer 或统计检验。

主报告只保存 RQ2、RQ3 和 RQ4 的聚合分析。逐 task、slot、reduction case 和 transfer
family 结果仍在三个明细报告中。因此，表格生成器不能只读取主报告。

## 调用者视角

调用者使用两个固定命令：

```bash
uv run python scripts/thesis_tables.py build
uv run python scripts/thesis_tables.py check
```

`build` 写入以下固定路径：

```text
evaluation/thesis-tables-v1.json
docs/thesis-tables-v1.md
```

命令不接受输入路径、输出路径、RQ、筛选条件、精度或科学参数。相同输出保持不变；任一
已有输出内容不同，命令拒绝覆盖。

Python 调用者只需要两个操作：

```python
from evidence_harness_mutation.thesis_tables import (
    build_thesis_tables,
    check_thesis_tables,
)

result = build_thesis_tables(project_root)
errors = check_thesis_tables(project_root)
```

## 输入信任闭包

生成器固定读取 11 份 canonical JSON：

1. 主分析协议。
2. executable manifest。
3. mini-swe-agent cohort。
4. Historical-7。
5. development analysis。
6. held-out readiness。
7. held-out offline campaign。
8. RQ2 报告。
9. RQ3 报告。
10. RQ4 报告。
11. 主分析报告。

loader 要求每个输入是项目目录内的普通文件，并校验 schema、canonical bytes、直接文件
绑定和跨报告 lineage。它验证 main 到 Historical-7、development、RQ2、RQ3、RQ4 的
绑定，RQ2 到 campaign、RQ3 到 RQ2、RQ4 到 cohort，以及 campaign 到 readiness 的绑定。
checker 从 canonical task/case rows 重新计算 RQ2、RQ3 和 RQ4 analysis，再与明细报告和
主报告比较。该步骤不读取 raw journal 或 trajectory。

生成器不跟随 journal、trajectory、run root 或 provider env 路径。

## 结构

JSON bundle 是权威输出，Markdown 是它的确定性视图。

```text
ThesisTableBundle
  source_artifacts[]
  tables[]
    columns[]
    rows[]
      cells[]
        text
        source_refs[]
  reproducibility_index[]
```

每个展示 cell 至少有一个 source reference。reference 包含 source artifact ID 和 RFC 6901
JSON Pointer。精确统计值直接复制 canonical report 中的 12 位 decimal 字符串，不经过
binary float。

provenance derivation 使用封闭枚举：

- `copy`
- `exact_decimal_copy`
- `join_exact_decimal_bounds`
- `join_integer_ratio`
- `fixed_label`
- `missing_member`

`fixed_label` 只允许生成器中显式定义的 label。`missing_member` 必须引用一个存在的对象，
并证明指定成员不存在。它用于表示 canonical artifacts 尚未记录的 producer commit，不能
猜测仓库外事实。

## 固定表格

正文按 RQ1、RQ2、RQ3、RQ4 顺序生成：

- RQ1 报告 Historical-7 pair 分母、vulnerable reproduction rate、fixed confirmation
  rate、paired success rate、inconclusive/error 数、成功阈值和 disposition。Historical-7
  canonical artifact 未记录的 byte-identical rebuild 状态明确显示为 unavailable。
- RQ2 报告五种方法的汇总及四个预注册 contrast。
- RQ3 报告四种 reducer 的汇总及可用的预注册 contrast。
- RQ4 报告 I1-I4 的完整 family summary，I3 保持 `unsupported_by_design`。

附录逐行保留：

- Historical-7 的 revision rows。
- 每个 RQ2 task、method 和 slot outcome。
- 每个 RQ3 case 和 reducer status。
- 每个 RQ4 task 和 family status。
- 主报告中的 protocol deviations。
- 所有直接输入的 path、bytes 和 SHA-256。
- 协议、executable 和 held-out collection 已记录的 chronology。
- 结果与 raw artifact 的发布决策状态。

没有 canonical publication policy 的 raw artifact 使用
`publication_decision_not_recorded`。provider env 使用
`excluded_not_an_artifact`。生成器不把未评审的材料标为可发布或永不发布。

## 写入和检查

`build` 先在内存中生成两份完整 bytes，再预检两个目标。不同内容、symlink 或非普通文件
会在首次写入前失败。缺失文件先写入同目录临时文件，再通过 `fsync`、`chmod 0644` 和
`os.link` 的 no-clobber 语义发布。两个路径位于不同目录，因此这不是跨文件事务；若进程
在第一次发布后退出，使用相同命令重跑会补齐第二个文件。

`check` 只读。它重新加载 11 个输入，重建 bundle 和 Markdown，验证所有 source pointer，
再逐字节比较两个固定输出。成功只表示 canonical projection 已重建，不表示模型、环境、
campaign 或 reducer 已重跑。

## 设计合成

候选 A 作为基础，因为它覆盖完整输入信任闭包，并为每个 cell 保存直接 source reference
和输出侧 reproducibility index。

从候选 B 吸收：

- write-once 的 no-clobber 预检和中断恢复语义。
- `publication_decision_not_recorded`，避免编造发布政策。
- table、row、column ID 唯一性和矩形 row 校验。
- RQ3 空 population 的显式 marker。
- Markdown escaping、只读检查和冲突恢复测试。

拒绝以下方案：

- 只读取主报告，因为它不能恢复完整 partition。
- 使用 JSONPath 或模板 DSL，因为配置会形成第二套研究协议。
- 把 raw artifact 标为 `publishable` 或 `never_publish`，因为现有 canonical artifacts
  没有记录该决策。
- 声称两个输出可跨目录原子提交。

## 取舍

- 接受较大的 JSON 和 Markdown，以换取逐 cell 的机器可查 provenance。
- 接受 11 个固定输入，以换取完整 binding chain。
- 接受一个较大的领域模块，以保持公共接口和调用链短。
- 对缺失的 chronology 或 publication decision 显式报告 unavailable，不推断不存在的事实。

## 风险

- 完整 RQ2 slot appendix 会较长，应作为论文补充材料而非正文。
- Historical-7 没有专用 Pydantic report model，loader 只能执行 canonical JSON 和固定结构
  检查。
- outcome producer commit 尚未出现在所有确认性报告 schema 中，chronology 表只能报告
  canonical artifacts 实际记录的提交。
