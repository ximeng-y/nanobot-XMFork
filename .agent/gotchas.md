# 常见注意事项

## 严格类型检查所需的 Channel 依赖

Channel 依赖来自各 Channel 包清单，不包含在主 extras 中。使用 `uv sync --all-extras --dev` 复现 CI 环境，然后运行 `uv run --no-sync python -m scripts.install_channel_dependencies --all-channels`。后续的 `uv run` 命令要保留 `--no-sync`，避免同步时移除这些依赖。检查项见 `.github/workflows/ci.yml`。

## Channel 自有 UI 与 WebUI 传输

Channel 可以在 `nanobot/channels/*/webui/` 中拥有前端代码，在 `nanobot/channels/*/tests/` 中拥有测试，包括 `tests/webui/`。WebUI 的 lint 和测试配置包含这些前端路径。不要假设所有 Channel 测试都位于根目录的 `tests/` 下，或所有前端代码都位于 `webui/` 下。

Vite 代理 `/api`、`/webui` 和 `/auth`；应用的 WebSocket 直接连接网关。`NANOBOT_API_URL` 用于设置 HTTP 代理目标，默认为 `http://127.0.0.1:8765`。详见 `webui/vite.config.ts`。

## 配置中的 `${VAR}` 引用

`nanobot/config/loader.py` 负责加载和验证配置；`resolve_config_env_vars` 在运行时使用前解析 `${VAR}` 引用。这不是类似 Shell 的默认值语法。引用的变量缺失时，会抛出带字段位置的 `ConfigLoadError`；已有配置文件内容无效时，也会抛出 `ConfigLoadError`。只有配置文件不存在时才会创建默认配置，不会用默认配置修复无效文件。

## Windows 兼容性

nanobot 明确支持 Windows。需要注意以下主要差异：
- `ExecTool` 在 Windows 上默认使用 PowerShell（可用时使用 `pwsh`，否则使用 Windows PowerShell）；如需 cmd.exe 语法或 cmd 内置命令，请传入 `shell="cmd"`（`shell.py`）。
- `nanobot/cli/entry.py` 在分发前将 Windows 控制台输出配置为 UTF-8，以处理 emoji 和多语言输入。
- MCP stdio 服务器命令会针对 Windows 路径分隔符进行规范化（`mcp.py`）。

## 提示词模板

Agent 系统提示词和特定场景的指令位于 `nanobot/templates/`，包括 `agent/identity.md` 和 `agent/platform_policy.md`；工作区默认文件包括 `AGENTS.md`、`HEARTBEAT.md` 和 `SOUL.md`。提示词渲染由 `nanobot/utils/prompt_templates.py` 负责。修改这些文件会像修改 Python 代码一样直接改变 Agent 行为。

仓库根目录的 `AGENTS.md` 用于指导编码 Agent；`nanobot/templates/AGENTS.md` 是 nanobot 工作区的默认模板。`nanobot/agent/context.py` 加载项目的 `AGENTS.md` 和全局 `SOUL.md`/`USER.md`，并跳过未改动的内置 `AGENTS.md` 和 `USER.md` 默认文件。它不会实现 Codex 的祖先目录指令链。

工具描述、技能和重放的会话历史也会影响模型行为。将这些界面的修改视为运行时代码：保持改动聚焦，验证受影响的约定，避免让模型学会重复内部标记、本地路径或工具调用文本。当能够证明存在现有测试未覆盖的可达故障时，添加回归测试。

## 上下文污染会持续存在

写入记忆、会话历史或提示词输入的任何内容，都可能在未来 LLM 调用中被重放。时间戳、本地媒体路径、工具调用回显和原始回退转储等元数据，在成为模型模仿的示例之前，必须限制范围并清理。

## 技能作为扩展点

内置技能位于 `nanobot/skills/`（Markdown + YAML frontmatter 格式）。属于“实操知识”而非代码的 Agent 能力应作为技能添加，而不是硬编码进 Agent 循环。外部技能可发布到 ClawHub，也可从 ClawHub 安装。

## 持久化与耐久性

`nanobot/agent/memory.py` 负责记忆日志 `memory/history.jsonl`；普通条目在锁保护下追加，不显式执行 fsync。完整重写日志时使用 `atomic_write_lines`，并采用其默认的文件和目录 fsync。保留这种原子替换方式，不要直接截断正在使用的日志。

会话历史存储在 `sessions.sqlite3` 中。运行时访问由 `SessionState`（`nanobot/session/state.py`）负责；`SqliteSessionStore`（`nanobot/session/sqlite_store.py`）在每个连接上使用 WAL 和 `PRAGMA synchronous = FULL`，通过 SQLite 事务提交更改。`SessionManager.save()` 用于离线管理快照，不支持每次保存时执行 fsync 的选项。运行时修改应通过异步持有者执行，并保留事务边界和过期代次检查。
