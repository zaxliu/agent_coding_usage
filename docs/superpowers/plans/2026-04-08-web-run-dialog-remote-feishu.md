# Web Run Dialog Remote/Feishu Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pre-run confirmation dialog to the web console so `collect` and `sync` can select remotes, and `sync` can also select Feishu upload targets.

**Architecture:** Extend the existing single-page web app with one reusable run-confirm dialog that reads from `state.config`, keeps temporary selection state in the browser, and only submits API requests after explicit confirmation. Keep backend API semantics unchanged and verify the UI contract with the existing static HTML/JS test style in `node/test/web-app.test.js`.

**Tech Stack:** Vanilla HTML/CSS/JS for the web UI, existing Python/Node web backends, Node `node:test` assertions for static web app coverage.

---

### Task 1: Add failing static tests for the new dialog contract

**Files:**
- Modify: `node/test/web-app.test.js`
- Test: `node/test/web-app.test.js`

- [ ] **Step 1: Write the failing test**

```js
test("index.html exposes run-confirm dialog hooks for collect and sync selection", () => {
  const html = fs.readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");

  assert.match(html, /id="run-confirm-modal"/u);
  assert.match(html, /id="run-confirm-title"/u);
  assert.match(html, /id="run-confirm-remotes"/u);
  assert.match(html, /id="run-confirm-feishu-section"/u);
  assert.match(html, /id="run-confirm-feishu-default"/u);
  assert.match(html, /id="run-confirm-feishu-all"/u);
  assert.match(html, /id="run-confirm-feishu-targets"/u);
});

test("app.js opens a run-confirm dialog before collect and sync, then submits selection payload", () => {
  const js = fs.readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");

  assert.match(js, /function openRunConfirmModal\(action\)/u);
  assert.match(js, /action === "collect"[\s\S]*openRunConfirmModal\(action\)/u);
  assert.match(js, /action === "sync"[\s\S]*openRunConfirmModal\(action\)/u);
  assert.match(js, /selected_remotes/u);
  assert.match(js, /feishu_targets/u);
  assert.match(js, /all_feishu_targets/u);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/lewis/Documents/code/agent_coding_usage && node --test node/test/web-app.test.js`
Expected: FAIL on missing `run-confirm` dialog hooks and missing `openRunConfirmModal` assertions.

- [ ] **Step 3: Write minimal implementation**

```js
// Minimal implementation target for later tasks:
// add dialog ids in web/index.html
// add openRunConfirmModal(action)
// route collect/sync through modal instead of direct POST
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/lewis/Documents/code/agent_coding_usage && node --test node/test/web-app.test.js`
Expected: PASS for the new dialog-related assertions.

- [ ] **Step 5: Commit**

```bash
cd /Users/lewis/Documents/code/agent_coding_usage
git add node/test/web-app.test.js web/index.html web/app.js
git commit -m "test: cover web run confirmation dialog"
```

### Task 2: Add dialog markup for runtime selection

**Files:**
- Modify: `web/index.html`
- Test: `node/test/web-app.test.js`

- [ ] **Step 1: Write the failing test**

```js
test("index.html exposes run-confirm dialog hooks for collect and sync selection", () => {
  const html = fs.readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");

  assert.match(html, /id="run-confirm-modal"/u);
  assert.match(html, /id="run-confirm-copy"/u);
  assert.match(html, /id="run-confirm-remotes"/u);
  assert.match(html, /id="run-confirm-remotes-empty"/u);
  assert.match(html, /id="run-confirm-feishu-section"/u);
  assert.match(html, /id="run-confirm-feishu-default"/u);
  assert.match(html, /id="run-confirm-feishu-all"/u);
  assert.match(html, /id="run-confirm-feishu-targets"/u);
  assert.match(html, /id="run-confirm-submit"/u);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/lewis/Documents/code/agent_coding_usage && node --test node/test/web-app.test.js --test-name-pattern "run-confirm dialog hooks"`
Expected: FAIL because the dialog markup does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```html
<dialog id="run-confirm-modal" class="credential-modal">
  <form method="dialog" class="credential-form" id="run-confirm-form">
    <p class="eyebrow">运行前确认</p>
    <h3 id="run-confirm-title">确认采集</h3>
    <p id="run-confirm-copy">选择这次运行使用的来源和目标。</p>

    <section>
      <div class="panel-head"><h3>远端来源</h3></div>
      <p id="run-confirm-remotes-empty" class="panel-note" hidden>未配置远端，将只采集本地数据。</p>
      <div id="run-confirm-remotes" class="settings-list"></div>
    </section>

    <section id="run-confirm-feishu-section" hidden>
      <div class="panel-head"><h3>飞书目标</h3></div>
      <label class="checkbox-label">
        <input id="run-confirm-feishu-default" type="radio" name="feishu-mode" value="default" checked>
        <span>默认目标</span>
      </label>
      <label class="checkbox-label">
        <input id="run-confirm-feishu-all" type="radio" name="feishu-mode" value="all">
        <span>全部 named targets</span>
      </label>
      <div id="run-confirm-feishu-targets" class="settings-list"></div>
    </section>

    <div class="credential-actions">
      <button value="cancel" class="button-subtle">取消</button>
      <button id="run-confirm-submit" value="submit" class="button-primary">开始采集</button>
    </div>
  </form>
</dialog>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/lewis/Documents/code/agent_coding_usage && node --test node/test/web-app.test.js --test-name-pattern "run-confirm dialog hooks"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/lewis/Documents/code/agent_coding_usage
git add web/index.html node/test/web-app.test.js
git commit -m "feat: add web run confirmation dialog markup"
```

### Task 3: Add dialog state helpers and selection payload assembly

**Files:**
- Modify: `web/app.js`
- Test: `node/test/web-app.test.js`

- [ ] **Step 1: Write the failing test**

```js
test("app.js opens a run-confirm dialog before collect and sync, then submits selection payload", () => {
  const js = fs.readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");

  assert.match(js, /runConfirmModal: document\.querySelector\("#run-confirm-modal"\)/u);
  assert.match(js, /function openRunConfirmModal\(action\)/u);
  assert.match(js, /function buildRunConfirmPayload\(\)/u);
  assert.match(js, /selected_remotes:\s*selectedRemotes/u);
  assert.match(js, /feishu_targets:\s*selectedFeishuTargets/u);
  assert.match(js, /all_feishu_targets:\s*selectAllFeishuTargets/u);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/lewis/Documents/code/agent_coding_usage && node --test node/test/web-app.test.js --test-name-pattern "selection payload"`
Expected: FAIL because the dialog references and payload helper do not exist yet.

- [ ] **Step 3: Write minimal implementation**

```js
const state = {
  ...createUiFlags(),
  pendingRunAction: "",
};

function buildRunConfirmPayload() {
  const selectedRemotes = [...document.querySelectorAll('input[data-run-remote]:checked')].map((input) => input.value);
  const selectAllFeishuTargets = document.querySelector("#run-confirm-feishu-all")?.checked || false;
  const selectedFeishuTargets = selectAllFeishuTargets
    ? []
    : [...document.querySelectorAll('input[data-run-feishu-target]:checked')].map((input) => input.value);
  return {
    selected_remotes: selectedRemotes,
    feishu_targets: selectedFeishuTargets,
    all_feishu_targets: selectAllFeishuTargets,
  };
}

function openRunConfirmModal(action) {
  state.pendingRunAction = action;
  refs.runConfirmModal.showModal();
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/lewis/Documents/code/agent_coding_usage && node --test node/test/web-app.test.js --test-name-pattern "selection payload"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/lewis/Documents/code/agent_coding_usage
git add web/app.js node/test/web-app.test.js
git commit -m "feat: add web run dialog selection state"
```

### Task 4: Route collect and sync through the dialog

**Files:**
- Modify: `web/app.js`
- Test: `node/test/web-app.test.js`

- [ ] **Step 1: Write the failing test**

```js
test("app.js routes collect and sync through the run-confirm dialog before posting", () => {
  const js = fs.readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");

  assert.match(js, /if \(action === "collect"\) \{[\s\S]*openRunConfirmModal\(action\)[\s\S]*return;/u);
  assert.match(js, /if \(action === "sync"\) \{[\s\S]*openRunConfirmModal\(action\)[\s\S]*return;/u);
  assert.match(js, /refs\.runConfirmForm\.addEventListener\("submit"/u);
  assert.match(js, /await getJson\(url,\s*\{ method: "POST", body: JSON\.stringify\(payload\) \}\)/u);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/lewis/Documents/code/agent_coding_usage && node --test node/test/web-app.test.js --test-name-pattern "routes collect and sync"`
Expected: FAIL because `collect` and `sync` still post directly.

- [ ] **Step 3: Write minimal implementation**

```js
if (action === "collect" || action === "sync") {
  openRunConfirmModal(action);
  return;
}

refs.runConfirmForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if ((event.submitter?.value || "") === "cancel") {
    refs.runConfirmModal.close();
    state.pendingRunAction = "";
    return;
  }
  const action = state.pendingRunAction;
  const payload = buildRunConfirmPayload();
  if (action === "sync") {
    payload.confirm_sync = true;
  }
  const url = action === "sync" ? "/api/sync" : "/api/collect";
  await getJson(url, { method: "POST", body: JSON.stringify(payload) });
});
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/lewis/Documents/code/agent_coding_usage && node --test node/test/web-app.test.js --test-name-pattern "routes collect and sync"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/lewis/Documents/code/agent_coding_usage
git add web/app.js node/test/web-app.test.js
git commit -m "feat: route web collect and sync through confirmation dialog"
```

### Task 5: Polish the dialog rendering and empty-state behavior

**Files:**
- Modify: `web/app.js`
- Modify: `web/app.css`
- Test: `node/test/web-app.test.js`

- [ ] **Step 1: Write the failing test**

```js
test("app.js renders empty-state copy and sync-specific feishu controls in the run-confirm dialog", () => {
  const js = fs.readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
  const css = fs.readFileSync(new URL("../../web/app.css", import.meta.url), "utf8");

  assert.match(js, /未配置远端，将只采集本地数据/u);
  assert.match(js, /未配置 named targets，将使用默认目标/u);
  assert.match(js, /run-confirm-feishu-section/u);
  assert.match(css, /\.run-confirm-grid/u);
  assert.match(css, /\.run-confirm-list/u);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/lewis/Documents/code/agent_coding_usage && node --test node/test/web-app.test.js --test-name-pattern "empty-state copy"`
Expected: FAIL because the rendering copy and dialog-specific styles are missing.

- [ ] **Step 3: Write minimal implementation**

```css
.run-confirm-grid {
  display: grid;
  gap: 16px;
}

.run-confirm-list {
  display: grid;
  gap: 10px;
}
```

```js
refs.runConfirmRemotesEmpty.hidden = remotes.length > 0;
refs.runConfirmRemotesEmpty.textContent = "未配置远端，将只采集本地数据。";
refs.runConfirmFeishuTargets.innerHTML = targets.length
  ? targets.map(...)
  : `<div class="empty-state empty-state-compact">未配置 named targets，将使用默认目标。</div>`;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/lewis/Documents/code/agent_coding_usage && node --test node/test/web-app.test.js --test-name-pattern "empty-state copy"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/lewis/Documents/code/agent_coding_usage
git add web/app.js web/app.css node/test/web-app.test.js
git commit -m "style: polish web run confirmation dialog"
```

### Task 6: Run verification for the completed web dialog flow

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/app.css`
- Test: `node/test/web-app.test.js`

- [ ] **Step 1: Run the focused web test suite**

Run: `cd /Users/lewis/Documents/code/agent_coding_usage && node --test node/test/web-app.test.js`
Expected: PASS with all web app static assertions green.

- [ ] **Step 2: Run the broader Node web/runtime suite**

Run: `cd /Users/lewis/Documents/code/agent_coding_usage && node --test node/test/web.test.js node/test/web-app.test.js`
Expected: PASS with no regressions in web route/config coverage.

- [ ] **Step 3: Inspect the diff**

Run: `cd /Users/lewis/Documents/code/agent_coding_usage && git diff -- web/index.html web/app.js web/app.css node/test/web-app.test.js`
Expected: Diff only shows the run-confirm dialog, JS routing/state changes, styles, and test updates.

- [ ] **Step 4: Commit**

```bash
cd /Users/lewis/Documents/code/agent_coding_usage
git add web/index.html web/app.js web/app.css node/test/web-app.test.js
git commit -m "feat: add web runtime remote and feishu target selection"
```
