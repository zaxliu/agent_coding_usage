# llm-usage-sync

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
- `llm-usage doctor`：检查配置和各采集器可用性
- `llm-usage init`：生成 `.env`、`.env.example` 和 `reports/`
- `llm-usage bundle`：生成可分发的内部 / 外部脱敏压缩包

## 快速开始

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

llm-usage init
# 编辑 .env，至少补全 HASH_SALT
# ORG_USERNAME 缺失时，命令行运行会提示输入并自动写回 .env

llm-usage doctor
llm-usage collect --ui auto
```

如果你要同步到飞书，再补全飞书相关环境变量后执行：

```bash
llm-usage sync --ui auto
```

## 最小配置

`.env` 中至少建议配置：

```env
ORG_USERNAME=san.zhang
HASH_SALT=change-me
TIMEZONE=Asia/Shanghai
LOOKBACK_DAYS=7
```

说明：

- `ORG_USERNAME`：必填，用于生成稳定的匿名身份哈希
- `HASH_SALT`：必填，决定匿名字段的稳定性与不可逆性
- `TIMEZONE`：聚合时按该时区落到 `date_local`
- `LOOKBACK_DAYS`：采集窗口，默认 `7`

如果缺少 `ORG_USERNAME`，交互终端下运行时会提示输入并写回 `.env`。

远端不建议手工编辑 `REMOTE_*` 配置。推荐直接运行 `llm-usage collect --ui auto` 或 `llm-usage sync --ui auto`，按提示输入 SSH 主机、用户、端口；连通性检查通过后，确认保存即可自动写入 `.env`。

如果你是从旧版仓库迁移，旧配置通常还在仓库根目录的 `.env` 和 `reports/runtime_state.json`。这类配置现在需要一次性迁移到新的运行时路径，推荐在旧仓库根目录执行：

```bash
llm-usage import-config --from /path/to/old/repo
```

可选参数：

- `--dry-run`：先预览会复制哪些文件
- `--force`：覆盖新位置里已存在的目标文件

如果你就在旧仓库根目录执行，也可以省略 `--from`。迁移完成后，后续直接使用 `llm-usage doctor`、`llm-usage collect` 或 `llm-usage sync` 即可。

## 命令说明

### `llm-usage init`

初始化：

- `.env.example`
- `.env`
- `reports/`

### `llm-usage doctor`

检查：

- `ORG_USERNAME`、`HASH_SALT`、`TIMEZONE`
- 本地采集器是否能找到对应数据源
- `.env` 中配置的远端采集器是否可探测

### `llm-usage collect`

行为：

- 读取本地日志
- 按需选择远端 SSH 来源
- 输出终端汇总表
- 写入 `reports/usage_report.csv`

常用参数：

- `--ui auto|tui|cli|none`：远端选择界面，默认 `auto`
- `--cursor-login-timeout-sec`：Cursor 浏览器登录捕获超时，默认 `600`
- `--cursor-login-browser`：指定登录浏览器

### `llm-usage sync`

与 `collect` 相同，但会额外：

- 自动获取飞书访问令牌
- 在目标多维表格中按 `row_key` 执行插入或更新

### `llm-usage bundle`

生成两个压缩包到 `dist/`：

- `internal`：保留团队共享配置，清空个人身份和本机路径
- `external`：进一步清空飞书密钥与内部敏感配置

常用参数：

- `--output-dir dist`
- `--keep-staging`

示例：

```bash
llm-usage bundle
llm-usage bundle --output-dir dist --keep-staging
```

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
- 若 token 失效，会清空旧 token，并引导重新登录后将新 token 粘贴回命令行
- 若 token 为空且本地日志不可用，会尝试打开系统浏览器登录页，并提示将 token 粘贴回命令行后自动写回 `.env`

Windows 下使用 `default` / `chrome` / `chromium` / `edge` / `msedge` 时，不会自动扫描本地 Cursor 浏览器 cookie，而是固定走“弹出网页登录页 + 手动粘贴 token”流程。

Windows 手动登录步骤：

1. 运行 `llm-usage collect` 或 `llm-usage sync`
2. 程序会自动打开 `https://cursor.com/dashboard/usage`
3. 在浏览器里完成登录
4. 打开 DevTools
5. 在 `Application > Cookies > https://cursor.com` 中复制 `WorkosCursorSessionToken`
6. 回到命令行，粘贴到 `CURSOR_WEB_SESSION_TOKEN` 提示中
7. 程序会自动写入 `.env`，后续优先复用

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

## 分发包脱敏规则

`llm-usage bundle` 会清理以下内容：

所有分发包都会清空：

- `ORG_USERNAME`
- `CURSOR_WEB_SESSION_TOKEN`
- `CURSOR_WEB_WORKOS_ID`
- 各类本地路径覆盖变量
- 所有 `REMOTE_*`

外部分发包还会额外清空：

- `HASH_SALT`
- 所有 `FEISHU_*`

同时会重置安全默认值：

- `CURSOR_DASHBOARD_BASE_URL=https://cursor.com`
- `CURSOR_DASHBOARD_TEAM_ID=0`
- `CURSOR_DASHBOARD_PAGE_SIZE=300`
- `CURSOR_DASHBOARD_TIMEOUT_SEC=15`

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

采集器扩展说明见 [docs/ADAPTERS.md](/Users/lewis/Documents/code/agent_coding_usage/docs/ADAPTERS.md)。
