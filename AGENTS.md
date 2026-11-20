# 开发 nanobot

Python 网关负责 Agent 执行、会话、工具、记忆和安全策略。WebUI 和 TUI 共用这套运行时；执行逻辑和策略不要放到客户端中。

WebUI 与网关只支持并测试相同版本的组合，远程网关也一样。不同版本之间可能可以使用，但即使通过协议检查，也不保证兼容；排查问题时先统一版本。不要仅因软件包版本不同就拒绝连接，也不要仅为支持旧网关添加兼容回退或协商逻辑。

## 按任务查阅指南

| 工作内容 | 查阅文档 |
| --- | --- |
| 定位负责相关功能的代码和有针对性的测试 | [`.agent/navigation.md`](.agent/navigation.md) |
| 核心边界、扩展或内部类型 | [`.agent/design.md`](.agent/design.md) |
| 重构、回退方案或测试选择 | [`.agent/simplify.md`](.agent/simplify.md) |
| 面向用户的文案、功能命名或本地化 | [`.agent/copywriting.md`](.agent/copywriting.md) |
| 路径权限、HTTP/MCP 或 Shell 隔离 | [`.agent/security.md`](.agent/security.md) |
| 依赖安装、WebUI 传输、配置、Windows、提示词或持久化 | [`.agent/gotchas.md`](.agent/gotchas.md) |
| 复用验证证据 | [`.agent/workflow.md`](.agent/workflow.md) |
| WebUI 与宿主的兼容性 | [`.agent/review-guide.md`](.agent/review-guide.md) |
| 贡献或发布 | [`CONTRIBUTING.md`](CONTRIBUTING.md)、[`docs/releasing.md`](docs/releasing.md) |

## 开发约束

- 扩展现有代码前，先分析所需行为、状态归属和根本原因。如果问题由结构引起，就进行重构；不能为了让 diff 更小就再增加一层回退逻辑。
- 不要为假设中的内部状态或不受支持的组合添加防御性测试。每个新测试都必须对应一条可达路径，并保护一项有意义的行为约定。
- 不要仅仅为了证明某种不存在的行为、已删除的代码或废弃的符号不存在而添加测试。否定性断言必须针对具体且可达的风险，并保护可观察的行为约定；应优先测试受支持的操作及其结果。
- 不要运行 `ruff format`；机械格式化会影响 git blame 的可读性，并产生无关 diff。此约束优先于 `CONTRIBUTING.md` 中对已修改文件进行可选格式化的要求。
- 不要在 Markdown 文件中手动为正文换行。每个段落在源码中保持为一行；只有 Markdown 结构需要时才使用换行。
