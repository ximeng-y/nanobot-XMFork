# 设计约束

更改归属边界、扩展点、抽象或动态类型时，请查阅本文。这些约束适用于相关路径上的实现选择。

## 保持核心精简，在边缘扩展

新能力应通过 `channels/`、`tools/`、技能或 MCP 服务器添加。`agent/loop.py` 和 `agent/runner.py` 构成关键核心路径；对它们的更改应尽可能少且有充分理由。如果某项功能可以放在渠道适配器、工具或外部 MCP 服务器中，就不要直接嵌入 Agent 循环。

运行时状态分发遵循同样的边界。`MessageBus.publish` 会等待本地订阅者处理轮次、运行、模型和目标状态变化；`MessageBus.publish_event` 将渠道路由投递放入队列，不等待网络发送。两者都携带 `AgentEvent` 值。Runner 钩子通过当前轮次作用域内的 `EventSink` 发布带类型的输出；直接调用回调在执行边界进行适配。WebUI/WebSocket 传输细节、标题刷新和目标状态同步应放在 `nanobot.session.webui_turns.WebuiTurnCoordinator` 或相应渠道适配器中。`nanobot.webui.outbound_delivery.WebUIOutboundDelivery` 负责 WebUI 出站投影、流缓冲区、对话记录策略和投递轮次完成；WebSocket 传输只保存接收者快照并将公开帧放入队列，同时维护连接顺序、背压和排空计数。

工具和渠道各自负责其配置、元数据和启用行为。工具配置模型与实现一起放在 `agent/tools/*_config.py`；渠道描述符和配置保留在各自的渠道包中。`config/schema.py` 中的根模式负责组装这些约定，但不导入其运行时实现。工具类通过 `config_key` 和 `config_cls()` 公开其配置。

通用恢复检查点放在 `session/recovery.py`；WebUI 恢复和轮次协调放在 `webui/recovery.py` 与 `session/webui_turns.py`。网关启动时提供 WebUI 轮次策略。渠道负责 `channels/notification_routes.py` 中的纯地址约定；核心轮次投递使用该约定实现默认回复和持久通知，无需加载渠道运行时实现。

## 可执行的导入边界

核心执行、会话存储和安全原语独立于 API、渠道、CLI 和 WebUI 适配器实现。核心可以使用渠道负责的纯地址约定，但这些约定必须独立于运行时实现。共享客户端约定独立于 UI 实现，客户端初始化依赖不得成环。

Python 规则位于 [scripts/check_import_boundaries.py](../scripts/check_import_boundaries.py)；在仓库根目录运行 `python -m scripts.check_import_boundaries`。客户端规则位于 [scripts/check_client_import_boundaries.mjs](../scripts/check_client_import_boundaries.mjs)；在对应项目的 `webui/` 或 `tui/` 中运行 `bun run check:imports` 和 `bun run test:imports`。这些脚本是被检查模块、依赖规则和例外的权威依据。

这些检查覆盖选定的直接导入和客户端初始化依赖，并不构成完整的运行时依赖图；执行、状态归属和安全约定仍需行为验证。

## 让更改在局部也易于理解

按代码负责的行为分组，不要以目标文件长度为依据。有用的提取应通过窄小的带类型接口管理自己的状态、订阅和清理；只移动辅助函数却仍暴露原组件的全部状态，并不能建立边界。如果拆分协作逻辑反而需要更多上下文才能理解同一项更改，就将它们保留在一起。归属发生转移时，保留跨归属测试，并将任务导航指向新的负责方。

## 宁可适度重复，也不要过早抽象

渠道和 Provider 可以重复类似逻辑（发送重试、媒体处理、消息拆分）。不要为了消除不同渠道文件之间的重复而引入复杂基类或共享辅助函数。每个渠道文件都应保持自包含，可以独立阅读。Provider 实现同样如此。

## 在边缘为动态边界定义类型

传输负载、持久化记录和第三方 SDK 对象都是不可信的动态边界。优先在负责相关功能的边缘使用解析器或小型规范化器，并为稳定的字典结构使用 `TypedDict`，使验证只进行一次，内部代码接收具体类型。不要让原始动态字典或 SDK 对象在核心中扩散。

稳定的第一方依赖必须在存储或传递时定义类型。不要将内部服务、上下文字段或回调结果声明为 `Any`，再让使用方通过强制类型转换恢复真实类型。使用具体类型或窄小的 `Protocol`；`Any` 只留给真正动态的边界。

每次新增的强制类型转换都必须有边界检查，或由构造过程和控制流建立的不变量作为依据。不要在已验证路径中添加冗余检查，也不要仅为消除 BasedPyright 报错而使用 `cast`。

## 显式优先于隐式魔法

Provider 自动检测必须能从工厂一直追溯到具体 Provider 类。
