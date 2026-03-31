# llm-usage-node

`llm-usage-node` 是这个仓库里的 Node.js CLI 版本，用于本地采集 LLM 编码工具用量，并可将聚合结果同步到飞书多维表格。

当前 npm 包能力：

- `init`：初始化运行时 `.env` 与 `reports/`
- `whoami`：输出当前匿名身份与各来源哈希
- `import-config`：一次性导入旧仓库根目录下的 `.env` / `reports/runtime_state.json`
- 本地采集与聚合
- `export-bundle`：导出离线 bundle，供后续联网机器上传
- Cursor 本地日志 + 网页仪表盘采集
- Cursor 登录辅助：`auto|managed-profile|manual`
- 终端报表与 CSV 输出
- 飞书同步

当前限制：

- 远端 SSH 采集暂未在 Node 版本中实现；检测到远端配置时会提示并忽略
- 远端相关的 `REMOTE_*` 配置当前仅用于兼容现有配置和 `whoami` 哈希查看，不参与 Node 本地采集

## 安装

```bash
npm install -g @llm-usage-horizon/llm-usage-node
```

## 命令

```bash
llm-usage-node init
llm-usage-node whoami
llm-usage-node doctor
llm-usage-node collect --ui none
llm-usage-node export-bundle
llm-usage-node sync --ui none
llm-usage-node sync --from-bundle /path/to/offline.zip --dry-run
```

Cursor 相关常用参数：

- `--cursor-login-mode auto|managed-profile|manual`
- `--cursor-login-browser default|chrome|edge|safari|firefox|chromium|msedge|webkit`
- `--cursor-login-user-data-dir PATH`
- `--cursor-login-timeout-sec N`

Windows 下使用 `default` / `chrome` / `chromium` / `edge` / `msedge` 时，`auto` 会优先切到 `managed-profile`。
CLI 会启动一个受工具管理的 Chromium profile，等待 `WorkosCursorSessionToken` 出现在该 profile 的 cookie 数据库中，再写回运行时 `.env`。
如果自动捕获失败，则回退到手动粘贴 token。

## Node 版本要求

- Node.js `>=22`

项目主页：

- https://github.com/zaxliu/agent_coding_usage

## 发布

发布前先更新 [package.json](/Users/lewis/Documents/code/agent_coding_usage/node/package.json) 中的 `version`，再执行本地校验：

```bash
npm install
npm test
npm pack --dry-run
```

仓库已提供 GitHub Actions workflow：

- 推送 tag `node-vX.Y.Z` 自动发布到 npm
- 或手动运行 `Publish npm`

tag 发布会校验 tag 版本与 [package.json](/Users/lewis/Documents/code/agent_coding_usage/node/package.json) 中的 `version` 是否一致。workflow 采用 npm trusted publishing，仓库侧 environment 名称为 `npm`。
