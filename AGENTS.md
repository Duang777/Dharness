<!-- idoor-agent-collaboration:start schema=1 -->
## iDoor Agent 协作规范

每次处理 iDoor 请求，先执行一次更新门禁，同一任务不重复：
- 检查 bytedcli --json self update --check、idoor --json update --check，并用 npm view --registry=`https://bnpm.byted.org` 对比 idoor-xcli 与 AgentBuddy 当前版本。
- 将上述六个 skills:skills.byted.org/cqc/idoor_xcli/<name> 完整 identifier 一次性传给 agentbuddy skill update <identifier...> --global --check-only --json；只有 errors 为空且 updates.length + unchanged = 6 才通过。
- 发现更新时先说明变化并取得用户确认，再按当前 --help 更新并复查。用户暂不更新则返回 manual_action_required，停止本次 iDoor 业务路由。

职责路由：产品/安装→idoor-product-guide；需求/SOP/验收→idoor-clarify-requirement；探测/开发→idoor-develop-workflow；YAML 独立校验→idoor-validate-workflow；Import/Debug/发布/Task/结果导出→idoor-workflow-debug；LogID 深度排障→idoor-troubleshoot-logid。

工具边界：idoor-xcli 仅用于开发期 Sandbox、API、页面探测和 preset；idoor 仅用于 iDoor 平台控制面；Cookie Bridge 仅在页面探测需要浏览器登录态且用户另行授权时同步。

需求未确版不生成正式 YAML；开发结果不得自签准出，只有绑定当前 YAML SHA256 且 verdict=ready_for_debug 的独立报告才能进入平台 Debug。平台写操作先 dry-run，再由用户确认精确目标和影响。保留 request LogID，不索取或打印凭证。接入通过不代表业务流程已通过。
<!-- idoor-agent-collaboration:end -->
