# LLM Usage Horizon v0.2.0 Preview Release Notes

> 版本跨度：0.1.5 → 0.2.0rc1 → 0.2.0rc2 → 0.2.0rc3
> 变更规模：67 文件，+16,232 / -209 行

---

## 0.2.0rc1 — Web Console & Runtime Preflight

**发布日期**：2026-04-10 &nbsp;|&nbsp; **变更规模**：52 文件，+12,289 / -129 行

这是 0.2.0 系列的首个预览版本，引入两大核心能力：**本地 Web 控制台**与**运行时预检框架**。

### 本地 Web 控制台（全新）

新增内置 Web 控制台，提供浏览器端的可视化管理界面：

- **Dashboard 仪表盘**：用量数据可视化，图表数字本地化 & 紧凑显示
- **远程管理 CRUD**：通过模态对话框完整管理远程主机（alias、SSH 配置、sshpass、日志路径），支持新增/编辑/删除
- **飞书 Target 编辑**：Web 端飞书命名 target 的完整 CRUD，大小写不敏感的重名检测
- **一键初始化**：通过 `/api/init` 端点从 Web UI 创建 `.env` 和报告目录
- **运行确认对话框**：执行采集/同步前的确认面板，远程和飞书分区展示，命名 target 折叠显示
- **表格增强**：列筛选和排序功能
- **品牌化**：Favicon 和侧边栏品牌图标
- **响应式布局**：移动端适配加固

### 运行时预检框架（全新）

建立统一的运行前校验机制，覆盖所有执行路径：

- 新增共享 `runtime_preflight` 模块，提供 `validate_basic_config()` 和 `validate_runtime_config()` 
- **collect 路径**：采集前校验 ORG_USERNAME / HASH_SALT 基础配置
- **sync 路径**：同步前执行飞书 preflight；`--dry-run` 仅校验基础配置（跳过飞书）；`--from-bundle` 在读取 bundle 前执行完整飞书预检
- **Web 路径**：config 端点应用 preflight，save/sync 操作强制预检
- 飞书 doctor 新增写入权限校验
- 执行 preflight 仅作用于用户选中的飞书 target，避免无关 target 阻塞

### 配置编辑器改进

- 所有子菜单新增 `s. Save` 快捷保存，脏状态标识 `(*)` 
- `.env` 注释说明 APP_ID/APP_SECRET 从默认 target 继承行为
- 飞书 target 选择引导整合到 sync 帮助文本
- 子菜单保存输入校验

### Bug 修复

- 修复 SSH 认证失败后无法重新输入密码的问题
- 修复 config sidebar XSS 注入漏洞（`escapeHtml` 转义）
- 修复 Web favicon 静态文件交付
- 修复飞书同步在 preflight 错误时未提前终止的问题
- 移除已废弃的飞书链接分享 helper

### 工程化

- macOS CI 排除 Python 3.9/3.10（ARM64 兼容性问题）
- 跨平台测试路径修复（Windows `fileURLToPath`）
- Web 端测试套件：547+ 行 web-app 测试、241+ 行 web 测试
- Runtime preflight 测试套件：201 行独立测试

---

## 0.2.0rc2 — 飞书连通性探测 & 性能优化

**发布日期**：2026-04-11 &nbsp;|&nbsp; **变更规模**：9 文件，+383 / -74 行

针对 rc1 的补充修复版本，聚焦于飞书连通性诊断和 CLI 启动性能。

### 飞书连通性探测（新增）

- sync 和 feishu doctor 命令执行前新增 `open.feishu.cn` 可达性检测
- 5 秒超时，优先使用 `fetch_tenant_access_token` 探测，无凭据时降级为 HTTPS GET
- 用户可在第一时间获得清晰的"无法连接飞书"错误，而非在上传深处遭遇不可理解的失败

### CLI 启动性能

- CLI 依赖改为懒加载（lazy import），减少 `import llm_usage` 的初始化耗时
- 仅在实际调用相关子命令时才加载对应模块

### Bug 修复

- 配置编辑器 EOF（Ctrl+D）处理修复，不再陷入无限循环

### 测试加固

- 飞书命令测试新增 169 行覆盖（连通性探测场景）
- 飞书 target 配置测试新增 55 行
- cursor 登录测试重构，隔离环境假设

---

## 0.2.0rc3 — Jump Host (Bastion) 支持

**发布日期**：2026-04-13 &nbsp;|&nbsp; **变更规模**：27 文件，+3,588 / -34 行

此版本新增企业级网络环境最常见的需求：通过跳板机连接远程服务器。

### Jump Host / Bastion 支持（新增）

端到端实现通过跳板机（bastion host）连接远程服务器：

- **数据模型**：新增 `ssh_jump_host` 和 `ssh_jump_port` 字段
- **环境变量持久化**：写入 `.env` 并正确读取
- **CLI 配置编辑器**：远程主机编辑新增 jump host / port 输入项
- **Web API**：远程主机 CRUD 接口支持 jump 字段
- **SSH 命令构建**：自动拼接 `-J user@jump_host:port` 参数
- **输入校验**：拒绝 jump host 中包含 `@` 或空白字符
- **Web 端清理**：删除远程主机时清理陈旧的 `REMOTE_*` 环境变量
- **容错处理**：collect / doctor 命令在 SSH 认证失败时优雅降级，支持交互式密码输入

### 测试隔离改进

- CLI 环境假设测试隔离，避免不同机器上的环境差异导致测试失败
- 无效 bundle CLI 测试用例从 preflight 测试中剥离
- 远程文件采集器新增 jump host 场景测试（+64 行）
- 交互流程测试新增 jump host 输入覆盖（+84 行）

### 文档

- 新增多份设计规格文档（preflight robustness、web layout、feishu probe 等）

---

## 版本对比总览

| 维度 | rc1 | rc2 | rc3 |
|------|-----|-----|-----|
| **核心主题** | Web Console + Preflight | 连通性诊断 + 性能 | Jump Host 支持 |
| **文件变更** | 52 | 9 | 27 |
| **代码增量** | +12,289 / -129 | +383 / -74 | +3,588 / -34 |
| **新增模块** | `web.py`, `runtime_preflight.py`, `interaction_flow.py`, 整套 web 前端 | — | — |
| **破坏性变更** | 无 | 无 | 无 |

## 升级指引

```bash
pip install llm-usage-horizon==0.2.0rc3
```

从 0.1.x 升级无需额外迁移步骤。新增的 Web Console 通过 `llm-usage web` 命令启动，所有新功能向后兼容。

---

*本文档基于 git commit e9705e9 (v0.1.5) 至 e8dd444 (v0.2.0rc3) 的提交记录生成。*
