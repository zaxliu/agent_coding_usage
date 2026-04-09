# Web Run Dialog Remote/Feishu Design

**Goal**

为网页版控制台补充运行前确认弹框，使用户在执行 `collect` 和 `sync` 前可以选择参与采集的远端来源；在执行 `sync` 前还可以选择上传到哪些飞书目标。

**Current State**

- 设置页已经支持维护远端列表和命名飞书目标。
- Web 后端已经支持从请求体读取 `selected_remotes`、`feishu_targets` 和 `all_feishu_targets`。
- 当前前端在点击 `collect` / `sync` 时直接发请求，没有任何运行前选择入口。

**User-Visible Behavior**

`collect`

- 点击“采集”后不直接执行，先打开“确认采集”弹框。
- 弹框展示当前已配置远端的多选列表。
- 默认勾选全部已配置远端。
- 允许用户取消全部远端；此时采集仅包含本地来源。
- 用户确认后，前端向 `/api/collect` 提交 `selected_remotes`。

`sync`

- 点击“同步”后不直接执行，先打开“确认同步”弹框。
- 弹框包含两块内容：
  - 远端多选，规则与 `collect` 一致。
  - 飞书目标选择。
- 飞书目标选择支持三种状态：
  - 仅默认目标。
  - 选定一个或多个 named targets。
  - 全部 named targets。
- 当选择“全部 named targets”时，前端提交 `all_feishu_targets: true`。
- 当选择部分 named targets`时，前端提交 `feishu_targets: [...]`。
- 当未选择 named targets 且未启用“全部 named targets”时，保持默认目标行为，即提交空的 `feishu_targets` 且 `all_feishu_targets: false`。
- 用户确认后，前端向 `/api/sync` 提交 `selected_remotes`、`feishu_targets`、`all_feishu_targets`、`confirm_sync: true`。

**Interaction Design**

- 采用一个通用对话框组件，不新增新页面，也不改设置页结构。
- 对话框标题和确认按钮文案根据动作切换：
  - `collect`: “确认采集” / “开始采集”
  - `sync`: “确认同步” / “开始同步”
- `collect` 仅渲染远端区域。
- `sync` 渲染远端区域和飞书目标区域。
- 如果没有已配置远端，则远端区域显示“未配置远端，将只采集本地数据”。
- 如果没有命名飞书目标，则 `sync` 的飞书区域显示“未配置 named targets，将使用默认目标”。

**Implementation Boundaries**

- 保持后端 API 语义不变，优先只补前端 UI 和请求组装。
- 复用现有 `state.config` 中的 `remotes` 和 `feishu_targets`，不引入新的配置来源。
- 不改动已有远端编辑和飞书目标编辑逻辑。
- 不在本次范围内增加 `doctor` 的远端/飞书选择弹框。

**Error Handling**

- 用户取消弹框时不发起请求。
- 若请求失败，沿用现有 flashbar 和运行状态卡错误展示。
- 若配置为空，对话框只展示说明，不阻止执行默认路径：
  - `collect` 无远端时继续本地采集。
  - `sync` 无 named targets 时继续默认目标同步。

**Testing**

- 为 `web/index.html` 增加对运行前弹框 DOM 钩子的静态断言。
- 为 `web/app.js` 增加对以下行为的静态断言：
  - `collect` 和 `sync` 不再直接提交请求，而是先打开对话框。
  - 确认提交时会组装 `selected_remotes`。
  - `sync` 确认提交时会组装 `feishu_targets` 和 `all_feishu_targets`。
- 如果需要纯函数辅助，可优先放入 `web/app-state.js`，并为其写单元测试。

**Out of Scope**

- 新增后端字段或 API。
- 保存“上次选择”的持久化行为。
- 在 dashboard 或 settings 面板直接做常驻筛选器。
