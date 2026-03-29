# llm-usage-node

`llm-usage-node` 是这个仓库里的 Node.js CLI 版本，用于本地采集 LLM 编码工具用量，并可将聚合结果同步到飞书多维表格。

当前 npm 包能力：

- 本地采集与聚合
- 终端报表与 CSV 输出
- 飞书同步

当前限制：

- 本地命令运行不依赖 Python
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

## Node 版本要求

- Node.js `>=22`

项目主页：

- https://github.com/zaxliu/agent_coding_usage
