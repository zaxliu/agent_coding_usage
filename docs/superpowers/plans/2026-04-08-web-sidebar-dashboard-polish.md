# Web Sidebar Dashboard Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move console actions into the left sidebar, simplify the dashboard entry, and make runtime state more legible during active jobs.

**Architecture:** Keep the shared single-page shell in `web/`, but shift the operations cluster into the sidebar and drive a single runtime status card from job state in `web/app.js`. Keep config preview data in the existing sidebar summary and append the `.env` location there instead of the runtime status area.

**Tech Stack:** Static HTML, shared CSS, vanilla JavaScript, Node test runner.

---

### Task 1: Lock The New Shell Shape With Frontend Tests

**Files:**
- Modify: `node/test/web-app.test.js`

- [ ] **Step 1: Write the failing shell expectations**

```js
assert.match(css, /\.sidebar-actions\b/u);
assert.match(css, /\.status-card\.is-running\b[\s\S]*animation:/u);
assert.match(html, /class="panel compact-panel sidebar-actions" id="operations-bar"/u);
assert.doesNotMatch(html, /<span class="status-label">后端<\/span>/u);
assert.doesNotMatch(html, /<h2>最近 30 天<\/h2>/u);
```

- [ ] **Step 2: Run the focused frontend test**

Run: `node --test node/test/web-app.test.js`

Expected: fail because the current shell still keeps the operations cluster in the main header and still renders the old status/dashboard text.

### Task 2: Move Controls Into The Sidebar And Simplify Main Content Entry

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.css`

- [ ] **Step 1: Move the operations markup into the sidebar**

```html
<section class="panel compact-panel sidebar-actions" id="operations-bar">
  <div class="stamp">
    <span class="stamp-label">更新时间</span>
    <strong id="generated-at">等待数据</strong>
  </div>
  <div class="actions">
    <button data-action="init">初始化</button>
    <button data-action="doctor">诊断</button>
    <button data-action="collect">采集</button>
    <button data-action="sync-preview">同步预览</button>
    <button data-action="sync" class="button-primary">同步</button>
  </div>
</section>
```

- [ ] **Step 2: Remove the dashboard hero copy and keep the main panel content-first**

```html
<main class="main-panel console-main">
  <section class="panel settings-panel collapsed" id="settings-panel" hidden>
```

- [ ] **Step 3: Add sidebar action-grid and runtime-status visual styling**

```css
.sidebar-actions .actions {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.status-card.is-running {
  animation: statusCardBlink 1.1s ease-in-out infinite;
}
```

### Task 3: Drive The Sidebar Status And Config Preview From Runtime Data

**Files:**
- Modify: `web/app.js`

- [ ] **Step 1: Add a dedicated runtime status renderer**

```js
function renderRuntimeStatus(status = "idle", title = "空闲", meta = "可以开始新的任务") {
  const className = String(status || "idle").replace(/_/g, "-");
  refs.runtimeStatusCard.classList.remove("is-idle", "is-running", "is-needs-input", "is-failed", "is-succeeded");
  refs.runtimeStatusCard.classList.add(`is-${className}`);
  refs.runtimeBackend.textContent = title;
  refs.runtimeMeta.textContent = meta;
}
```

- [ ] **Step 2: Append the `.env` path to the config preview**

```js
const summaryItems = buildConfigSummary(config);
summaryItems.push({ label: ".env", value: state.runtime?.env_path || "-" });
```

- [ ] **Step 3: Map latest job state into the runtime status card**

```js
if (latest.status === "needs_input") {
  renderRuntimeStatus("needs_input", "等待输入", latest.input_request?.message || `${latest.type} 需要当前会话输入`);
} else {
  renderRuntimeStatus("running", "运行中", `${latest.type} · ${fmtTime(latest.updated_at || latest.created_at)}`);
}
```

### Task 4: Verify

**Files:**
- Verify: `node/test/web-app.test.js`

- [ ] **Step 1: Run the focused frontend regression test**

Run: `node --test node/test/web-app.test.js`

Expected: pass with 0 failures.
