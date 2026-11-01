# Dharness 开题原始文献核验

2026-10-30；`cb21942a087dae86211be6418448f3fc8d4f71be`。未复现实验；清单据 [Issue #16](https://github.com/Duang777/Dharness/issues/16)。

## 指定来源

作者列首位。七项题名、作者、首投日期及摘要已在原站核对。

|来源|作者；日期|已读范围与结论|
|---|---|---|
|[SpecHarness / Who Holds the Pen?](https://arxiv.org/html/2609.29921v1)|Haiqing Li 等；2026-09-24|摘要、HTML 正文及附录。证据提交、新鲜度已有；B.3 绑定工件/输入/验证器/环境/依赖版本。248 次突变检验新鲜度失效；C.2 承认在线反馈、轨迹、实际计算量不匹配，未隔离提交机制效果。|
|[Teaching Agents to Code Reliably](https://arxiv.org/html/2610.03984v1)|Muhammad Ahmed Mohsin 等；2026-10-02|摘要、HTML 正文及附录，重点§2、3.1、B.2/3/11。补丁/回退差分执行、突变奖励、固定候选验证对照。270 题未用于训练但参与方法诊断；有限突变集不证明语义正确。|
|[Goal-Autopilot](https://arxiv.org/abs/2606.11688)|Youwang Deng；2026-06-10|仅元数据、摘要。持久 FSM、执行门禁、条件化无假成功定理。|
|[ECT](https://arxiv.org/html/2608.23623v1)|Jason Liu；2026-08-22|摘要、正文契约及验证方法。任务/账本摘要绑定、回执作用域和封闭重放；明确只证轨迹支持，不证外部真值。|
|[Bounded Loops](https://arxiv.org/abs/2609.27871)|Varun Pratap Bhardwaj 等；页面 2026-08-20|摘要与正文§1，独立门禁、预算预留、留出突变已有。编号 2609 却标 8 月首投；日期存疑，暂缓引用。|
|[AgentChaos](https://arxiv.org/html/2608.06790v1)|Gou Tan 等；2026-08-07|摘要、§3 至 4.4。HTTP 响应故障、每任务状态、旧缓存重放与触发核验已有；不等同协议状态前缀突变。|
|[ToolFuzz](https://arxiv.org/html/2503.04479v3)|Ivan Milev 等；2025-03-06，v3 为 03-11|摘要、§3/4。污点引导工具输入、同义提示及一致性/LLM 判断；对象为工具文档。|

## 补充近邻

- [RESTler](https://patricegodefroid.github.io/public_psfiles/icse2019.pdf)，Atlidakis 等，ICSE 2019。读 PDF 第 1、3、4 页：依赖可达请求序列、执行反馈剪枝已有。
- [in-toto](https://www.usenix.org/conference/usenixsecurity19/presentation/torres-arias)，Torres-Arias 等，2019。会议摘要及[官方说明](https://github.com/in-toto/in-toto)已读，材料/产物链及签名证据已有；PDF 返回 403，正文未核验。
- [SWT-Bench](https://arxiv.org/html/2406.12952v2)，Mündler 等，2024。摘要与引言：修复前失败、修复后通过及变更覆盖已有。
- [SWE-rebench](https://arxiv.org/html/2505.20411v2)，Badertdinov 等，2025。摘要、§2.3/2.4、3.1/3.2：隔离执行、固定依赖、测试质量、数据污染及脚手架过拟合。数据污染与代理改测试、运行残留污染须分开测。
- [Delta Debugging 作者说明](https://www.debuggingbook.org/html/DeltaDebugger.html)，读 Synopsis，同异常缩减已有；2002 原论文全文未取得。

## 待验证贡献与反驳

1. 候选快照锚定的终端提交：在同测试、同候选、同预算下，`epoch/attempt/candidate_digest`绑定与隔离重验能否减少旧回执复用和验后变更误接受，同时保住正确完成率。须明确挂载卷、进程、网络及验证器版本的边界。
2. PrefixBench：对可达前缀注入状态故障，用独立判据保留同一违规及候选/证据因果关系；检验能否比无状态突变及普通缩减发现更多真实缺陷，并在留出算子、自然故障、第二实现上成立。

最大反驳是既有机制组合：SpecHarness 已绑定依赖版本，通用缩减也保持失败；缺配对效果证据时，三字段与 witness 命名不足以构成研究创新。哈希、FSM、拒绝不明证据、容器重跑、预算预留、突变与缩减均不能单独称创新。

## 公平实验

先固定正确/错误候选与同前缀，配对比较自验证、独立末端重跑、版本证据门禁、完整协议；消融各绑定维度、隔离、预算预留。随后在线同模型/提示/工具/总预算、配对种子，核算验证开销。隐藏判定测试只作终评，冻结验证器，保留正确替代解；分离开发/留出故障家族，核验注入触发并报告未触发、基础设施失败。报告误接受率、正确完成/误拒绝、恢复率、成本及任务聚类置信区间，避免靠全拒绝取胜。真实任务与自然故障检验外推，跨 harness 须在线实验。

