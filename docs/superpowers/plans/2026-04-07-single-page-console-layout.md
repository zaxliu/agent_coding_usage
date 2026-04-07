# Single-Page Console Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the web UI into a single-page console where system status, configuration summary, operational actions, and dashboard results all live in one coherent layout.

**Architecture:** Keep the current data-fetching and modal behavior intact, but replace the two-view layout with a single responsive console shell. Move status and configuration summary into a persistent left column, move operations and results into a continuous right-column workflow, and keep settings as an expandable inline panel rather than a separate view.

**Tech Stack:** Vanilla HTML/CSS/JS in `web/`, Node-based frontend helper tests in `node/test/web-app.test.js`

---

### Task 1: Remove View Switching and Build the Single-Page HTML Structure

**Files:**
- Modify: `web/index.html`
- Test: `node/test/web-app.test.js`

- [ ] **Step 1: Write the failing test**

```javascript
import fs from "node:fs";

test("index.html exposes a single-page console shell without dashboard/settings nav tabs", () => {
  const html = fs.readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");

  assert.match(html, /console-layout/u);
  assert.doesNotMatch(html, /data-view-target=\"dashboard\"/u);
  assert.doesNotMatch(html, /data-view-target=\"settings\"/u);
  assert.match(html, /id=\"settings-panel\"/u);
  assert.match(html, /id=\"system-status\"/u);
  assert.match(html, /id=\"config-summary\"/u);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test node/test/web-app.test.js`
Expected: FAIL because `web/index.html` still contains `data-view-target="dashboard"` / `data-view-target="settings"` and does not yet expose the new shell ids

- [ ] **Step 3: Write minimal implementation**

```html
<div class="app-shell console-layout">
  <aside class="console-sidebar">
    <section class="status-stack" id="system-status">...</section>
    <section class="panel compact-panel" id="latest-job-panel">...</section>
    <section class="panel compact-panel" id="config-summary">...</section>
  </aside>

  <main class="console-main">
    <section class="hero console-hero">
      <div class="operations-bar" id="operations-bar">...</div>
    </section>
    <section class="panel settings-panel collapsed" id="settings-panel">...</section>
    <section class="summary-grid" id="summary-cards">...</section>
    <section class="panel chart-panel">...</section>
    <section class="comparison-grid">...</section>
    <section class="panel table-panel">...</section>
  </main>
</div>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test node/test/web-app.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/index.html node/test/web-app.test.js
git commit -m "Restructure web console into single-page shell"
```

### Task 2: Add Frontend State Helpers for Config Summary and Settings Panel Mode

**Files:**
- Modify: `web/app-state.js`
- Modify: `node/test/web-app.test.js`

- [ ] **Step 1: Write the failing test**

```javascript
test("buildConfigSummary extracts compact config facts for the sidebar", () => {
  const summary = buildConfigSummary({
    basic: {
      ORG_USERNAME: "san.zhang",
      TIMEZONE: "Asia/Shanghai",
      LOOKBACK_DAYS: "30",
    },
    remotes: [{ alias: "SERVER_A" }, { alias: "SERVER_B" }],
  });

  assert.deepEqual(summary, [
    { label: "用户", value: "san.zhang" },
    { label: "时区", value: "Asia/Shanghai" },
    { label: "回看", value: "30 天" },
    { label: "远端", value: "2 个" },
  ]);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test node/test/web-app.test.js`
Expected: FAIL because `buildConfigSummary` is not exported yet

- [ ] **Step 3: Write minimal implementation**

```javascript
export function buildConfigSummary(config = {}) {
  const basic = config.basic || {};
  const remotes = Array.isArray(config.remotes) ? config.remotes : [];
  return [
    { label: "用户", value: String(basic.ORG_USERNAME || "-") },
    { label: "时区", value: String(basic.TIMEZONE || "-") },
    { label: "回看", value: basic.LOOKBACK_DAYS ? `${basic.LOOKBACK_DAYS} 天` : "-" },
    { label: "远端", value: `${remotes.length} 个` },
  ];
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test node/test/web-app.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/app-state.js node/test/web-app.test.js
git commit -m "Add sidebar config summary helpers"
```

### Task 3: Wire the Single-Page Console in `app.js`

**Files:**
- Modify: `web/app.js`
- Modify: `web/app-state.js`
- Modify: `web/index.html`
- Test: `node/test/web-app.test.js`

- [ ] **Step 1: Write the failing test**

```javascript
test("status and settings helpers keep inline settings collapsed by default", () => {
  assert.equal(createUiFlags().settingsOpen, false);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test node/test/web-app.test.js`
Expected: FAIL because `createUiFlags` does not exist and the inline settings state is not modeled

- [ ] **Step 3: Write minimal implementation**

```javascript
export function createUiFlags() {
  return { settingsOpen: false };
}
```

```javascript
const state = {
  ...createUiFlags(),
  runtime: null,
  config: null,
  results: null,
  jobs: [],
};

function toggleSettings(open = !state.settingsOpen) {
  state.settingsOpen = open;
  refs.settingsPanel.hidden = !open;
}

function renderConfigSummary(config) {
  refs.configSummary.innerHTML = buildConfigSummary(config)
    .map((item) => `<div class="summary-pair"><span>${item.label}</span><strong>${item.value}</strong></div>`)
    .join("");
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test node/test/web-app.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/app.js web/app-state.js web/index.html node/test/web-app.test.js
git commit -m "Wire single-page console state and summary rendering"
```

### Task 4: Rebuild the CSS for the New Single-Page Console Layout

**Files:**
- Modify: `web/app.css`
- Modify: `web/index.html`
- Test: `node/test/web-app.test.js`

- [ ] **Step 1: Write the failing test**

```javascript
test("single-page console layout exposes dedicated sidebar and main-column classes", () => {
  const css = fs.readFileSync(new URL("../../web/app.css", import.meta.url), "utf8");

  assert.match(css, /\.console-layout/u);
  assert.match(css, /\.console-sidebar/u);
  assert.match(css, /\.console-main/u);
  assert.match(css, /\.operations-bar/u);
  assert.match(css, /\.settings-panel/u);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test node/test/web-app.test.js`
Expected: FAIL because those selectors do not exist yet

- [ ] **Step 3: Write minimal implementation**

```css
.console-layout {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 0;
}

.console-sidebar {
  display: grid;
  gap: 16px;
  padding: 24px 20px;
}

.console-main {
  padding: 24px;
}

.operations-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
}

.settings-panel[hidden] {
  display: none;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test node/test/web-app.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/app.css web/index.html node/test/web-app.test.js
git commit -m "Style single-page web console layout"
```

### Task 5: Preserve Dashboard Behavior in the New Layout

**Files:**
- Modify: `web/app.js`
- Modify: `web/app-state.js`
- Modify: `node/test/web-app.test.js`

- [ ] **Step 1: Write the failing test**

```javascript
test("formatCompactNumber keeps uppercase compact units for chart labels and cards", () => {
  assert.equal(formatCompactNumber(1200), "1.2K");
  assert.equal(formatCompactNumber(2_500_000), "2.5M");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test node/test/web-app.test.js`
Expected: FAIL if any layout refactor accidentally regresses the compact formatting helpers or their imports

- [ ] **Step 3: Write minimal implementation**

```javascript
// Keep renderSummary(), renderTrendChart(), renderBreakdown(), and renderTable()
// operating on the existing normalized payload while updating only target containers.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test node/test/web-app.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/app.js web/app-state.js node/test/web-app.test.js
git commit -m "Keep dashboard rendering intact in single-page console"
```

### Task 6: End-to-End Frontend Verification for Single-Page Console

**Files:**
- Modify: `node/test/web-app.test.js`
- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/app.css`

- [ ] **Step 1: Write the failing test**

```javascript
test("single-page console markup keeps status, settings summary, and operations on one screen", () => {
  const html = fs.readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");

  assert.match(html, /id=\"system-status\"/u);
  assert.match(html, /id=\"config-summary\"/u);
  assert.match(html, /id=\"operations-bar\"/u);
  assert.match(html, /id=\"settings-panel\"/u);
  assert.doesNotMatch(html, /data-view=\"settings\"/u);
  assert.doesNotMatch(html, /data-view=\"dashboard\"/u);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test node/test/web-app.test.js`
Expected: FAIL until all old split-view markup is removed

- [ ] **Step 3: Write minimal implementation**

```html
<!-- Remove the old .view wrappers and render a single continuous document flow -->
```

```javascript
// Remove navigate() bindings and any assumptions that a view switch is required.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test node/test/web-app.test.js && node --check web/app-state.js && node --check web/app.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/index.html web/app.js web/app.css node/test/web-app.test.js
git commit -m "Finish single-page web console layout"
```
