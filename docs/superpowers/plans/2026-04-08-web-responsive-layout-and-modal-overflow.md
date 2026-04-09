# Web Responsive Layout And Modal Overflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the web console usable at non-maximized desktop widths by fixing responsive layout collapse and dialog overflow, with final verification in Chrome MCP.

**Architecture:** Keep the existing single-page console and dialog behavior, but harden the layout with responsive CSS breakpoints, wrapping rules, and viewport-constrained dialog sizing. Use targeted markup hooks only where the current structure prevents reliable wrapping, and verify both CSS hooks and real browser behavior.

**Tech Stack:** Vanilla HTML/CSS/JS in `web/`, Node test runner in `node/test/web-app.test.js`, Chrome MCP for browser verification

---

### Task 1: Lock in Responsive CSS Expectations with Tests

**Files:**
- Modify: `node/test/web-app.test.js`
- Test: `node/test/web-app.test.js`

- [ ] **Step 1: Write the failing test**

```javascript
test("app.css defines responsive console and dialog overflow protections", () => {
  const css = fs.readFileSync(new URL("../../web/app.css", import.meta.url), "utf8");

  assert.match(css, /@media\s*\(max-width:\s*1100px\)/u);
  assert.match(css, /@media\s*\(max-width:\s*720px\)/u);
  assert.match(css, /\.console-layout[\s\S]*grid-template-columns:\s*320px minmax\(0,\s*1fr\)/u);
  assert.match(css, /\.summary-grid[\s\S]*repeat\(4,\s*minmax\(0,\s*1fr\)\)/u);
  assert.match(css, /\.comparison-grid[\s\S]*repeat\(2,\s*minmax\(0,\s*1fr\)\)/u);
  assert.match(css, /\.credential-form[\s\S]*max-height:\s*calc\(100vh - 32px\)/u);
  assert.match(css, /\.credential-form[\s\S]*overflow:\s*auto/u);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test node/test/web-app.test.js`
Expected: FAIL because `.credential-form` does not yet constrain height or enable internal scrolling with the expected rules

- [ ] **Step 3: Write minimal implementation**

```javascript
test("app.css defines responsive console and dialog overflow protections", () => {
  const css = fs.readFileSync(new URL("../../web/app.css", import.meta.url), "utf8");

  assert.match(css, /@media\s*\(max-width:\s*1100px\)/u);
  assert.match(css, /@media\s*\(max-width:\s*720px\)/u);
  assert.match(css, /\.console-layout[\s\S]*grid-template-columns:\s*320px minmax\(0,\s*1fr\)/u);
  assert.match(css, /\.summary-grid[\s\S]*repeat\(4,\s*minmax\(0,\s*1fr\)\)/u);
  assert.match(css, /\.comparison-grid[\s\S]*repeat\(2,\s*minmax\(0,\s*1fr\)\)/u);
  assert.match(css, /\.credential-form[\s\S]*max-height:\s*calc\(100vh - 32px\)/u);
  assert.match(css, /\.credential-form[\s\S]*overflow:\s*auto/u);
});
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test node/test/web-app.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add node/test/web-app.test.js
git commit -m "test: lock responsive console overflow expectations"
```

### Task 2: Fix Responsive Shell, Grid, and Header Wrapping

**Files:**
- Modify: `web/app.css`
- Test: `node/test/web-app.test.js`

- [ ] **Step 1: Write the failing test**

```javascript
test("app.css allows shell sections and action rows to shrink and wrap", () => {
  const css = fs.readFileSync(new URL("../../web/app.css", import.meta.url), "utf8");

  assert.match(css, /\.console-main,\s*\.console-sidebar,\s*\.panel,\s*\.metric-card[\s\S]*min-width:\s*0/u);
  assert.match(css, /\.hero[\s\S]*flex-wrap:\s*wrap/u);
  assert.match(css, /\.panel-head[\s\S]*flex-wrap:\s*wrap/u);
  assert.match(css, /\.actions[\s\S]*width:\s*100%/u);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test node/test/web-app.test.js`
Expected: FAIL because the current shell and headers do not consistently opt into shrinkage and wrapped action rows

- [ ] **Step 3: Write minimal implementation**

```css
.console-main,
.console-sidebar,
.panel,
.metric-card,
.status-card,
.stamp {
  min-width: 0;
}

.hero,
.panel-head,
.job-head,
.remote-head,
.credential-actions {
  flex-wrap: wrap;
}

.actions {
  width: 100%;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test node/test/web-app.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/app.css node/test/web-app.test.js
git commit -m "fix: make console shell shrink and wrap cleanly"
```

### Task 3: Make Panels and Forms Collapse Gracefully at Narrow Widths

**Files:**
- Modify: `web/app.css`
- Modify: `web/index.html`
- Test: `node/test/web-app.test.js`

- [ ] **Step 1: Write the failing test**

```javascript
test("index.html exposes responsive form hooks for settings and dialogs", () => {
  const html = fs.readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");

  assert.match(html, /class="form-grid settings-form-grid"/u);
  assert.match(html, /class="form-grid modal-form-grid"/u);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test node/test/web-app.test.js`
Expected: FAIL because the current markup does not distinguish page forms from modal forms

- [ ] **Step 3: Write minimal implementation**

```html
<div class="form-grid settings-form-grid">
  <label>ORG_USERNAME<input id="org-username"></label>
  <label>HASH_SALT<input id="hash-salt" type="password"></label>
</div>
```

```html
<div class="form-grid modal-form-grid">
  <label>Alias<input id="remote-edit-alias"></label>
  <label>SSH Host<input id="remote-edit-ssh-host"></label>
</div>
```

```css
@media (max-width: 1100px) {
  .summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .comparison-grid,
  .settings-grid,
  .settings-form-grid,
  .modal-form-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .console-layout {
    grid-template-columns: 1fr;
  }

  .summary-grid {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test node/test/web-app.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/index.html web/app.css node/test/web-app.test.js
git commit -m "fix: collapse dashboard and form grids responsively"
```

### Task 4: Constrain Dialog Width and Height Without Horizontal Dragging

**Files:**
- Modify: `web/app.css`
- Modify: `web/index.html`
- Test: `node/test/web-app.test.js`

- [ ] **Step 1: Write the failing test**

```javascript
test("dialog markup and styles support viewport-safe modal content", () => {
  const html = fs.readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");
  const css = fs.readFileSync(new URL("../../web/app.css", import.meta.url), "utf8");

  assert.match(html, /id="credential-modal" class="credential-modal"/u);
  assert.match(css, /\.credential-modal[\s\S]*max-width:\s*calc\(100vw - 16px\)/u);
  assert.match(css, /\.credential-form[\s\S]*width:\s*min\(460px,\s*calc\(100vw - 32px\)\)/u);
  assert.match(css, /\.credential-form[\s\S]*word-break:\s*break-word/u);
  assert.match(css, /\.credential-actions[\s\S]*justify-content:\s*flex-end/u);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test node/test/web-app.test.js`
Expected: FAIL because the dialog shell does not yet clamp its own width and the form content does not explicitly wrap long text

- [ ] **Step 3: Write minimal implementation**

```css
.credential-modal {
  max-width: calc(100vw - 16px);
  max-height: calc(100vh - 16px);
}

.credential-form {
  width: min(460px, calc(100vw - 32px));
  max-height: calc(100vh - 32px);
  overflow: auto;
  word-break: break-word;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test node/test/web-app.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/app.css web/index.html node/test/web-app.test.js
git commit -m "fix: constrain console dialogs to the viewport"
```

### Task 5: Verify the Final UI in Chrome MCP

**Files:**
- Verify: `web/index.html`
- Verify: `web/app.css`
- Verify: running web console

- [ ] **Step 1: Start the local web server**

Run: `python -m src.llm_usage.main web`
Expected: server starts and prints the local URL for the console

- [ ] **Step 2: Open the console in Chrome MCP and inspect default desktop layout**

Run: open the printed local URL with Chrome MCP
Expected: sidebar and main column render normally with no clipping at full desktop width

- [ ] **Step 3: Resize to medium desktop width and verify no page-level horizontal scrolling**

Run: use Chrome MCP resize controls to test a medium desktop width near the reported failure point
Expected: summary cards and settings sections remain visible; page shell does not require horizontal dragging

- [ ] **Step 4: Resize to narrow desktop width and verify stacked layout**

Run: use Chrome MCP resize controls to test a narrow desktop width around `720px`
Expected: sidebar stacks above the main content, summary cards collapse, and the page remains readable

- [ ] **Step 5: Trigger and verify each modal**

Run: in Chrome MCP, open the settings panel, then open the remote edit modal and Feishu target edit modal; trigger the runtime credential prompt if available, otherwise verify the credential modal structure directly in the DOM
Expected: each modal fits within the viewport, shows full prompt and actions, supports vertical scrolling if needed, and does not require horizontal dragging

- [ ] **Step 6: Confirm overflow ownership**

Run: inspect the results table at a narrow width
Expected: only the table container owns horizontal overflow; the page shell and dialogs do not
