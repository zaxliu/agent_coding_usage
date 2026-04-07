# Web Console Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current JSON-heavy local web console with a dashboard-first experience, move persistent config into a separate Settings page, and add session-scoped runtime credential prompts for remote SSH password entry during collect/sync.

**Architecture:** Keep the shared static frontend in `web/`, but restructure it into Dashboard and Settings views with chart-ready presentation logic. Upgrade both Python and Node web backends to expose dashboard-shaped result payloads and explicit job states, including `needs_input` for runtime credential entry. Preserve CLI behavior and keep runtime credentials memory-only.

**Tech Stack:** Shared HTML/CSS/vanilla JS frontend, Python stdlib HTTP server, Node built-in `http`, existing collect/sync/config runtime logic, pytest, Node test runner.

---

### Task 1: Redesign The Shared Frontend Shell

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.css`
- Modify: `web/app.js`

- [ ] **Step 1: Write the failing frontend shape tests in existing backend-facing tests**

Add expectations in:

- `tests/test_web.py`
- `node/test/web.test.js`

The new expectations must require:

- dashboard data is rendered via structured fields rather than raw JSON payload blocks
- a separate Settings view exists in the DOM shell
- result payloads include summary, timeseries, breakdowns, and table rows

- [ ] **Step 2: Run targeted tests to verify they fail**

Run:

```bash
pytest tests/test_web.py -q
node --test node/test/web.test.js
```

Expected:

- Python web tests fail because dashboard/result payload shape is incomplete
- Node web tests fail because dashboard/result payload shape is incomplete

- [ ] **Step 3: Replace the HTML shell with dashboard-first structure**

Implement:

- Dashboard header with title, last update, and top-right action cluster
- dashboard summary cards area
- wide trend chart section
- tool/model comparison sections
- detail table section
- Settings view container, separated from dashboard
- runtime credential modal container
- lightweight job/status area instead of raw `<pre>` dumps

- [ ] **Step 4: Replace current CSS with calm-analysis-tool styling**

Implement the agreed palette and layout:

- app background `#F3F6F8`
- white surfaces
- high-contrast text
- compact control styling
- chart/table layouts with desktop-first density and responsive fallback

- [ ] **Step 5: Rebuild the frontend controller in `web/app.js`**

Implement:

- dashboard rendering from structured result payloads
- simple SVG/canvas-free chart rendering using HTML/CSS bars or inline SVG
- filters for 30-day default range and table narrowing
- navigation between Dashboard and Settings views
- action handlers for doctor/collect/sync-preview/sync
- job polling and `needs_input` handling
- session credential modal submit flow

- [ ] **Step 6: Run targeted tests and basic smoke checks**

Run:

```bash
pytest tests/test_web.py -q
node --test node/test/web.test.js
```

Expected:

- payload-shape and frontend shell tests pass

- [ ] **Step 7: Commit**

```bash
git add web/index.html web/app.css web/app.js tests/test_web.py node/test/web.test.js
git commit -m "feat: redesign web dashboard shell"
```

### Task 2: Upgrade Python Web Backend For Dashboard Payloads And Runtime Input

**Files:**
- Modify: `src/llm_usage/web.py`
- Test: `tests/test_web.py`

- [ ] **Step 1: Write the failing Python tests for dashboard payload shape and runtime input**

Add tests for:

- `load_latest_results()` returns:
  - `summary`
  - `timeseries`
  - `breakdowns`
  - `table_rows`
- collect/sync job manager supports:
  - `queued`
  - `running`
  - `needs_input`
  - `succeeded`
  - `failed`
- runtime SSH passwords are stored only in memory and not written to `.env`

- [ ] **Step 2: Run the Python tests to verify failure**

Run:

```bash
pytest tests/test_web.py -q
```

Expected:

- failures showing missing dashboard fields and missing runtime input state handling

- [ ] **Step 3: Refactor result transformation into dashboard-oriented helpers**

Implement helpers in `src/llm_usage/web.py` that:

- parse `usage_report.csv`
- compute summary totals and active-day count
- build per-day token timeseries
- aggregate tool breakdown
- aggregate model breakdown
- emit table rows at `date + tool + model` grain

- [ ] **Step 4: Add explicit job-state handling for runtime credential prompts**

Implement:

- in-memory session store for runtime credentials
- job state `needs_input`
- structured prompt payload:
  - remote alias
  - input kind `ssh_password`
  - message indicating session-only storage
- resume endpoint or equivalent job continuation hook for submitted runtime credentials

- [ ] **Step 5: Keep persistent config and runtime credentials separate**

Ensure:

- Settings save path still writes only persistent config
- runtime credential submissions never modify `.env`
- restarting the server clears runtime credential memory

- [ ] **Step 6: Run Python tests to verify pass**

Run:

```bash
pytest tests/test_web.py tests/test_cli_help.py -q
```

Expected:

- all targeted Python web tests pass

- [ ] **Step 7: Commit**

```bash
git add src/llm_usage/web.py tests/test_web.py tests/test_cli_help.py
git commit -m "feat: add python dashboard web api"
```

### Task 3: Upgrade Node Web Backend For Dashboard Payloads And Runtime Input

**Files:**
- Modify: `node/src/runtime/web.js`
- Modify: `node/src/cli/main.js`
- Test: `node/test/web.test.js`
- Test: `node/test/cli.test.js`

- [ ] **Step 1: Write the failing Node tests for dashboard payload shape and runtime input**

Add tests for:

- dashboard-shaped result helpers from `node/src/runtime/web.js`
- Node job manager states including `needs_input`
- runtime SSH password storage remains memory-only
- `llm-usage-node web` help/entry remains valid

- [ ] **Step 2: Run Node tests to verify failure**

Run:

```bash
node --test node/test/web.test.js node/test/cli.test.js
```

Expected:

- failures showing missing dashboard fields and missing runtime credential pause/resume flow

- [ ] **Step 3: Refactor Node result helpers to mirror the Python dashboard shape**

Implement helpers that:

- transform latest CSV/report content into:
  - `summary`
  - `timeseries`
  - `breakdowns`
  - `table_rows`
  - `warnings`

- [ ] **Step 4: Add Node runtime credential session handling**

Implement:

- in-memory credential cache
- explicit `needs_input` job state
- structured runtime-input payload for SSH password prompt
- continuation flow to resume collect/sync after password entry

- [ ] **Step 5: Keep the Node CLI entry aligned**

Ensure:

- `llm-usage-node web` still starts the console
- no CLI regressions in collect/sync/help behavior

- [ ] **Step 6: Run Node tests to verify pass**

Run:

```bash
node --test node/test/web.test.js node/test/cli.test.js
```

Expected:

- targeted Node tests pass

- [ ] **Step 7: Commit**

```bash
git add node/src/runtime/web.js node/src/cli/main.js node/test/web.test.js node/test/cli.test.js
git commit -m "feat: add node dashboard web api"
```

### Task 4: Integrate, Regress, And Document

**Files:**
- Modify: `README.md`
- Modify: `node/README.md`
- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/app.css`
- Modify: `tests/test_web.py`
- Modify: `node/test/web.test.js`

- [ ] **Step 1: Verify frontend/backend contract alignment**

Check:

- dashboard payload field names match in Python and Node
- runtime input payload shape is the same across backends
- Dashboard and Settings render correctly against both backends

- [ ] **Step 2: Update docs for the redesigned console**

Document:

- dashboard-first home page
- separate Settings page
- runtime SSH password prompt behavior
- session-only credential memory

- [ ] **Step 3: Run merged verification**

Run:

```bash
pytest tests/test_web.py tests/test_cli_help.py -q
node --test node/test/cli.test.js node/test/web.test.js
```

Expected:

- all targeted regression tests pass

- [ ] **Step 4: Commit**

```bash
git add README.md node/README.md web/index.html web/app.js web/app.css tests/test_web.py node/test/web.test.js
git commit -m "docs: finalize redesigned web console"
```

