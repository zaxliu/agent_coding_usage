# llm-usage-horizon

本地优先的 LLM 编码工具用量采集器，支持聚合以下来源并输出到终端、CSV 或飞书多维表格：

- Claude Code
- Codex
- Cursor
- GitHub Copilot CLI
- GitHub Copilot VS Code Chat
- OpenCode
- 通过 SSH 拉取的远端日志

设计目标：

- 默认只在本地读取原始日志
- 上传时只发送聚合后的白名单字段
- 支持桌面端统一汇总多台服务器数据

## 功能概览

- `llm-usage collect`：采集并汇总本地 + 已选远端数据，输出终端表格和 `reports/usage_report.csv`
- `llm-usage sync`：在 `collect` 基础上，将聚合结果同步到飞书多维表格
- `llm-usage export-bundle`：采集并生成离线 bundle，拷回联网机器后可用 `sync --from-bundle` 上传
- `llm-usage doctor`：检查配置和各采集器可用性
- `llm-usage init`：生成 `.env`、`.env.example` 和 `reports/`
- `llm-usage config`：打开交互式菜单编辑器，编辑当前运行时 `.env`

## 快速开始

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

llm-usage init
# 用菜单编辑器配置当前运行时 .env，至少补全 HASH_SALT
llm-usage config
# ORG_USERNAME 缺失时，命令行运行会提示输入并自动写回 .env

llm-usage doctor
llm-usage collect --ui auto
```

如果你要同步到飞书，再补全飞书相关环境变量后执行：

```bash
llm-usage sync --ui auto
```

## Node 版本

仓库中的 `node/` 目录提供一个 Node.js CLI 实现。

当前 Node 版本特性：

- 本地采集、聚合、报表输出、飞书同步均可直接由 Node 执行
- 运行本地命令时不再依赖 Python collector bridge
- 远端 SSH 采集暂未在 Node 版本中实现；检测到远端配置时会提示并忽略

如果你要验证 Node 版本：

```bash
cd node
node --test
node ./bin/llm-usage-node.js doctor
node ./bin/llm-usage-node.js collect --ui none
```

## 最小配置

`.env` 中至少建议配置：

```env
ORG_USERNAME=san.zhang
HASH_SALT=change-me
TIMEZONE=Asia/Shanghai
LOOKBACK_DAYS=30
```

说明：

- `ORG_USERNAME`：必填，用于生成稳定的匿名身份哈希
- `HASH_SALT`：必填，决定匿名字段的稳定性与不可逆性
- `TIMEZONE`：聚合时按该时区落到 `date_local`
- `LOOKBACK_DAYS`：采集窗口，默认 `30`

如果缺少 `ORG_USERNAME`，交互终端下运行时会提示输入并写回 `.env`。

远端不建议手工编辑 `REMOTE_*` 配置。推荐直接运行 `llm-usage config`，在菜单里编辑当前运行时 `.env`，包括远端 SSH 主机、用户、端口和路径列表。保存前的修改都停留在草稿里，不会直接写盘。

如果你是从旧版仓库迁移，旧配置通常还在仓库根目录的 `.env` 和 `reports/runtime_state.json`。这类配置现在需要一次性迁移到新的运行时路径，推荐在旧仓库根目录执行：

```bash
llm-usage import-config --from /path/to/old/repo
```

可选参数：

- `--dry-run`：先预览会复制哪些文件
- `--force`：覆盖新位置里已存在的目标文件

如果你就在旧仓库根目录执行，也可以省略 `--from`。迁移完成后，后续直接使用 `llm-usage doctor`、`llm-usage whoami`、`llm-usage collect` 或 `llm-usage sync` 即可。

## 命令说明

先查看顶层帮助：

```bash
llm-usage --help
```

顶层 help 会列出所有子命令，并附带常用示例：

- `llm-usage doctor`
- `llm-usage whoami`
- `llm-usage config`
- `llm-usage collect --ui auto`
- `llm-usage sync --ui cli`
- `llm-usage export-bundle --output /tmp/offline.zip`
- `llm-usage import-config --from /path/to/legacy/repo`

### `llm-usage init`

初始化：

- `.env.example`
- `.env`
- `reports/`
- 默认写入 `LOOKBACK_DAYS=30`

### `llm-usage doctor`

检查：

- `ORG_USERNAME`、`HASH_SALT`、`TIMEZONE`
- 本地采集器是否能找到对应数据源
- `.env` 中配置的远端采集器是否可探测

查看帮助：

```bash
llm-usage doctor --help
```

常用参数：

- `--lookback-days N`：覆盖 `.env` 中的 `LOOKBACK_DAYS`

### `llm-usage config`

推荐的配置编辑入口：

- 打开当前运行时 `.env` 的交互式菜单编辑器
- 支持分组编辑基础配置、飞书配置、Cursor 配置、远端配置和原始环境变量
- 修改先保存在草稿里，确认 `Save` 后才写回文件

查看帮助：

```bash
llm-usage config --help
```

### `llm-usage whoami`

输出：

- 当前 `ORG_USERNAME`
- 当前 `user_hash`
- `source_host_hash(local)`
- 每个已配置远端各自的 `source_host_hash(<alias>)`

查看帮助：

```bash
llm-usage whoami --help
```

### `llm-usage collect`

行为：

- 读取本地日志
- 按需选择远端 SSH 来源
- 输出终端汇总表
- 写入 `reports/usage_report.csv`

说明：终端表格按 `日期 + 工具 + 模型` 合并展示，不区分单个 session 或来源机器。CSV 仍保留原始聚合结果，不会因为终端显示合并而改变存储内容。

查看帮助：

```bash
llm-usage collect --help
```

常用参数：

- `--lookback-days N`：覆盖 `.env` 中的 `LOOKBACK_DAYS`
- `--ui auto|tui|cli|none`：远端选择界面。`auto` 自动选最合适的交互方式，`tui` 强制终端选择器，`cli` 使用逐项提示，`none` 跳过远端选择
- `--cursor-login-mode`：Cursor 登录模式。默认 `auto`；Windows Chromium 浏览器下会自动切到 `managed-profile`；也可显式选择 `manual`
- `--cursor-login-timeout-sec`：Cursor 浏览器登录等待时间，默认 `600`
- `--cursor-login-browser`：指定 Cursor 登录捕获所用浏览器；默认 `default`
- `--cursor-login-user-data-dir`：`managed-profile` 模式下的专用浏览器 profile 目录；留空时使用工具默认的受控目录

示例：

```bash
llm-usage collect --ui auto
llm-usage collect --ui cli --cursor-login-browser safari
```

### `llm-usage sync`

与 `collect` 相同，但会额外：

- 自动获取飞书访问令牌
- 在目标多维表格中按 `row_key` 执行插入或更新

说明：`sync` 的终端显示也按 `日期 + 工具 + 模型` 合并，但上传到飞书的记录内容和粒度保持不变。

查看帮助：

```bash
llm-usage sync --help
```

常用参数：

- `--lookback-days N`：覆盖 `.env` 中的 `LOOKBACK_DAYS`
- `--ui auto|tui|cli|none`：远端选择界面。`auto` 自动选最合适的交互方式，`tui` 强制终端选择器，`cli` 使用逐项提示，`none` 跳过远端选择
- `--cursor-login-mode`：Cursor 登录模式。默认 `auto`；Windows Chromium 浏览器下会自动切到 `managed-profile`；也可显式选择 `manual`
- `--cursor-login-timeout-sec`：Cursor 浏览器登录等待时间，默认 `600`
- `--cursor-login-browser`：指定 Cursor 登录捕获所用浏览器；默认 `default`
- `--cursor-login-user-data-dir`：`managed-profile` 模式下的专用浏览器 profile 目录；留空时使用工具默认的受控目录

示例：

```bash
llm-usage sync --ui auto
llm-usage sync --ui cli --cursor-login-browser chrome
llm-usage sync --from-bundle ~/Downloads/llm-usage-devbox-a.zip --dry-run
```

离线导入参数：

- `--from-bundle PATH`：从离线 bundle 读取聚合结果，跳过本地/远端在线采集
- `--dry-run`：只校验 bundle 并输出终端汇总，不上传到飞书

注意：`--from-bundle` 模式下不应再同时传 `--ui`、`--lookback-days` 或 Cursor 登录相关参数。

### `llm-usage export-bundle`

行为：

- 读取本地日志并按当前配置聚合
- 生成单个 zip bundle，默认写到 `reports/llm-usage-bundle-<timestamp>.zip`
- bundle 内固定包含 `manifest.json` 和 `rows.jsonl`
- 默认还会附带 `usage_report.csv`，方便人工检查

查看帮助：

```bash
llm-usage export-bundle --help
```

常用参数：

- `--output PATH`：指定输出 zip 路径
- `--lookback-days N`：覆盖 `.env` 中的 `LOOKBACK_DAYS`
- `--ui auto|tui|cli|none`：与 `collect` 相同，用于远端选择
- `--no-csv`：bundle 中不附带 `usage_report.csv`

示例：

```bash
llm-usage export-bundle
llm-usage export-bundle --output ~/llm-usage-devbox-a.zip
llm-usage export-bundle --no-csv
```

## 离线 Bundle 工作流

适用场景：

- 开发机无法联网
- 只能通过远程桌面进入
- 但可以把文件从远端拷回本地联网机器

推荐流程：

1. 在远端开发机执行：

```bash
llm-usage export-bundle --output ~/llm-usage-devbox-a.zip
```

2. 把 `~/llm-usage-devbox-a.zip` 拷回本地联网机器

3. 在本地先校验：

```bash
llm-usage sync --from-bundle ~/Downloads/llm-usage-devbox-a.zip --dry-run
```

4. 校验无误后正式上传：

```bash
llm-usage sync --from-bundle ~/Downloads/llm-usage-devbox-a.zip
```

bundle 只包含聚合后的白名单字段，不包含提示词原文、响应原文、本地路径、命令内容或原始主机名。

读取端除了支持默认的 `.zip` bundle，也兼容已经解压出来、且目录中包含 `manifest.json` 与 `rows.jsonl` 的 bundle 目录。

## 输出与隐私

上传到飞书的字段是固定白名单：

- `date_local`
- `user_hash`
- `source_host_hash`
- `tool`
- `model`
- `input_tokens_sum`
- `cache_tokens_sum`
- `output_tokens_sum`
- `row_key`
- `updated_at`

不会上传：

- 提示词 / 响应原文
- 会话 ID
- 本地路径
- 命令内容
- 原始主机名或 SSH 连接信息

其中：

- `user_hash` 基于 `ORG_USERNAME + HASH_SALT`
- `source_host_hash` 基于 `ORG_USERNAME + source_label + HASH_SALT`

如需查看当前机器和已配置远端对应的哈希值，可直接运行：

```bash
llm-usage whoami
```

这意味着同一台共享服务器上的不同用户不会发生来源冲突。

## 支持的数据源

### 本地来源

默认支持：

- Claude Code
- Codex
- Cursor
- Copilot CLI
- Copilot VS Code Chat
- OpenCode

如默认路径不足，可在 `.env` 中覆盖：

- `CLAUDE_LOG_PATHS`
- `CODEX_LOG_PATHS`
- `COPILOT_CLI_LOG_PATHS`
- `COPILOT_VSCODE_SESSION_PATHS`
- `CURSOR_LOG_PATHS`

这些值使用逗号分隔的 glob 匹配模式。

### OpenCode

OpenCode 采集器从 SQLite 读取 token 使用量：

- 默认路径：`~/.local/share/opencode/opencode.db`
- 可通过 `OPENCODE_DB_PATH` 覆盖

### Cursor 网页仪表盘（可选）

如果本地 Cursor 日志不可用，或当前 lookback 内没有数据，`collect` / `sync` 会尝试使用 Cursor 网页端数据。

相关环境变量：

- `CURSOR_WEB_SESSION_TOKEN`
- `CURSOR_WEB_WORKOS_ID`
- `CURSOR_DASHBOARD_BASE_URL`，默认 `https://cursor.com`
- `CURSOR_DASHBOARD_TEAM_ID`，默认 `0`
- `CURSOR_DASHBOARD_PAGE_SIZE`，默认 `300`
- `CURSOR_DASHBOARD_TIMEOUT_SEC`，默认 `15`

行为说明：

- 若 `CURSOR_WEB_SESSION_TOKEN` 已配置，优先使用网页仪表盘接口
- 若 token 失效，会清空旧 token，并引导重新登录
- 若 token 为空且本地日志不可用，会尝试打开登录页并刷新 `.env` 中的网页登录凭证

Windows 下使用 `default` / `chrome` / `chromium` / `edge` / `msedge` 时，默认不会扫描系统浏览器默认 profile 的 cookie。
程序会优先使用受控浏览器 profile 登录流程；若失败，再回退到手动粘贴 `WorkosCursorSessionToken`。

Windows 受控 profile 登录流程：

1. 运行 `llm-usage collect` 或 `llm-usage sync`
2. 若是 Windows Chromium 浏览器，程序会打开一个由工具管理的专用浏览器 profile
3. 在该窗口中完成 `https://cursor.com/dashboard/usage` 登录
4. 程序会轮询该 profile 中的 cookie，并自动写入 `.env`
5. 如果自动捕获失败，程序才会回退到手动粘贴 `WorkosCursorSessionToken`

## 远端 SSH 采集

远端采集由桌面机发起，通过 SSH 拉取日志后在本地统一聚合。

当前远端支持：

- Claude Code
- Codex
- Copilot CLI
- Copilot VS Code Chat

不支持远端 Cursor，本项目中的 Cursor 仍以桌面端本地 / 网页数据为主。

推荐使用命令行交互添加远端，而不是手工编辑 `.env`：

```bash
llm-usage collect --ui auto
```

典型流程：

1. 首次运行时选择 `+` 新增一个临时远端
2. 按提示输入 `SSH 主机`、`SSH 用户`、`SSH 端口`
3. 程序会先执行 SSH 连通性检查
4. 检查通过后继续采集
5. 退出前会询问是否将该远端保存到 `.env`

如果远端机器在堡垒机 / 跳板机后面，当前也支持将目标机器信息直接嵌入 SSH 登录串，例如：

```bash
ssh username@username@host_server_ip@host_jumpserver_ip
```

这种场景下，命令行交互录入时可按下面填写：

- `SSH 主机`：`username@host_server_ip@host_jumpserver_ip`
- `SSH 用户`：`username`
- `SSH 端口`：按实际堡垒机端口填写

这样程序最终拼接出的 SSH 目标会是：

```text
username@username@host_server_ip@host_jumpserver_ip
```

只要你的堡垒机环境本身支持这种格式，`llm-usage` 当前也可以正常连通并采集。

只有在需要批量预置配置、或做非交互部署时，才建议手工维护 `REMOTE_*`。

运行时行为：

- `--ui auto` 优先使用轻量 TUI，失败时回落到 CLI
- `--ui none` 会禁用所有远端，不会沿用 `runtime_state.json` 里上次选中的静态远端
- 上次选择的静态远端会保存到当前运行时数据目录下的 `runtime_state.json`
- 可以在运行时临时添加远端
- 推荐通过运行时交互添加远端，临时远端只有确认后才会追加写入 `.env`
- 临时远端默认来源标签为 `ssh_user@ssh_host`
- 远端机器只要求有 `ssh` 和基础 `python3` / `python`

## 飞书多维表格同步

需要的环境变量：

- `FEISHU_APP_TOKEN`
- `FEISHU_TABLE_ID`，可选；为空时自动选择第一个表
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_BOT_TOKEN`，可选；若提供则直接作为 bearer token 使用

建议在目标表中创建以下字段：

- `date_local`
- `user_hash`
- `source_host_hash`
- `tool`
- `model`
- `input_tokens_sum`
- `cache_tokens_sum`
- `output_tokens_sum`
- `row_key`
- `updated_at`

注意：

- 飞书应用权限不等于多维表格协作权限
- 即使应用有写权限，如果表格本身只读，写入仍会失败
- 应确保应用或其运行身份对目标表保有编辑权限

## 开发

安装开发依赖：

```bash
pip install -e '.[dev]'
pytest
```

## 发布到 PyPI

先准备发布工具：

```bash
python -m pip install -U build twine
```

每次发布前都要先修改 [pyproject.toml](/Users/lewis/Documents/code/agent_coding_usage/pyproject.toml) 里的 `version`，避免重复上传同一版本。

构建并检查 PyPI 分发文件：

```bash
./scripts/build_pypi_release.sh
```

这个脚本会把产物单独输出到 `dist/pypi/`，不会碰现有 `dist/` 目录中的业务压缩包。

如果你想自定义输出目录：

```bash
./scripts/build_pypi_release.sh /tmp/llm-usage-pypi
```

上传命令单独执行，不放进脚本里：

```bash
python -m twine upload dist/pypi/*
```

如果要先用 TestPyPI 验证：

```bash
python -m twine upload --repository testpypi dist/pypi/*
```

GitHub Actions 自动发布也已支持：

- 推送 tag `py-vX.Y.Z` 会触发 `.github/workflows/publish-pypi.yml`
- 也可以在 Actions 页面手动运行 `Publish PyPI`
- tag 触发时，workflow 会校验 tag 版本和 [pyproject.toml](/Users/lewis/Documents/code/agent_coding_usage/pyproject.toml) 中的 `version` 完全一致

启用前提：

- 在 PyPI 项目 `llm-usage-horizon` 中配置 GitHub trusted publisher
- publisher 的仓库填 `zaxliu/agent_coding_usage`
- workflow 名称填 `Publish PyPI`
- environment 名称填 `pypi`

手动触发不会改版本号，只会发布当前仓库里已经写入 [pyproject.toml](/Users/lewis/Documents/code/agent_coding_usage/pyproject.toml) 的版本。

## 发布到 npm

先确认 [node/package.json](/Users/lewis/Documents/code/agent_coding_usage/node/package.json) 里的 `version` 已更新，且包名仍然是 `@llm-usage-horizon/llm-usage-node`。

本地发布前校验：

```bash
cd node
npm install
npm test
npm pack --dry-run
```

GitHub Actions 自动发布也已支持：

- 推送 tag `node-vX.Y.Z` 会触发 `.github/workflows/publish-npm.yml`
- 也可以在 Actions 页面手动运行 `Publish npm`
- tag 触发时，workflow 会校验 tag 版本和 [node/package.json](/Users/lewis/Documents/code/agent_coding_usage/node/package.json) 中的 `version` 完全一致

启用前提：

- 在 npm 上为包 `@llm-usage-horizon/llm-usage-node` 启用 trusted publishing
- 关联 GitHub 仓库 `zaxliu/agent_coding_usage`
- workflow 名称填 `Publish npm`
- environment 名称填 `npm`

手动触发不会改版本号，只会发布当前仓库里已经写入 [node/package.json](/Users/lewis/Documents/code/agent_coding_usage/node/package.json) 的版本。

采集器扩展说明见 [docs/ADAPTERS.md](/Users/lewis/Documents/code/agent_coding_usage/docs/ADAPTERS.md)。
