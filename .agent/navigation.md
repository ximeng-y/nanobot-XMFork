# 定位负责相关功能的代码

从下表中选择受影响的行为，再阅读负责该行为的代码和现有测试。只需查阅相关链接；本文是任务索引，不是必读书单。先在选定区域中搜索用户可见的操作，再扩大到调用方和共享约定。

| 更改内容 | 从这里开始 | 现有覆盖 |
| --- | --- | --- |
| 浏览器输入、配对、导航、历史记录或设置 | [WebUI 更改地图](../webui/README.md#change-a-behavior) | 该地图将每种行为与组件测试和浏览器测试对应起来 |
| 终端输入、菜单或渲染 | [TUI 源码布局](../tui/README.md#source-layout) | 同目录测试；应用交互位于 [`tui/src/app/`](../tui/src/app/) |
| 聊天渠道或其设置 UI | [渠道包指南](../docs/channel-package-guide.md)，然后是 [`nanobot/channels/`](../nanobot/channels/) 中对应的包 | 渠道自己的 `tests/`，包括 `tests/webui/`；共享约定位于 [`tests/channels/`](../tests/channels/) |
| Provider 选择或模型请求 | [Provider 扩展指南](../docs/development.md#adding-an-llm-provider)、[`providers/routing.py`](../nanobot/providers/routing.py)，然后是选定的适配器 | [`tests/providers/`](../tests/providers/) |
| 工具参数或执行 | [`agent/tools/`](../nanobot/agent/tools/) 中的实现和相邻配置；共享约定见 [`base.py`](../nanobot/agent/tools/base.py) | [`tests/tools/`](../tests/tools/)；运行时和会话工具位于 [`tests/agent/tools/`](../tests/agent/tools/) |
| Shell 进程启动或清理 | [`execution/processes.py`](../nanobot/execution/processes.py) 负责原生进程树；[`agent/tools/exec_session.py`](../nanobot/agent/tools/exec_session.py) 负责会话状态和输出 | [`test_exec_platform.py`](../tests/tools/test_exec_platform.py)、[`test_shell_reap.py`](../tests/tools/test_shell_reap.py)、[`test_shell_execution_budgets.py`](../tests/tools/test_shell_execution_budgets.py) |
| 抓取内容时的对外 HTTP 重定向 | [`execution/http.py`](../nanobot/execution/http.py) 负责 GET 请求和响应生命周期；[`security/network.py`](../nanobot/security/network.py) 负责 URL 策略和 DNS 验证 | [`test_web_fetch_security.py`](../tests/tools/test_web_fetch_security.py)、[`test_image_generation_security.py`](../tests/providers/test_image_generation_security.py) |
| 斜杠命令或命令分发 | [`command/router.py`](../nanobot/command/router.py)、[`command/builtin.py`](../nanobot/command/builtin.py) | [`tests/command/`](../tests/command/) |
| 轮次调度、取消或投递 | [`agent/loop.py`](../nanobot/agent/loop.py)、[`agent/turn_delivery.py`](../nanobot/agent/turn_delivery.py) | [`test_loop_concurrency.py`](../tests/agent/test_loop_concurrency.py)、[`test_task_cancel.py`](../tests/agent/test_task_cancel.py)、[`test_turn_delivery.py`](../tests/agent/test_turn_delivery.py) |
| 模型/工具迭代、重试或注入输入 | [`agent/runner.py`](../nanobot/agent/runner.py)；[`agent/pending_inputs.py`](../nanobot/agent/pending_inputs.py) 负责运行内的输入批次和终止前等待，`AgentLoop` 负责准备每条输入 | [`test_runner_core.py`](../tests/agent/test_runner_core.py)、[`test_runner_fallback.py`](../tests/agent/test_runner_fallback.py)、[`test_runner_injections.py`](../tests/agent/test_runner_injections.py) |
| 提示词组装、技能或上下文限制 | [`agent/context.py`](../nanobot/agent/context.py)、[`agent/context_governance.py`](../nanobot/agent/context_governance.py)；[提示词注意事项](gotchas.md#prompt-templates) | [`test_context_builder.py`](../tests/agent/test_context_builder.py)、[`test_skills_loader.py`](../tests/agent/test_skills_loader.py)、[`test_context_governance.py`](../tests/agent/test_context_governance.py) |
| 会话持久化或恢复 | [`session/state.py`](../nanobot/session/state.py)、[`session/sqlite_store.py`](../nanobot/session/sqlite_store.py)、[`session/recovery.py`](../nanobot/session/recovery.py) | [`tests/session/`](../tests/session/)、[`test_loop_recovery.py`](../tests/agent/test_loop_recovery.py) |
| WebUI/TUI 传输事件或显示的历史记录 | [`webui/outbound_delivery.py`](../nanobot/webui/outbound_delivery.py)、[`webui/transcript.py`](../nanobot/webui/transcript.py)；[客户端兼容性](review-guide.md) | [`tests/webui/`](../tests/webui/)、[WebSocket 测试](../nanobot/channels/websocket/tests/)和受影响的客户端测试 |
| 工作区访问或沙箱限制 | [`security/workspace_access.py`](../nanobot/security/workspace_access.py)、[`execution/files.py`](../nanobot/execution/files.py)、[`execution/sandbox.py`](../nanobot/execution/sandbox.py)；[安全规则](security.md) | [`tests/security/`](../tests/security/)、[`test_workspace_scope.py`](../tests/agent/test_workspace_scope.py)、[`test_sandbox.py`](../tests/tools/test_sandbox.py) |
| 网关启动、关闭或启动器 | [`gateway/`](../nanobot/gateway/)、[`cli/gateway_runtime.py`](../nanobot/cli/gateway_runtime.py) | [`tests/gateway/`](../tests/gateway/)、[`tests/cli/`](../tests/cli/) 中对应的启动器测试 |

需要了解端到端运行时流程时，继续阅读[架构](../docs/architecture.md)。更改归属边界时，阅读[设计约束](design.md)；目录相邻并不意味着可以跨越这些边界。

## 找到最小且有效的检查

从覆盖所改操作及其相关失败或生命周期边界的现有测试开始。上面的文件名只是入口，不是完整的验收清单。更改跨越归属边界时，保留集成覆盖。

| 层面 | 针对性命令 | 环境准备和完整检查 |
| --- | --- | --- |
| Python | 在根目录运行：`uv run --no-sync python -m pytest tests/path/test_feature.py` | [贡献环境准备](../CONTRIBUTING.md#development-setup)，包括严格检查所需的渠道依赖 |
| WebUI | 在 `webui/` 中运行：`bun run test src/tests/feature.test.tsx` | [WebUI 检查](../webui/README.md#test)；同一配置也包含渠道 UI 测试 |
| 浏览器 | 在 `webui/` 中运行：`bun run test:browser browser/feature.spec.ts` | [WebUI 检查](../webui/README.md#test)和受影响交互的现有测试夹具 |
| TUI | 在 `tui/` 中运行：`bun test src/feature/feature.test.ts` | [TUI 检查](../tui/README.md)；终端行为仍需经过真实终端路径验证 |

表中的命令路径是占位符，请替换为选定的现有文件。遵守任务规定的本地/远程验证边界。针对性检查能缩短反馈时间，但不能替代[工作流](../.github/workflows/ci.yml)及其[路径规则](../scripts/ci_scope.py)选出的必要 CI 检查。
