# Terminal-Bench 2.0 评测结果

- 数据集: `terminal-bench@2.0`
- 评分完整: `是`
- 所有任务均已尝试: `是`
- 已执行: `89/89`
- 已评分: `89/89`
- 通过 / 失败 / 错误: `59 / 30 / 0`
- 已尝试任务通过率: `66.3%`
- 已评分任务通过率: `66.3%`
- 执行覆盖率: `100.0%`
- 评分覆盖率: `100.0%`

| 任务 | 难度 | 类别 | 模型 | 模式 | 状态 | 奖励 | 停止原因 |
|---|---|---|---|---|---|---:|---|
| adaptive-rejection-sampler | 中等 | 科学计算 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| bn-fit-modify | 困难 | 科学计算 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| break-filter-js-from-html | 中等 | 安全 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 模型失败 |
| build-cython-ext | 中等 | 调试 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 预算耗尽 |
| build-pmars | 中等 | 软件工程 | 不适用 (日志回放) | 回放 | 通过 | 1 |  |
| build-pov-ray | 中等 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 预算耗尽 |
| caffe-cifar-10 | 中等 | 机器学习 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 模型失败 |
| cancel-async-tasks | 困难 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| chess-best-move | 中等 | 游戏 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| circuit-fibsqrt | 困难 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 预算耗尽 |
| cobol-modernization | 简单 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| code-from-image | 中等 | 软件工程 | 不适用 (日志回放) | 回放 | 失败 | 0 |  |
| compile-compcert | 中等 | 系统管理 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| configure-git-webserver | 困难 | 系统管理 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| constraints-scheduling | 中等 | 个人助理 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| count-dataset-tokens | 中等 | 模型训练 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| crack-7z-hash | 中等 | 安全 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 预算耗尽 |
| custom-memory-heap-crash | 中等 | 调试 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| db-wal-recovery | 中等 | 文件操作 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| distribution-search | 中等 | 机器学习 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| dna-assembly | 困难 | 科学计算 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 已验证 |
| dna-insert | 中等 | 科学计算 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 模型主动停止 |
| extract-elf | 中等 | 文件操作 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| extract-moves-from-video | 困难 | 文件操作 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 预算耗尽 |
| feal-differential-cryptanalysis | 困难 | 数学 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 模型失败 |
| feal-linear-cryptanalysis | 困难 | 数学 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 模型失败 |
| filter-js-from-html | 中等 | 安全 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 已验证 |
| financial-document-processor | 中等 | 数据处理 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| fix-code-vulnerability | 困难 | 安全 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| fix-git | 简单 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| fix-ocaml-gc | 困难 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 预算耗尽 |
| gcode-to-text | 中等 | 文件操作 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 预算耗尽 |
| git-leak-recovery | 中等 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| git-multibranch | 中等 | 系统管理 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| gpt2-codegolf | 困难 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 预算耗尽 |
| headless-terminal | 中等 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| hf-model-inference | 中等 | 数据科学 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| install-windows-3.11 | 困难 | 系统管理 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 已验证 |
| kv-store-grpc | 中等 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| large-scale-text-editing | 中等 | 文件操作 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 模型主动停止 |
| largest-eigenval | 中等 | 数学 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| llm-inference-batching-scheduler | 困难 | 机器学习 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| log-summary-date-ranges | 中等 | 数据处理 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| mailman | 中等 | 系统管理 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 预算耗尽 |
| make-doom-for-mips | 困难 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 模型主动停止 |
| make-mips-interpreter | 困难 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 预算耗尽 |
| mcmc-sampling-stan | 困难 | 数据科学 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 预算耗尽 |
| merge-diff-arc-agi-task | 中等 | 调试 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| model-extraction-relu-logits | 困难 | 数学 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 预算耗尽 |
| modernize-scientific-stack | 中等 | 科学计算 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| mteb-leaderboard | 中等 | 数据科学 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| mteb-retrieve | 中等 | 数据科学 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| multi-source-data-merger | 中等 | 数据处理 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| nginx-request-logging | 中等 | 系统管理 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| openssl-selfsigned-cert | 中等 | 安全 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| overfull-hbox | 简单 | 调试 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| password-recovery | 困难 | 安全 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| path-tracing | 困难 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 预算耗尽 |
| path-tracing-reverse | 困难 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 预算耗尽 |
| polyglot-c-py | 中等 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 模型主动停止 |
| polyglot-rust-c | 困难 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 已验证 |
| portfolio-optimization | 中等 | 优化 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| protein-assembly | 困难 | 科学计算 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 已验证 |
| prove-plus-comm | 简单 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| pypi-server | 中等 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| pytorch-model-cli | 中等 | 模型训练 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| pytorch-model-recovery | 中等 | 模型训练 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| qemu-alpine-ssh | 中等 | 系统管理 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 预算耗尽 |
| qemu-startup | 中等 | 系统管理 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| query-optimize | 中等 | 数据科学 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 已验证 |
| raman-fitting | 中等 | 科学计算 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 已验证 |
| regex-chess | 困难 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 预算耗尽 |
| regex-log | 中等 | 数据处理 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 预算耗尽 |
| reshard-c4-data | 中等 | 数据科学 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| rstan-to-pystan | 中等 | 数据科学 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| sam-cell-seg | 困难 | 数据科学 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 模型主动停止 |
| sanitize-git-repo | 中等 | 安全 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 已验证 |
| schemelike-metacircular-eval | 中等 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 已验证 |
| sparql-university | 困难 | 数据查询 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| sqlite-db-truncate | 中等 | 调试 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| sqlite-with-gcov | 中等 | 系统管理 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| torch-pipeline-parallelism | 困难 | 软件工程 | 不适用 (日志回放) | 回放 | 失败 | 0 |  |
| torch-tensor-parallelism | 困难 | 软件工程 | 不适用 (日志回放) | 回放 | 通过 | 1 |  |
| train-fasttext | 困难 | 模型训练 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 预算耗尽 |
| tune-mjcf | 中等 | 科学计算 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 已验证 |
| video-processing | 困难 | 视频处理 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 已验证 |
| vulnerable-secret | 中等 | 安全 | openai/modelhub/gpt-5.6-terra | 实时 | 失败 | 0 | 模型失败 |
| winning-avg-corewars | 中等 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 模型主动停止 |
| write-compressor | 困难 | 软件工程 | openai/modelhub/gpt-5.6-terra | 实时 | 通过 | 1 | 预算耗尽 |

> `replay` 运行会执行先前记录的 Agent 决策。不会发起新的模型调用。
