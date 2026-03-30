# llm-usage-node

`llm-usage-node` 是这个仓库里的 Node.js CLI 版本，用于本地采集 LLM 编码工具用量，并可将聚合结果同步到飞书多维表格。

当前 npm 包能力：

- 本地采集与聚合
- Cursor 本地日志 + 网页仪表盘采集
- Cursor 登录辅助：`auto|managed-profile|manual`
- 终端报表与 CSV 输出
- 飞书同步

当前限制：

- 远端 SSH 采集暂未在 Node 版本中实现；检测到远端配置时会提示并忽略

## 安装

```bash
npm install -g llm-usage-node
```

## 命令

```bash
llm-usage-node doctor
llm-usage-node collect --ui none
llm-usage-node sync --ui none
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
