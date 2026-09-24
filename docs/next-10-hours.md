# 如果再给 10 小时

## 当前起点

固定矩阵 10/10 题均已运行。9 题获得 reward 1.0，`qemu-startup` 因 Rosetta 不支持
syscall 282 且 trial 最终触发 `AgentTimeoutError`，记为 `error`。execution coverage
为 100%，scored coverage 为 90%。9 个通过结果中有 8 个 live trial 和 1 个 replay。

## 0 至 2 小时：在 x86_64 runner 重跑 QEMU

使用原生 x86_64 Linux runner，先确认容器内 QEMU 能启动，且不经过 Rosetta。保持任务、
Agent、预算和 `--n-concurrent 1` 不变。若仍失败，记录 QEMU stderr、`/dev/kvm` 状态和
Harbor exception，不将基础设施异常改写为 verifier 失败。

## 2 至 5 小时：设计隔离 completion check

`overfull-hbox` 和 `model-extraction-relu-logits` 的 Harbor reward 为 1.0，但 Harness
内部因 completion check 需要生成 PDF、日志或矩阵文件而耗尽 repair。设计一个隔离
工作区接口，要求验证写入不能回流任务目录，并保留命令、退出码和输出哈希。

先写威胁模型和失败用例，再改 policy。不要直接允许任意重定向、`cp`、`mkdir` 或工具
输出，因为这会让 finish 检查改变待评分状态。

## 5 至 7 小时：实现与回归

实现隔离检查后，先运行 policy、evidence gate 和 run loop 的单元测试。再用本地 fixture
证明验证产生的文件不会改变任务目录。检查 read-only 默认路径不增加额外容器复制成本。

## 7 至 9 小时：复测两类任务

在稳定模型端点上，以并发 1 重跑一个编译型任务和一个会保存产物的黑盒任务。目标是
Harbor reward 保持 1.0，Harness stop reason 从 `budget_exhausted/model_protocol`
变为 `verified`，且 repair 次数下降。

## 9 至 10 小时：质量门禁与发布

运行 Ruff、全量 pytest、覆盖率、类型检查、build、Agent schema 和本地 Docker smoke。
审查 journal 是否含凭证，确认聚合结果与原始 Harbor reward 一致。若 QEMU 尚未在合适
runner 重跑，继续保留 `error` 和 90% scored coverage，不将其改写为模型失败。
