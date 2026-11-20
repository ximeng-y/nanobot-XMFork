# 安全边界

修改路径访问、对外 HTTP/MCP 或 Shell 执行时，请查阅本文档。Agent 运行时拥有文件系统、Shell 和网络访问权限；所有受影响的路径都必须保留下列防护措施。

## 工作区限制

文件系统工具和原生文件搜索使用 `nanobot/execution/files.py` 中受范围限制的操作。这些操作在打开文件、列出目录或写入补丁前，会通过 `nanobot/security/workspace_policy.py` 对请求实际使用的工作区应用权限策略。编辑活动快照使用同一个已授权的写入目标，而不是工具参数中的路径。受限模式下，媒体上传目录是内部附加的只读根目录。这些是应用层检查，无法像操作系统隔离那样阻止其他进程同时替换祖先目录。

附加文件系统根目录必须按权限类型划分。`extra_allowed_dirs` 是旧版只读别名。只读根目录使用 `extra_read_allowed_dirs`；只有明确允许具备写权限的工具修改附加目录时，才使用 `extra_write_allowed_dirs`；工具只能修改特定文件时，使用精确的文件允许列表。

Shell 执行（`ExecTool`、`agent/tools/shell.py`）也将 `restrict_to_workspace` 作为应用层防护：启用后，如果 `working_dir` 位于工作区外，命令会在执行前被拒绝，同时会检查命令文本中明显的逃逸工作区行为。这不是进程级隔离；如需进程级隔离，请使用执行沙箱后端。

**规则**：文件工具适配器应请求受范围限制的文件操作，不要在适配器中直接添加路径授权或文件系统操作。遍历目录时，先授权发现的文件目标，再读取文件。通过子进程执行的搜索遵循下文的 Shell 规则。

## SSRF 防护

Agent 工具发出的所有对外 HTTP 请求都必须经过 `security/network.py` 中的共享 URL 防护（`validate_url_target` 或 `resolve_url_target`）。默认情况下，它们会阻止环回地址、RFC1918 私有地址、CGNAT 网段、链路本地网段和云元数据端点（包括 `169.254.169.254`）。

对于直接请求，唯一的豁免方式是 `configure_ssrf_whitelist(cidrs)`，它在加载时读取 `config.tools.ssrf_whitelist`。由 `providers.<name>.useProxy` 启用的共享 `network.proxy` 地址是独立的、经用户授权的信任边界，适用于 Provider 请求和 Provider 返回的图片 URL 下载。这些下载在每次重定向时仍会拒绝格式错误的 URL 以及可在本地识别出的私有/内部目标，但本地 DNS 无法解析的主机名会交由受信任的代理处理。最终 DNS 解析和网络出口策略由用户选择的代理负责。

HTTP/SSE MCP 传输也属于此边界：在探测或构造客户端前验证配置的 MCP URL，并在跟随重定向前验证每个发出的 HTTP 请求。只有通过显式 SSRF 白名单，才能允许本地/私有 HTTP MCP 端点。Stdio MCP 服务器不经过 HTTP SSRF 路径。

**规则**：不要在工具中新增直接调用 `httpx.get` / `requests.get` 的代码。通过现有网页抓取工具处理，或复刻 `validate_url_target` 检查。

## Shell 沙箱

`execution/sandbox.py` 为 Shell 执行和设置探测构建沙箱参数列表。`security/sandbox_launcher.py` 解析可信的宿主机启动程序。Linux 上使用 `bwrap`（bubblewrap），macOS 上使用 `seatbelt`（`sandbox-exec`）。Seatbelt 不得暴露宿主机共享临时目录；临时工作区保留在工作区内。在 Windows 上，配置了后端时仅发出警告，只保留应用层防护。在 Unix 上，已配置的后端如果无法启动，必须失败，不得在没有隔离的情况下静默执行。

**规则**：新增沙箱后端时，必须符合 `nanobot/execution/sandbox.py` 中的后端可调用对象约定，并在 `_BACKENDS` 中注册。保留读/写根目录处理方式及已配置后端的失败行为。
