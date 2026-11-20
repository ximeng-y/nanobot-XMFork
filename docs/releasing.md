# 发布检查清单

请将本检查清单与[发布打包约定](../CONTRIBUTING.md#release-packaging-contract)配合使用。准备工作不代表已获准发布。推送标签、发布 GitHub Release、上传到 PyPI 和部署文档均为独立操作。

## 发布节奏

Stable 是默认更新源，Preview 需要主动订阅。两者共用同一套更新流程，安装前都必须确认。发布节奏再快、用户是否订阅，都不允许自动停止任务或重启网关。用户操作说明请参阅[更新 nanobot](./updating.md)。

| 发布类型 | 触发方式 | 公开目的地 | 文档 |
| --- | --- | --- | --- |
| Stable 功能版本 | 需要时手动触发 | 正式 GitHub Release 和 PyPI；还要将其清单加入索引，供 Preview 订阅者使用 | 完整的版本文档及计划发布的公告 |
| Stable 补丁 | 修复通过评审后手动触发；不必等待功能版本里程碑 | 与 Stable 相同的目的地和检查 | 生成的变更说明及必要的升级指引；只更新受影响的文档 |
| Preview / dev | 维护者启用后，由每日工作流触发 | GitHub 预发布和受保护的 `release-index` 目录；不发布到 PyPI | 生成的变更说明；不为每次构建制作新版文档 |

这是三条发布线，不是三套构建系统。功能版本和补丁版本都使用 `channel=stable`；dev 使用现有的 `preview` 通道。发布补丁前不需要先发 Preview，也不需要全面更新 Wiki 和宣传内容。必要的测试和发布检查必须保留；未受影响的验证证据可按照[验证指南](../.agent/workflow.md)复用。

实现状态：**Verify Release Candidate** 支持手动准备和选择启用的每日 Preview 准备。它生成说明草稿，并复用相同的五平台构建与验收任务，不执行发布。自动预发布上传、签名交接和目录推进尚未接通；配置可信发布者后，PyPI 发布还需要单独的手动工作流。每日准备默认关闭。不要把这第一步描述成已可用的每日公开发布通道。

记录请求的通道、准确的源代码和候选版本标识。源代码未变、检查失败、资产缺失、验收不完整或未获批准时，均保留上一次通过验证的发布。计划任务可能延迟执行，不保证每天都有新版本。验收必须覆盖受支持的 Stable 基线和较早的 Preview（包括跨过的 Preview），不能只检查上一个构建。

### 共用的准备入口

在选定的源代码 ref 上运行 **Verify Release Candidate**，使用现有的 `next_stable`、`channel` 和可选的 `retain_installable` 输入。两种 Stable 发布都使用这个入口。工作流根据提交标题生成 `release-notes.md`，并将其纳入候选报告和明确要求保留的可安装交接包。发布前检查必要的升级说明和安全措辞。这些说明是本地工件，不是 GitHub Release，草稿 Release 也不算。

Stable 说明从所选源代码线上的上一个正式版本开始，忽略 Preview 标签。Preview 说明可以从上一个 Preview 的源代码开始。两者都不使用无关分支上的最新标签。整个运行过程中，计划使用的源代码始终是事件 SHA；准备工作不会跟随不断变化的分支。

如需每日准备，维护者之后可以将 `NANOBOT_PREVIEW_BASE` 设置为下一个尚未发布的正式版本，并明确启用 `NANOBOT_DAILY_CANDIDATES_ENABLED=true`。目前的计划执行时间为 UTC 00:17。只有官方仓库的 `main` 可以使用此功能。它要求该精确 SHA 最新一次 push CI 成功，并跳过已经通过此工作流验收的 source/base 组合。失败的候选版本可以重新运行；仍可手动触发，以保留新的交接包。两条路径都使用现有的工作流运行编号生成 `.devN`。对应正式版本发布后，要更新 base；已经打过标签的 base 会被拒绝。

每日运行只保留小型报告，不保留可选的可安装交接包。它仍使用现有的五个原生运行器和临时 wheel 传输，因此启用该变量前，应评估运行器和存储成本。这只是准备开关，不代表允许签名或发布。添加此工作流代码不会更改任何仓库变量或保护设置。

### Stable 补丁流程

1. 选择受支持的 Stable 基线和已通过评审的修复。如果 `main` 含有尚未完成的功能，请从该基线创建分支，只回填所需修复。不得把未经验证的 Preview 改标为 Stable。
2. 分配一个未使用的正式版本号，更新两处版本声明，并遵循**准备候选版本**的流程。补丁与功能版本一样，必须通过打包、许可、原生启动、数据保留以及更新/恢复这些发布门槛。
3. 按顺序发布获批的不可变文件：GitHub、PyPI、公开下载检查，最后推进目录中的 `stable` 指针。保留 `preview` 和撤回记录。如果 Stable 补丁比用户已安装的 Preview 更旧，不得将其作为降级版本提供给用户。
4. 发布简明的修复说明和必要的运维操作。没有此更新器的用户，首次升级仍需使用原来的安装方式；外部软件包或部署的负责人继续沿用各自的更新途径。

### Preview 流程

1. 选择已通过评审且必需 CI 全部通过的 `main` 精确提交；如果其源代码 SHA 与上一个通过验收的候选版本相同，则跳过。记录下一个尚未发布的 Stable base 和未使用、递增的 Preview 序号。
2. 针对该 ref 和 base 使用 **Verify Release Candidate**，可手动运行，也可通过已启用的每日准备运行。它准备只修改版本的候选版本，一次性构建 WebUI 和五个原生 TUI 包，并在不发布的情况下，对与清单精确绑定的 wheel 运行 macOS arm64/x64、Linux arm64/x64 和 Windows x64 的全新安装及打包更新/恢复验收。普通 PR CI 仍使用 Linux x64/Windows x64 矩阵；候选版本准备使用全部五个原生运行器。**Candidate required** 检查必须通过，仅有构建报告不够。用户获得的是构建好的软件包，而不是还需要 Bun 的源码检出。
3. 选择 `retain_installable`，并在过期前保存已通过验收的 `installable-candidate` 交接包。临时的 `candidate-native` 输入和 `prepared-candidate` 工件不算通过验收的交接包。针对这些完全相同的字节完成真实历史基线和交互验收；自动化故障注入夹具不能证明与先前发布版本的兼容性，也不能证明交互式桌面行为。将候选 SHA、清单哈希、覆盖范围、已知问题和源码提供承诺的批准记录放在一起；仅有 CI 报告，既不等于可安装工件，也不等于同意发布。
4. 经单独批准，在**候选**提交上发布不可变的 `v<version>` 和 GitHub 预发布，绝不能使用最新的 Stable 发布。上传完整且经过验证的文件集、清单和说明；确保固定版本的安装器源码可以公开访问。
5. 以新的目录生成推进 `preview` 前，验证所有公开文件的大小和哈希，并完成公开的全新安装/更新验收。根据最新目录进行验证，保留 `stable` 和撤回记录，并串行处理 Stable、Preview 和撤回写入。发布方如果持有过期的目录状态，必须重新读取并验证。
6. 只上传了一部分文件时，保持更新源不变，只能使用原先经过验证的文件继续。字节丢失或发生变化时，必须使用新版本号并重新验收。撤回不安全的候选版本时要说明原因，并提供包含修复的更新版本；绝不能回滚更新源或恢复用户数据。

使用 Python 版本排序：`0.3.6.dev42 < 0.3.6.dev43 < 0.3.6 < 0.3.7.dev1`（示例版本）。正式 base 发布后，Preview 必须使用更晚的 base；分配时应考虑 Stable 补丁。工作流使用 `github.run_number` 作为 `X.Y.Z.devN` 中的 `N`；未检查已发布版本前，不得重置序号。重新运行不能替换已经发布的字节。正式版本必须以其正式标识重新构建和测试；绝不能重命名 `.devN` wheel。

### 上线门槛与自动化边界

提供公开 Preview 前，必须就索引所有权和保护、发布凭据、源码保留，以及精确打包文件和公开下载的验收获得批准。准备候选版本不会启用公开通道、计划任务、受保护的发布方或 PyPI 上传。获批的发布字节和源码必须按要求期限保存在会过期的 CI 存储之外。

Stable 发布仍需手动进行。每日 Preview 准备需要上述明确 opt-in；自动发布则需要单独实现、评审并启用。构建保持只读，发布权限留在受保护阶段。目录只能有一个串行写入方。确认公开更新源可用后，才能宣布 Preview 上线。任何发现提示都必须可以关闭；通道不可用时不得显示更新徽标，也不能仅因用户检查更新就为其订阅。

## 准备候选版本

1. 选择上一个发布版本和精确的候选提交。使用干净的工作树，其中不得有本地配置、会话、凭据或无关改动。
2. 更新 `pyproject.toml` 中的 `project.version` 和 `nanobot/__init__.py` 中最终仅源码模式的回退值。私有的 WebUI/TUI 版本不是 Python 发布版本。
3. 检查生成的变更说明，补充必要的升级/回滚指引。功能版本可以加入亮点和致谢；普通补丁不需要完整公告。核对数量：提交数不等于已合并的 PR 数。安全披露另行协调。
4. 针对最终提交运行 Python、WebUI 和 TUI CI 检查，不要使用较早的 PR head。评审安装、配置、会话和 API 变更。
5. 在干净的输出目录中运行 `uv build --out-dir <artifact-directory>`，不要设置 `NANOBOT_SKIP_WEBUI_BUILD`。构建钩子会将 WebUI 打包进 sdist 和 wheel；wheel 由 sdist 构建。
6. 对中间产物运行 `twine check`，检查内容并记录 SHA-256 哈希。在源码检出之外的隔离环境中测试安装，并使用一次性的配置和会话测试从上一 Stable 版本升级，绝不能使用维护者正在使用的工作区。
7. 功能版本应按照 `Re-bin/nanobot-web` 中的 `MAINTAINING.md` 准备版本文档。补丁只更新受影响的说明和译文；现有文档仍然适用时，不要求重新编写完整手册或宣传页面。保留现有 URL 和历史版本，并验证受影响的网站路由。适用时固定源码引用，绝不能宣传尚未发布的软件包。Preview 说明涵盖开发变更，无需每天创建新版文档。
8. 检查固定版本 Bun/OpenTUI 的许可证、对应源码和重新链接说明。确认上游修订可用，并检查运行时、锁文件和许可声明是否一致。取得维护者对[源码提供承诺](../tui/SOURCE_OFFER.md)完整期限的承诺，至少为发布后三年；测试不能代替这项承诺。
9. 使用发布脚本构建全部五个原生目标，并在打包前对 macOS 进行 ad-hoc 签名。
   - 验证归档校验和、清单、架构、许可证/声明和内嵌源码，包括 `tui/scripts/targets.ts` 与 `tui/scripts/target-bindings.ts` 的字节是否与源码检出一致。运行受支持的原生冒烟测试，记录覆盖范围和精确 head 的 CI 证据，并标明仅交叉编译的目标。
   - 按下文构建平台 wheel，使用 `twine check` 检查，验证 RECORD、标签和权限，并在全部五个平台测试 pip 的选择结果。
   - 使用空缓存和 `NANOBOT_TUI_NO_DOWNLOAD=1` 测试首次启动；可执行文件必须来自 `site-packages/nanobot/tui/bin/`，不能来自源码检出或缓存。
10. 只有当前检查和评审全部通过后，才能合并发布准备改动。将合并后的源码树与已测试的候选版本比较；如有变化，重新构建和检查。核对更新日志和文档链接，然后为通过验证的提交打标签，不要选择后来未经检查的 `main` 顶端。

### 受保护更新器的交接兼容性

支持重启的软件包包含 `nanobot/update_contract.json`：schema 版本为 1，`handoff_protocols` 列出正整数协议 ID。协议 1 的请求、受保护启动和激活要求定义在[更新协议](./update-protocol.md)中，与软件包和 WebUI 版本无关。

宣传支持协议 1 前，针对打包后的代码运行准备、冻结 worker 和真实网关重启测试；确认 sdist 和 wheel 中都有该声明。只有完整支持某项约定的全部内容时才保留其 ID；请求、启动或激活行为不兼容时，需要使用新的 ID。旧更新器只有在协议有交集时才能安装未来的软件包。声明缺失、格式错误或不重叠时，必须在关闭前拒绝，并要求安装所有者使用外部更新途径。

### 旧版历史记录和代理的兼容窗口

在整个 **0.4.x** 期间保留旧版模型 JSONL、WebUI JSONL / segments 和按功能划分的代理迁移。在下一个次版本之前移除它们，会导致旧安装无法通过 0.4.x 补丁版本完成迁移。

**TODO(0.5.0):** 移除旧版导入器、代理转换、启动钩子和迁移专用测试；更新升级夹具和当前文档，使其只涉及 SQLite 历史记录以及 `network.proxy` / `useProxy`。保留 SQLite schema 升级和当前格式的行为。OpenAI API 选择器有单独的兼容窗口，见下文。

### OpenAI API 选择器兼容窗口

v0.4.0 and v0.4.1 included automatic conversion of `providers.openai.apiType` / `api_type`. The first release without conversion must note in its release notes that users can save old configuration with v0.4.1 and must update launcher environment variables separately.

### 无发布标签的 TUI 预检

使用 Bun 1.3.13 和干净的源码检出；不需要标签或 GitHub Release。准备、编译和声明生成使用同一个 `<target>`。由于准备过程会更新共享的 `node_modules`，各目标必须依次运行：

```bash
cd tui
bun install --frozen-lockfile
bun scripts/prepare-target.ts <target>
bun run build -- <target>
# For darwin-* only: ad-hoc sign dist/nanobot-tui-<target> before the next steps.
bun scripts/release-notices.ts <target>
python3 scripts/package-release.py <target>
```

对 `darwin-arm64`、`darwin-x64`、`linux-arm64`、`linux-x64` 和 `win32-x64` 分别重复上述步骤。在 macOS 上运行 `codesign --force --sign - <binary>` 并验证签名；发布工作流在 Linux 上使用固定版本的 `rcodesign`。将十个 `.zip`/`.zip.sha256` 文件与 Python 中间产物和来源清单一同放在候选版本专用目录中，上传选择中不得包含配置、备份和测试环境。

候选构建会打包压缩后的 JavaScript 和独立 Bun 运行时，并禁用额外的预编译字节码缓存。Linux 目标包含 GNU 和 musl 两种原生变体，x64 保留 Bun 的 baseline CPU 变体。Windows/Linux x64 构建执行 135 MiB 的可执行文件预算；其他目标在完成测量前保留现有的 150 MiB 上限。`dist/<executable>.size.json` 报告标识编译完成、尚未签名的可执行文件及预期的原生软件包；最终归档清单标识签名后的发布字节。

发布前，在每个受支持目标上测量 TUI 首屏和 Mermaid 渲染延迟，包括首次渲染和热渲染。验证取消和 worker 重启。保留延迟分布，分别记录可执行文件、ZIP 和最终平台 wheel 的大小。交叉编译和启动探测不能替代原生交互验收。

原生验收针对精确的输入 wheel 运行 `tui/scripts/native_smoke.py`。它复用 PTY/ConPTY 输入、调整大小和终端恢复检查，记录五次首帧样本以及使用系统字体发现的冷/热 Mermaid 样本，然后终止一次已提交的渲染并验证新的 worker。其小型报告包含 wheel/可执行文件标识和大小；ZIP 大小从该候选版本的清单中获取。这是与不可用夹具网关进行的自动化原生终端交互，不是连接模型的 E2E，也不是受控的操作系统冷缓存计时。`DiagramJobs` 集成测试另行覆盖 UI 侧取消和后续任务完成。

原生渲染夹具包含中文 C4 标签和生成的拉丁标点。Linux 验收会安装受支持的 `fonts-noto-cjk` 系统依赖；macOS 和 Windows 使用系统已安装的字体。这种设置不会把字体打包进 wheel，也不能证明没有合适字体的主机可以渲染中文。保持字形检查启用，并为没有可用字体的用户保留源码视图回退。

### 构建 PyPI 平台 wheel

完成 `uv build` 和原生归档检查后，在精确的发布源码检出中运行：

```bash
uv run python -m scripts.build_tui_wheels \
  --wheel <intermediates>/nanobot_ai-X.Y.Z-py3-none-any.whl \
  --tui-dir <verified-tui-archives> \
  --out-dir <final-wheels>
```

脚本会依据源码检出检查 Python 版本/RECORD，以及原生校验和、架构、声明和源码，然后写出五个捆绑 wheel，并重新生成 RECORD。它拒绝覆盖已有候选文件；`--target <target>` 用于选择单个平台。最低标签为 macOS 13、manylinux glibc 2.17 和 Windows x64；x64 使用 Bun 的 baseline SSE4.2 运行时，而非 AVX2。升级 Bun 或 OpenTUI 时，重新检查二者的要求。不提供 musl 或 Windows ARM64 wheel；标签不能替代原生执行验证。

PyPI 接收**五个平台 wheel 和一个 sdist**，不接收中间的通用 wheel 或原生 ZIP。保留同一构建生成的平台无关 sdist，供其他平台上的 Python/classic 和 WebUI 使用。记录源码提交、版本、文件、哈希、检查和剩余门槛；源码一旦变化，就必须重新构建和检查。

正式版本还需要为 `stable` 提供 `release-manifest.json`。在干净的正式版本源码检出中，按下文运行 `scripts.release_candidate assemble`，将最终提交用作 `--source-commit`，不进行 Preview 准备。GitHub 接收相同的 wheel/sdist、十个归档/校验和文件及清单；PyPI 只接收 wheel/sdist。推进目录前，验证最终源码和公开字节。只含 TUI 的 GitHub Release 无法向 Preview 订阅者交付正式版本。

要在全部五个原生目标上对同一构建进行候选验收，请在精确的正式版本 ref 上运行 **Verify Release Candidate**，设置 `channel=stable`、`next_stable` 等于声明的版本，并设置 `retain_installable=true`。Stable 准备会复制精确提交，不修改版本声明，也不创建 Preview 提交；版本不匹配或 Stable 标签已存在时会被拒绝。默认的 `channel=preview` 保留只修改版本的 Preview 准备方式。两种模式均为只读准备，都要求 **Candidate required**，并将通过验收的文件保留一天；两者都不会发布标签、Release、PyPI 软件包或更新源。在过期前下载并独立保存通过验收的交接包，然后针对同一清单完成剩余的交互和真实历史所有者验收。

## 发布正式软件包

由一位维护者发布 GitHub Release。GitHub Actions 验证通过验收的分发包，并通过可信发布上传到 PyPI。发布可安装的软件包并不会启用应用管理的更新。

1. 在 `main` 上完成候选验收。保留原始文件及其清单，并使用通过验收的提交创建 `vX.Y.Z`。绝不能移动或复用已发布的标签。
2. 创建草稿，附上五个平台 wheel、sdist、五个 TUI 归档、对应的校验和及原始 `release-manifest.json`。使用通过验收的整套工件，不得在同一发布标识下重新构建文件。如果使用 **Publish Terminal UI** 重新构建，必须组装新的匹配 wheel 和清单，并在发布前重新完成候选验收。
3. 点击 **Publish release**。正式版本发布会启动 **Validate published release for PyPI**，随后在 `main` 上启动 **Publish accepted release to PyPI**。发布器要求成功的 **Verify Release Candidate** 运行提供匹配的 CI 清单，并且同一次尝试的 **Candidate required** 任务成功。它检查标签、所有公开工件和软件包索引，然后只上传五个 wheel 和 sdist。预发布或仅推送标签不会发布 Python 软件包。
4. 验证从公开 PyPI 全新安装、捆绑 WebUI 和无需下载的 TUI 启动。添加注明日期的[发布归档](./release-archive.md)条目并记录发布结果。只有 GitHub 和 PyPI 资源均已公开并通过验证，才能合并 wiki PR；检查受影响的本地化路由。更新目录变更是独立操作，需要下文所述的签名。

### 配置可信发布

保护 `main` 和发布工作流。在 PyPI 项目 `nanobot-ai` 的 **Publishing** 设置中，绑定 `HKUDS/nanobot`、`publish-pypi.yml` 和 `pypi` 环境。该 GitHub 环境只允许从 `main` 部署；一键发布路径不要求环境评审者。上线过程中，先使用单独配置的 TestPyPI 可信发布者验证发布路径（相同仓库和工作流，环境为 `testpypi`），再取消生产环境的评审者要求。生产环境不使用长期令牌。独立的 `release-approval` 环境继续保留独立评审。

演练或恢复发布时，在 `main` 上使用 **Run workflow**，填入已发布版本和目标 `testpypi` 或 `pypi`。如果人工发布 GitHub Release 未触发上传，使用同一个手动工作流入口。创建新的运行，不要使用 **Re-run jobs**：新运行会检查索引，只上传缺失的文件。如果原始 CI 报告已过期，自动发布会停止。保留通过验收的文件字节和验证记录；必要时使用现有手动上传途径上传同一批已验证文件。绝不能替换已发布的文件或移动其标签。

### 发布批准证明

Stable 和 Preview 的自动安装都要求精确的 GitHub Release 中，在清单旁提供 `release-manifest.json.sigstore.json`。客户端验证 Sigstore 的生产证书链和透明度证明，只接受 `HKUDS/nanobot` 在 `main` 上手动运行的 `release-approve.yml`。签名清单必须与版本、wheel 大小/摘要匹配；对于 Preview，还必须匹配目录提交/清单绑定。验证在下载前和关闭前执行，没有未签名回退。未签名的历史发布仍可被发现，但不能自动安装；现有安装继续运行。

设置仓库变量 `NANOBOT_RELEASE_SIGNING_ENABLED=true` 前，管理员必须保护 `main` 和 `release-approval` 环境：要求独立评审、禁止自我评审、只允许从 `main` 部署，并限制绕过保护及更改工作流/签名要求的权限。未设置该变量时，签名保持禁用。不需要长期签名密钥或 Apple Developer ID。

候选测试和依赖评审完成后，从保留的验收证据中取得版本、清单 SHA-256、源码 SHA 和候选 SHA，不要从公开下载中取得。通过单独授权的发布交接上传拟定的清单，然后使用这些值从 `main` 手动运行 **Approve Exact Release**，并取得环境评审。哈希锁定任务检查并签署精确的清单，不安装候选代码，也不写入 Release、PyPI 或目录。附加其证明需要单独授权；推进任一通道前验证公开证明，并在三天 CI 保留期届满前，将其与候选证据一起保存。

只有在经过评审的依赖更新中，才重新生成签名锁：

```bash
uv pip compile .github/release-signing-requirements.in --python-version 3.11 \
  --python-platform x86_64-manylinux_2_17 --generate-hashes --no-header \
  --output-file .github/release-signing-requirements.txt
```

检查解析出的依赖；哈希不等于恶意代码审查。首次发布前，证明受支持的真实旧更新器可以安装签名候选版本，且该候选版本可以安装下一个签名版本。私有的打包恢复夹具代替的是发布权限，不是签名证据。

该证明验证的是发布批准，不证明构建可复现或代码无害。已授权工作流或评审者遭到入侵、恶意源码通过评审、运行时依赖遭到入侵，这些风险依然存在。签名锁不锁定运行时依赖解析，后者仍遵循安装所有者的策略。经过评审的运行时依赖清单、公开的签名升级连续性，以及经过验证的仓库保护设置，仍是安全发布的门槛；缺少这些条件时，不得宣称提供全面的供应链保护。首个安装器和校验和来源也需要可信的分发渠道。

只有签名、匹配的公开发布文件和适用的公开更新检查均通过验证，维护者才能推进 `stable` 或 `preview`。保留另一通道和撤回记录，并检查两种订阅的更新发现。没有此证明的软件包仍可手动安装，但不能通过应用管理的更新安装。

## 如果某个门槛未通过

TUI 工件或验证缺失时，不得发布到 PyPI。公开文档的 `latest` 保持指向可用的 Stable 版本。修正已发布软件包时必须使用新版本，绝不能把替换代码藏在已有标签后。降级已迁移的安装前，停止所有旧进程，并遵循文档中的会话回滚流程。

## 安装器身份检查

无论 PATH 上是否有可选管理器，全新安装基础软件包都使用官方安装器的专用 venv。`nanobot-install.json` 在 venv/下载工作前写入，因此首次安装失败也能保留其所有者；它不是就绪证明。现有 venv/uv/pipx 安装不会被接管或移动。回执记录 extras 以供更新规划，并让 `webui` 选择持久后台启动；未记录的安装保留其原有生命周期。发布说明中应包含这一区别和实例专用的停止命令。

Linux/Windows 原生 CI 构建平台 wheel，并在 PATH 不含 Bun/Node/Git 的私有 home 中运行公开安装器，然后检查托管回执、已安装的 WebUI、原生 TUI 和网关。Python/WebUI 变更同样需要这些任务。CI 保留小型报告，不保留候选二进制文件。启动探测和离线安装器夹具不能替代打包升级/恢复或公开下载验收。

在 `uv` 和 `pipx` 可用时运行 `pytest tests/test_install_scripts.py`。它使用私有 home、离线夹具 wheel 和真实管理器，不启动网关，也不真实安装应用：Unix 上使用 `install.sh`，Windows PowerShell 中使用 `install.ps1`。CI 在应用依赖之外准备 pipx。验证以下所有者保留要求：

- 启动所选持久环境中的隔离 Python，不使用 `pipx run`、一次性工具或任意 PATH 可执行文件。已识别出的现有安装优先于新出现的管理器；识别出多个安装时，必须明确选择。普通的活动 venv 就是明确选择；uv/pipx 环境必须使用对应管理器。
- 保留 uv 约束。通用 pipx 升级可能丢失约束/选项；对于带这些设置、锁定工作流或未确认来源的 pipx 记录，安装器必须拒绝处理。所有者必须检查并保留这些内容。精确 wheel 可以替换 wheel 来源，但不能替换注册表约束/选项；pipx 升级必须保留请求的 extras，并拒绝缺少已记录 extras 的 wheel。
- 所选管理器失败后，不得转而使用另一个安装，即使首次安装也是如此。进入引导流程前，验证持久软件包的精确版本：固定版本/锁定模式下成功跳过，不等于安装了目标版本。重启后网关的身份需要单独检查。

针对固定版本的安装器验证精确交接选项，不要使用旧脚本：`install.sh --wheel <path> --sha256 <published-hash>` / `install.ps1 -Wheel <path> -Sha256 <published-hash>`，或 `install.sh --release <version> --manifest-sha256 <trusted-manifest-hash>` / `install.ps1 -Release <version> -ManifestSha256 <trusted-manifest-hash>`。Wheel 模式验证校验和、软件包和版本；Release 模式为目标 Python 选择五个原生标签之一，并在调用原所有者前验证大小、哈希和身份。不会安装前端工具链或额外的选择器软件包；管理器仍会检查 Python/依赖。哈希必须来自可信元数据，不能来自未知发布方。

发布下载使用官方 GitHub HTTPS 主机和配置的 HTTPS 代理；wheel 来源保留在其记录路径，包括 `~/.nanobot/downloads/releases` 或 `NANOBOT_RELEASE_CACHE`。失败的不完整下载会被删除。Wheel 模式的 `--dry-run` / `-DryRun` 只验证，不安装或设置；Release 模式的 dry-run 只检查清单，不创建 wheel、缓存、环境或设置，也不能证明 wheel 字节或依赖解析正确。

这些精确版本交接不会发现 Preview、更改订阅、停止网关或备份数据。必须先停止写入方并备份。检查普通 pip、带后缀/自定义安装和外部所有者的 extras 及已记录选项；请参阅[用户指南](./updating.md)和[更新协议](./update-protocol.md)。仅凭安装器夹具，不能证明实际维护、备份或重启的安全性。

### 打包升级和恢复验收

Linux/Windows 原生 CI 针对其 wheel 运行 `scripts/verify_packaged_update.py`；手动候选准备则在匹配的原生运行器上分别测试五个组装好的平台 wheel。针对候选版本的 macOS wheel 运行 macOS 验收：

```bash
<build-tools-python> -I scripts/verify_packaged_update.py \
  --wheel <exact-native-wheel> --commit <candidate-commit> \
  --output <new-private-acceptance-directory>
```

构建工具需要项目的 Python 依赖。探测程序创建一次性的 venv、账户、配置、工作区和环回网关，不使用维护者的数据、模型或凭据。它记录未改动的健康目标的 SHA-256 和候选提交；注入过故障的私有 wheel 绝不能上传。

在本地 macOS 桌面上，添加 `--dock`，在这些流程中同时验证真实的 AppKit 辅助程序。托管回执必须让 `nanobot webui --yes --no-open` 自动分离；每个适用的运行版本都必须有一个辅助程序，在升级/恢复和第二次启动后保留桌面意图，并在停止时移除辅助程序。不支持 Dock 的历史软件包只有在首次健康升级并明确启动 WebUI 后才会获得图标。这是进程/生命周期验收，不是 Dock 点击的视觉测试；不要在无头 CI 上启用，也不要宣称覆盖原生 Windows/Linux 图标。

必须覆盖正常升级、连续升级、跨过 Preview、导入/启动失败、关闭后备份失败、worker 终止、部分元数据丢失，以及程序恢复重命名之间的中断。每条成功流程都必须在新网关运行中显示预期版本，保留经过验证的备份，支持经过身份验证的历史读取、继续写入和第二次正常启动。每个恢复后的服务随后都必须使用相同账户/会话更新到健康目标。故障影响第一个任务；备份失败后必须能通过自动重试，而单独确认的下一次更新可以生成健康备份。插桩的中间 Preview 第二跳安装未改动的最终 wheel。

分别比较配置、保留的旧版源字节、权威 SQLite 记录、WebUI 对话记录和待处理的 `delivery/outbox.sqlite3` 记录。覆盖仅权威、仅显示和混合历史记录，不得为仅显示的会话虚构权威记录；SQLite 检查点字节变化本身不代表数据丢失。在旧/新受保护存储清单之间保留协议 1 的暂停网关回归测试：激活前，不得在旧授权范围之外初始化、迁移或写入 outbox。范围和租约要求请参阅[更新协议](./update-protocol.md)。

默认基线标注为目标派生夹具，不是历史兼容性证据。发布验收时，添加 `--baseline-wheel <previously-accepted-wheel>`，并使用受支持的 Stable 和较早的 Preview 软件包重复测试，包括跨过 Preview 的情况。基线更新源传输经过插桩；健康目标保持不变。加入保留的 SQLite 之前版本更新器，分别检查 JSONL 迁移的权威消息正文和显示历史。如果恢复还原的是 JSONL 代码，将 SQLite 检查标为不适用，而不是通过。没有此更新器的软件包需要单独记录安装所有者的引导验收。

在同一个已有会话的一次性账户中，通过每种适用的所有者执行连续更新：官方安装器、uv tool、pipx、普通 pip venv 和源码。检查前后记录的约束/选项，执行提供的命令，验证重启后的网关，追加一条消息并再次启动。策略拒绝必须保持安装不变；由所有者评审的自定义更新不能证明自动恢复能力。容器替换需要真实引擎和保留的数据卷，不能使用模拟标记。

对于安装器所有的后台恢复，验证一次精确目标修复，然后只在激活前且受保护数据未变时恢复原程序，包括在无需浏览器或终端干预的情况下验证重启/健康状态。数据变化、激活、存活的所有者或安装所有权变化，都必须阻止不安全的回滚。其他所有者保留各自的修复途径。绝不能删除 jobs/fences 或自动恢复旧配置/聊天数据。执行原生 Windows 锁定/重命名测试。supervisor 和 worker 同时丢失、存储永久损坏以及不兼容的数据写入无法自动恢复；在发布说明和运维指南中保留这些限制。

私有证据在评审前保持私有；其中可能包含程序世代/备份，不属于上传集。CI 保留小型报告，包括后续失败前已完成的用例；只有所有选定流程都通过，整体结果才算成功。部分报告或超时不算整体通过。将实际原生 OS/CPU 覆盖范围与安装器/启动夹具分开记录：Linux x64、Windows x64 和 macOS arm64 不能证明覆盖 macOS x64 或 Linux arm64。受控发布传输和故障注入不能证明公开下载或交互 UI；宣称具备这些能力前，必须完成相应门槛。

## Stable 和 Preview 订阅

“设置 → 关于”和 CLI 的说明请参阅[更新 nanobot](./updating.md)。发布验收必须保留主动订阅同意：即使是开发构建，Preview 也默认关闭；切换只保存/检查更新源，不代表批准安装。必须单独确认精确版本，并显示 Preview 风险警告。共享环境/账户的配置方案共享其订阅；其他安装/账户不共享。替换环境后可能需要重新选择；浏览器存储不是权威依据。

| 当前安装 / 请求通道 | 必需的验收结果 |
| --- | --- |
| Stable → Preview，存在更新的已验证 Preview | 提供该精确 Preview；仅在确认后安装。 |
| Preview → Preview | 只提供更高的已验证版本，包括正式版本。 |
| Preview → Stable，Stable 更新 | 提供正式版本；保持选择 Stable。 |
| Preview → Stable，Stable 更旧 | 保存 Stable 选择并等待更新的正式版本；继续运行 Preview。 |
| Preview 订阅者安装正式版本 | 为下一个开发周期保持选择 Preview。 |
| 当前 Preview 已撤回 | 即使没有安全的新替代版本也要警告；绝不能静默降级。 |
| 更新源离线、不完整、缺失或不兼容 | 返回非成功；保留订阅并允许重新检查。 |

Stable 检查/规划需要完整的 PyPI wheel/sdist。Preview 使用 `release-index/catalogue.json` 和精确的 GitHub 清单，绝不使用 PyPI `--pre` 或任意分支。依据网关解释器/平台验证目标，而不是浏览器。也要为 Preview 订阅者索引正式版本；验证所有引用的公开资产后才能推进。

版本、标签和清单哈希均不可变。递增 generation；绝不能回滚或清除已发布指针、删除撤回记录或复用有问题的版本。要停止提供某个版本，保留其指针并将其撤回。串行处理写入方，并依据最新目录验证 `ReleaseCatalogue.validate_successor(previous)`。客户端拒绝相对其最后接受目录的重写；该检查不是加密签名。

每次通道变化都必须使评审失效，包括 Stable → Preview → Stable；通过正常停止检查取消非活动的暂存工作，并保留诊断信息。活动准备、已提交关闭、已接纳安装或失败的变更会阻止切换，直到完成或恢复。拒绝过期标签页写入，响应丢失后重新读取，绝不能自动重试安装。详细事务规则见[更新协议](./update-protocol.md)。

发现不代表获得安装权限。只有官方托管 venv 和受支持的 POSIX uv 事务支持经过评审的停止/安装/重启流程；pipx、源码、容器、系统管理、不受支持的 uv/平台和桌面包仍由其所有者/分发方处理。没有软件包安装权限的远程浏览器可以读取状态，但不能更改订阅。对于无法表示的状态，旧客户端必须收到可操作的失败信息，绝不能错误地显示“已是最新”。宣布公开通道前，验证这些边界。

## 不发布的 Preview 预检

**Verify Release Candidate** 是只读准备，可手动请求，也可明确启用每日 Preview：不写入标签/Release、PyPI 或目录。它构建并检查五个平台 wheel、sdist 和符合许可的回退归档。交叉编译既不是原生执行，也不是发布批准。

默认将小型身份/清单/探测报告和安装指南保留**三天**，不包含可安装二进制文件。`retain_installable` 将这些经过检查的精确工件和一个自包含的浅层 Git 仓库保留**一天**（约 400 MB）。常规预检或计划任务应保持此存储例外关闭；合并前评估存储，并在工作流变更 PR 中明确说明。在过期前保存获批的交接包；重新构建已过期文件不能复用其验证结果。源码只包含候选/源码父提交历史，不包含配置或未跟踪文件。只有复制的工件通过验证后才写入 `handoff.json`；中断不算通过验收的交接。

组装后，工作流通过不可变工件 ID 传输全部五个与清单绑定的 wheel；每个匹配的原生运行器将其 wheel 和二进制依赖安装到独立 venv 中，安装 PATH 不含 Bun/Node/Git。隔离 Python 检查版本/提交/导入身份，通过已安装处理器检查包括懒加载块在内的所有 Vite 清单资产，并在禁用下载和空私有 HOME 的条件下检查捆绑 TUI 的 `--desktop-protocol`。使用 `--gateway` 时，它启动私有前台 CLI 网关，不配置 provider/MCP，禁用心跳并使用两个环回端口，然后检查就绪状态、受保护版本身份、HTML 和进程/监听器清理。Unix 关闭必须优雅完成；Windows 终止会明确标注，不能作为优雅关闭证据。不执行模型任务或外部通道。

在另一个受支持平台上，检查一次性的原生安装，不要更改其软件包：

```bash
<installed-venv-python> -I scripts/verify_installed_release.py \
  --version <candidate-version> --commit <candidate-commit> \
  --gateway --report <new-report-path>
```

使用候选提交，而不是其父提交。探测程序拒绝从源码检出导入和非隔离调用，并避开用户配置/凭据。只检查资产/TUI 时省略 `--gateway`。报告标识 OS/CPU/探测项，只记录成功结果且绝不覆盖；失败时清理子进程，并打印长度受限、已脱敏的日志末尾。这是软件包/启动证据，不是交互 UI、托管重启、迁移、公开下载或五平台 E2E 验收。

本地准备时，使用完整源码 SHA 和源码检出之外的新输出目录（base/序号仅为示例）：

```bash
python -m scripts.release_candidate prepare \
  --source-commit <full-commit-sha> --base 0.3.6 --sequence 42 \
  --output <new-candidate-directory>
```

准备过程不改动源码检出，并为相同输入确定性地创建只修改版本的提交。从该提交构建 Python 分发包和全部原生目标，然后在候选版本中运行：

```bash
python -m scripts.release_candidate assemble --source-commit <original-source-sha> \
  --wheel <intermediates>/nanobot_ai-0.3.6.dev42-py3-none-any.whl \
  --sdist <intermediates>/nanobot_ai-0.3.6.dev42.tar.gz \
  --tui-dir tui/dist --output <new-artifact-directory>
```

组装拒绝已有输出，只有在完整集合、版本/提交、WebUI、RECORD、原生/源码和权限检查全部通过后才生成清单。它在应用打包排除项后，将 sdist 源码/资源与候选版本的受跟踪文件比较，并将所有 wheel 的应用/WebUI 字节与 sdist 比较。仅有提交标记和 RECORD 不够；这些比较也不能证明前端/原生编译可复现。使用 `python -m scripts.release_candidate verify --source-commit <original-source-sha> --artifacts <artifact-directory>` 重新检查；它可以复用完全相同的清单，但绝不能替换不同字节。

从同一候选版本生成交接包：

```bash
python -m scripts.release_candidate instructions --source-commit <original-source-sha> \
  --artifacts <artifact-directory> --output <new-install-guide-path.md>
```

生成器重新检查工件/清单，并在不可变上传目录之外写入不覆盖已有文件的指南。当两个安装器都与受支持实现一致时，命令会使用保留的清单哈希获取精确候选版本的安装器，自动选择平台；较早候选版本保留手动命令，绝不使用可变的 `main` 或假定的参数。发布时必须让固定版本的安装器源码可访问。

指南记录版本、源码/候选提交、五个与哈希绑定的 wheel URL，以及 uv、pipx 和 pip venv 各自用于全新/已有基础软件包的命令。命令在调用管理器前使用标准库验证本地大小/SHA-256；对于测试过的 uv 命令，仅有 URL 哈希片段不够。保留管理器记录的 wheel 路径。支持自动入口后，手动命令移至高级部分；不引入新的清单 schema 或安装器可执行文件。

提供指南前，发布/验证精确的公开文件，并补充发布变更、原生覆盖范围、迁移/恢复说明以及停止/备份前提。检查 extras 和锁定/带后缀/外部所有者选项；独立验证已安装版本和重启后的网关。管理器夹具不能证明重启/迁移，指南既不是发布批准，也不是通道选择。

候选克隆借用源 Git 对象；在保留到获批的永久 ref 前，保存其源仓库。部分组装没有通过验收的清单，不得上传。源码提供承诺、原生启动、全新安装、升级/恢复和发布门槛仍然适用。缺失的公开资产必须从原始已验证文件恢复，绝不能用重新构建的文件覆盖。

在临时构建存储之外保留精确的本地交接包：

```bash
python -m scripts.release_candidate retain --source-commit <original-source-sha> \
  --artifacts <verified-artifact-directory> --output <new-retained-directory>
```

只有 `artifacts/` 是上传集；`source.git/`、`install.md` 和 `handoff.json` 是维护证据，不是 PyPI 软件包。使用 `git clone --no-local <retained>/source.git <new-checkout>` 恢复，依据 `handoff.json` 检查 HEAD，并在单独批准的发布前对保留工件重新运行 `release_candidate verify`。将原生报告和发布说明与它们一起保存；保留本身不会增加原生、迁移、交互 UI 或公开下载证据。
