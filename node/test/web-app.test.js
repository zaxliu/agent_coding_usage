import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

import {
  buildConfigSummary,
  buildSemilogTicks,
  canDismissInputRequest,
  dashboardSummaryText,
  createUiFlags,
  escapeHtml,
  formatHostHash,
  formatCompactNumber,
  describeInputRequest,
  inputRequestSubmissionValue,
  normalizeResultsPayload,
  credentialSubmissionMode,
  nextCredentialPromptJob,
  settingsPanelMode,
} from "../../web/app-state.js";

function readSource(relativePath) {
  return fs.readFileSync(new URL(relativePath, import.meta.url), "utf8").replace(/\r\n/g, "\n");
}

function matchBlock(source, pattern, message) {
  const match = source.match(pattern);
  assert.ok(match, message);
  return match[0];
}

function assertContainsAll(source, patterns, messagePrefix = "expected pattern") {
  for (const pattern of patterns) {
    assert.match(source, pattern, `${messagePrefix}: ${pattern}`);
  }
}

function assertTagWithAttrs(source, tagName, attrs) {
  const tagPattern = new RegExp(`<${tagName}\\b[^>]*>`, "gu");
  const tags = source.match(tagPattern) || [];
  assert.ok(tags.some((tag) => attrs.every((attr) => tag.includes(attr))), `expected <${tagName}> with attrs: ${attrs.join(", ")}`);
}

test("index.html exposes a single-page console shell and removes old nav/view markers", () => {
  const html = readSource("../../web/index.html");

  assert.match(html, /console-layout/u);
  assert.match(html, /rel="shortcut icon" href="\/favicon\.ico"/u);
  assert.match(html, /rel="icon" href="\/favicon\.svg" type="image\/svg\+xml"/u);
  assert.match(html, /class="sidebar console-sidebar"/u);
  assert.match(html, /class="main-panel console-main"/u);
  assert.match(html, /id="system-status"/u);
  assert.match(html, /id="config-summary"/u);
  assert.match(html, /class="panel settings-panel collapsed"[^>]*id="settings-panel"/u);
  assert.match(html, /class="panel settings-panel collapsed"[^>]*id="settings-panel"[^>]*hidden/u);
  assert.doesNotMatch(html, /data-view-target="dashboard"/u);
  assert.doesNotMatch(html, /data-view-target="settings"/u);
  assert.doesNotMatch(html, /data-view="dashboard"/u);
  assert.doesNotMatch(html, /data-view="settings"/u);
});

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
  assert.deepEqual(buildConfigSummary(null), [
    { label: "用户", value: "-" },
    { label: "时区", value: "-" },
    { label: "回看", value: "-" },
    { label: "远端", value: "0 个" },
  ]);
});

test("settingsPanelMode maps open state to DOM-ready panel state", () => {
  assert.deepEqual(settingsPanelMode(false), { className: "collapsed", hidden: true });
  assert.deepEqual(settingsPanelMode(true), { className: "expanded", hidden: false });
});

test("app.js wires config summary rendering and inline settings state without old view switching", () => {
  const js = readSource("../../web/app.js");
  const html = readSource("../../web/index.html");

  assert.match(js, /buildConfigSummary/u);
  assert.match(js, /createUiFlags/u);
  assert.match(js, /settingsPanelMode/u);
  assert.match(js, /pendingRunAction:\s*""/u);
  assert.match(js, /runConfirmSubmitting:\s*false/u);
  assert.match(js, /config-summary-list/u);
  assert.match(js, /toggle-settings/u);
  assert.match(js, /function applyTableView\(/u);
  assert.match(js, /function openColumnFilter\(/u);
  assert.match(js, /runConfirmModal:\s*document\.querySelector\("#run-confirm-modal"\)/u);
  assert.match(js, /runConfirmForm:\s*document\.querySelector\("#run-confirm-form"\)/u);
  assert.match(js, /runConfirmRemotes:\s*document\.querySelector\("#run-confirm-remotes"\)/u);
  assert.match(js, /runConfirmFeishuSection:\s*document\.querySelector\("#run-confirm-feishu-section"\)/u);
  assert.match(js, /function openRunConfirmModal\(action\)/u);
  assert.match(js, /function buildRunConfirmPayload\(\)/u);
  assert.match(js, /function resetRunConfirmState\(\)/u);
  assert.match(js, /state\.tableFilters/u);
  assert.match(js, /state\.tableSort/u);
  assert.match(js, /data-column/u);
  assert.match(js, /selected_remotes:\s*selectedRemotes/u);
  assert.match(js, /feishu_targets:\s*selectedFeishuTargets/u);
  assert.match(js, /all_feishu_targets:\s*selectAllFeishuTargets/u);
  assert.match(js, /renderRuntimeStatus\("idle", "空闲", "本地控制台已就绪"\)/u);
  assert.match(js, /function setActionRuntimeState\(action, phase = "running", detail = ""\)/u);
  assert.match(js, /action === "validate-config"[\s\S]*setActionRuntimeState\("validate-config", "running"\)/u);
  assert.match(js, /if \(action === "collect"\) \{[\s\S]*openRunConfirmModal\(action\)[\s\S]*return;/u);
  assert.match(js, /if \(action === "sync"\) \{[\s\S]*openRunConfirmModal\(action\)[\s\S]*return;/u);
  assert.match(js, /refs\.runConfirmForm\.addEventListener\("submit"/u);
  assert.match(js, /refs\.runConfirmModal\.addEventListener\("cancel", resetRunConfirmState\)/u);
  assert.match(js, /refs\.runConfirmModal\.addEventListener\("close", resetRunConfirmState\)/u);
  const staleActionBlock = matchBlock(
    js,
    /if \(!\["collect", "sync"\]\.includes\(action\)\) \{[\s\S]*?return;\n\s*\}/u,
    "expected stale run-confirm action guard",
  );
  assertContainsAll(
    staleActionBlock,
    [/resetRunConfirmState\(\)/u, /refs\.runConfirmModal\.close\(\)/u, /showFlash\("运行确认状态已失效，请重新选择。", "error"\)/u],
    "expected stale-action fail-closed behavior",
  );
  assert.doesNotMatch(staleActionBlock, /getJson\(/u);
  assert.match(js, /action === "save-config"[\s\S]*applySettingsPanelState\(false\)/u);
  assert.match(js, /action === "validate-config"[\s\S]*showFlash\("配置校验通过。", "success"\)/u);
  assert.match(html, /id="settings-toggle"/u);
  assert.doesNotMatch(js, /currentView/u);
  assert.doesNotMatch(js, /data-view-target/u);
  assert.doesNotMatch(js, /navigate\(view\)/u);
});

test("createUiFlags keeps inline settings collapsed by default", () => {
  assert.deepEqual(createUiFlags(), { settingsOpen: false });
});

test("single-page console layout exposes dedicated sidebar, main, operations, and settings hooks", () => {
  const css = readSource("../../web/app.css");
  const html = readSource("../../web/index.html");

  assert.match(css, /\.console-layout\b/u);
  assert.match(css, /\.console-sidebar\b/u);
  assert.match(css, /\.console-main\b/u);
  assert.match(css, /\.operations-bar\b/u);
  assert.match(css, /\.sidebar-actions\b/u);
  assert.match(css, /\.operations-head\b/u);
  assert.match(css, /\.sidebar-actions[\s\S]*gap:\s*18px/u);
  assert.match(css, /\.operations-head[\s\S]*margin-bottom:\s*6px/u);
  assert.match(css, /\.action-tile\b/u);
  assert.match(css, /\.action-tile\.button-primary\b/u);
  assert.match(css, /\.status-card\.is-running\b[\s\S]*animation:/u);
  assert.doesNotMatch(css, /\.status-card::after/u);
  assert.doesNotMatch(css, /\.status-card\.is-idle::after/u);
  assert.match(css, /\.settings-panel\b/u);
  assert.match(css, /\.settings-panel\[hidden\]/u);
  assert.doesNotMatch(css, /\.view\s*\{/u);
  assert.doesNotMatch(css, /\.view\.view-active\s*\{/u);
  assert.doesNotMatch(css, /\.nav\s*\{/u);
  assert.doesNotMatch(css, /\.nav-link\b/u);
  assert.match(css, /@media[\s\S]*\.sidebar-actions[\s\S]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/u);
  assert.match(html, /id="operations-bar"/u);
  assert.match(html, /class="panel compact-panel sidebar-actions" id="operations-bar"/u);
  assert.match(html, /<div class="operations-head">[\s\S]*<p class="eyebrow">操作<\/p>[\s\S]*上次执行时间/u);
  assert.doesNotMatch(html, /data-action="sync-preview"/u);
  assert.doesNotMatch(html, /<h3>执行台<\/h3>/u);
  assert.match(html, /<section class="status-stack" id="system-status">[\s\S]*最近任务/u);
  assert.doesNotMatch(html, /<span class="status-label">后端<\/span>/u);
  assert.doesNotMatch(html, /class="hero console-hero"/u);
  assert.doesNotMatch(html, /<p class="eyebrow">控制台<\/p>/u);
  assert.doesNotMatch(html, /<h2>最近 30 天<\/h2>/u);
  assert.doesNotMatch(html, /先看趋势，再看对比，最后落到明细/u);
});

test("app.css defines responsive console and dialog overflow protections", () => {
  const css = readSource("../../web/app.css");

  assert.match(css, /@media\s*\(max-width:\s*1100px\)/u);
  assert.match(css, /@media\s*\(max-width:\s*720px\)/u);
  assert.match(css, /\.console-layout[\s\S]*grid-template-columns:\s*320px minmax\(0,\s*1fr\)/u);
  assert.match(css, /\.summary-grid[\s\S]*repeat\(4,\s*minmax\(0,\s*1fr\)\)/u);
  assert.match(css, /\.summary-grid[\s\S]*margin-bottom:\s*12px/u);
  assert.match(css, /\.metric-card[\s\S]*padding:\s*14px 16px/u);
  assert.match(css, /\.metric-value[\s\S]*margin-top:\s*8px/u);
  assert.match(css, /\.chart-large[\s\S]*min-height:\s*300px/u);
  assert.match(css, /\.comparison-grid[\s\S]*repeat\(2,\s*minmax\(0,\s*1fr\)\)/u);
  assert.match(css, /\.credential-form[\s\S]*max-height:\s*calc\(100vh - 32px\)/u);
  assert.match(css, /\.credential-form[\s\S]*overflow:\s*auto/u);
  assert.match(css, /\.flashbar\.is-success/u);
  assert.match(css, /\.run-confirm-grid/u);
  assert.match(css, /\.run-confirm-list/u);
});

test("app.css allows shell sections and action rows to shrink and wrap", () => {
  const css = readSource("../../web/app.css");

  assert.match(css, /\.console-main,[\s\S]*\.console-sidebar,[\s\S]*\.panel,[\s\S]*\.metric-card[\s\S]*min-width:\s*0/u);
  assert.match(css, /\.panel-head[\s\S]*flex-wrap:\s*wrap/u);
  assert.match(css, /\.status-head[\s\S]*justify-content:\s*space-between/u);
  assert.match(css, /\.operations-head[\s\S]*justify-content:\s*space-between/u);
  assert.match(css, /\.actions[\s\S]*width:\s*100%/u);
});

test("index.html exposes responsive form hooks for settings and dialogs", () => {
  const html = readSource("../../web/index.html");

  assert.match(html, /class="form-grid settings-form-grid"/u);
  assert.match(html, /class="form-grid modal-form-grid"/u);
  assert.match(html, /class="table-sort-button" data-column="input"/u);
  assert.match(html, /class="table-filter-button" data-column="source_host_hash"/u);
  assert.match(html, /id="table-column-filter" class="table-column-filter" hidden/u);
  assert.match(html, /placeholder="筛选日期、Host、工具或模型"/u);
  assert.match(html, /data-action="validate-config" class="button-subtle settings-action-secondary"/u);
  assert.match(html, /data-action="save-config" class="button-primary settings-action-primary"/u);
});

test("app.css includes table sorting and column filter popover styling", () => {
  const css = readSource("../../web/app.css");

  assert.match(css, /\.table-sort-button/u);
  assert.match(css, /\.table-filter-button/u);
  assert.match(css, /\.table-column-filter/u);
  assert.match(css, /\.filter-option/u);
});

test("dialog markup and styles support viewport-safe modal content", () => {
  const html = readSource("../../web/index.html");
  const css = readSource("../../web/app.css");

  assertTagWithAttrs(html, "dialog", ['id="credential-modal"', 'class="credential-modal"']);
  assertTagWithAttrs(html, "dialog", ['id="run-confirm-modal"', 'class="credential-modal"']);
  assertTagWithAttrs(html, "form", ['id="run-confirm-form"', 'method="dialog"', 'class="credential-form"']);
  assertTagWithAttrs(html, "section", ['id="run-confirm-remotes"', 'class="settings-list"']);
  assertTagWithAttrs(html, "p", ['id="run-confirm-remotes-empty"']);
  assertTagWithAttrs(html, "section", ['id="run-confirm-feishu-section"', 'class="panel"']);
  assertTagWithAttrs(html, "div", ['id="run-confirm-feishu-modes"', 'class="settings-list"']);
  assertTagWithAttrs(html, "input", ['id="run-confirm-feishu-default"', 'value="default"']);
  assertTagWithAttrs(html, "input", ['id="run-confirm-feishu-all"', 'value="all"']);
  assertTagWithAttrs(html, "input", ['id="run-confirm-feishu-named-targets"', 'value="named"']);
  assertTagWithAttrs(html, "div", ['id="run-confirm-feishu-targets"', 'class="settings-list"']);
  assertTagWithAttrs(html, "button", ['id="run-confirm-submit"']);
  assertContainsAll(
    html,
    [/id="run-confirm-title"/u, /id="run-confirm-copy"/u, /data-action="collect"/u, /data-action="sync"/u, /id="run-confirm-feishu-modes"[\s\S]*id="run-confirm-feishu-targets"/u],
    "expected run-confirm modal hooks",
  );
  assert.match(css, /\.credential-modal[\s\S]*max-width:\s*calc\(100vw - 16px\)/u);
  assert.match(css, /\.credential-form[\s\S]*width:\s*min\(460px,\s*calc\(100vw - 32px\)\)/u);
  assert.match(css, /\.credential-form[\s\S]*word-break:\s*break-word/u);
  assert.match(css, /\.credential-actions[\s\S]*justify-content:\s*flex-end/u);
});

test("app.js renders run-confirm empty states and sync-specific feishu controls", () => {
  const js = readSource("../../web/app.js");

  assertContainsAll(
    js,
    [/未配置远端，将只采集本地数据。/u, /未配置 named targets，将使用默认目标。/u, /run-confirm-feishu-section/u, /run-confirm-feishu-targets/u, /data-run-remote/u, /data-run-feishu-target/u],
    "expected run-confirm rendering hooks",
  );
});

test("app.js keeps collect payload scoped to selected_remotes only", () => {
  const js = readSource("../../web/app.js");
  const collectBlock = matchBlock(
    js,
    /if \(action === "collect"\) \{[\s\S]*?setActionRuntimeState\("collect", "running"\);/u,
    "expected collect payload block in submitRunConfirm",
  );

  assert.match(collectBlock, /url = "\/api\/collect"/u);
  assert.match(collectBlock, /payload = \{\s*selected_remotes:\s*buildRunConfirmPayload\(\)\.selected_remotes\s*\}/u);
  assert.doesNotMatch(collectBlock, /feishu_targets/u);
  assert.doesNotMatch(collectBlock, /all_feishu_targets/u);
  assert.doesNotMatch(collectBlock, /confirm_sync/u);
});

test("app.js keeps sync payload scoped to the approved four fields only", () => {
  const js = readSource("../../web/app.js");
  const syncBlock = matchBlock(
    js,
    /else if \(action === "sync"\) \{[\s\S]*?setActionRuntimeState\("sync", "running"\);/u,
    "expected sync payload block in submitRunConfirm",
  );

  assert.match(syncBlock, /const syncPayload = buildRunConfirmPayload\(\)/u);
  assert.match(syncBlock, /url = "\/api\/sync"/u);
  assert.match(syncBlock, /selected_remotes:\s*syncPayload\.selected_remotes/u);
  assert.match(syncBlock, /feishu_targets:\s*syncPayload\.feishu_targets/u);
  assert.match(syncBlock, /all_feishu_targets:\s*syncPayload\.all_feishu_targets/u);
  assert.match(syncBlock, /confirm_sync:\s*true/u);
  assert.doesNotMatch(syncBlock, /\.\.\.syncPayload/u);
});

test("app.js ties default sync feishu mode to empty named-target payload fields", () => {
  const js = readSource("../../web/app.js");
  const payloadHelperBlock = matchBlock(
    js,
    /function buildRunConfirmPayload\(\) \{[\s\S]*?return \{[\s\S]*?\};\n\}/u,
    "expected buildRunConfirmPayload helper",
  );

  assert.match(payloadHelperBlock, /const selectAllFeishuTargets = refs\.runConfirmFeishuAll\?\.checked \|\| false;/u);
  assertContainsAll(
    payloadHelperBlock,
    [/refs\.runConfirmFeishuNamedTargets\?\.checked/u, /input\[data-run-feishu-target\]:checked/u, /:\s*\[\]/u, /feishu_targets:\s*selectedFeishuTargets/u, /all_feishu_targets:\s*selectAllFeishuTargets/u],
    "expected default sync feishu path",
  );
});

test("app.js guards run-confirm double submit and resets modal state on native dismissal", () => {
  const js = readSource("../../web/app.js");
  const resetBlock = matchBlock(js, /function resetRunConfirmState\(\) \{[\s\S]*?\n\}/u, "expected run-confirm reset helper");
  const submitBlock = matchBlock(js, /async function submitRunConfirm\(event\) \{[\s\S]*?\n\}/u, "expected run-confirm submit handler");

  assertContainsAll(
    resetBlock,
    [/state\.pendingRunAction = ""/u, /state\.runConfirmSubmitting = false/u, /refs\.runConfirmSubmit\.disabled = false/u],
    "expected run-confirm reset state",
  );
  assertContainsAll(
    submitBlock,
    [/if \(state\.runConfirmSubmitting\) \{/u, /state\.runConfirmSubmitting = true/u, /refs\.runConfirmSubmit\.disabled = true/u],
    "expected double-submit guard",
  );
  assertContainsAll(
    js,
    [/refs\.runConfirmModal\.addEventListener\("cancel", resetRunConfirmState\)/u, /refs\.runConfirmModal\.addEventListener\("close", resetRunConfirmState\)/u],
    "expected native dialog dismissal reset hooks",
  );
});

test("normalizeResultsPayload reads dashboard summary totals and names from current backend payload", () => {
  const normalized = normalizeResultsPayload({
    summary: {
      totals: {
        rows: 2,
        input_tokens_sum: 15,
        cache_tokens_sum: 3,
        output_tokens_sum: 7,
        total_tokens: 25,
      },
      active_days: 2,
      top_tool: { name: "codex", total_tokens: 15 },
      top_model: { name: "gpt-5", total_tokens: 15 },
      generated_at: "2026-04-07T12:00:00Z",
    },
    timeseries: [
      {
        date_local: "2026-04-06",
        input_tokens_sum: 10,
        cache_tokens_sum: 2,
        output_tokens_sum: 3,
      },
    ],
    breakdowns: {
      tools: [{ name: "codex", total_tokens: 15 }],
      models: [{ name: "gpt-5", total_tokens: 15 }],
    },
    table_rows: [
      {
        date_local: "2026-04-06",
        source_host_hash: "abcdef1234567890",
        tool: "codex",
        model: "gpt-5",
        input_tokens_sum: 10,
        cache_tokens_sum: 2,
        output_tokens_sum: 3,
      },
    ],
  });

  assert.equal(normalized.summary.total_tokens, 25);
  assert.equal(normalized.summary.active_days, 2);
  assert.equal(normalized.summary.top_tool, "codex");
  assert.equal(normalized.summary.top_model, "gpt-5");
  assert.equal(normalized.summary.generated_at, "2026-04-07T12:00:00Z");
  assert.deepEqual(normalized.timeseries, [
    {
      date: "2026-04-06",
      input: 10,
      cache: 2,
      output: 3,
    },
  ]);
  assert.deepEqual(normalized.table_rows, [
    {
      date: "2026-04-06",
      source_host_hash: "abcdef1234567890",
      tool: "codex",
      model: "gpt-5",
      input: 10,
      cache: 2,
      output: 3,
    },
  ]);
});

test("formatHostHash shortens long host hashes for table display", () => {
  assert.equal(formatHostHash(""), "-");
  assert.equal(formatHostHash("abcd"), "abcd");
  assert.equal(formatHostHash("abcdef1234567890"), "abcd…7890");
});

test("dashboardSummaryText keeps summary cards on the normalized compact-number path", () => {
  const normalized = normalizeResultsPayload({
    summary: {
      totals: {
        rows: 2,
        input_tokens_sum: 15,
        cache_tokens_sum: 3,
        output_tokens_sum: 7,
        total_tokens: 25,
      },
      active_days: 12,
      top_tool: { name: "codex", total_tokens: 15 },
      top_model: { name: "gpt-5", total_tokens: 15 },
      generated_at: "2026-04-07T12:00:00Z",
    },
    timeseries: [],
    breakdowns: {},
    table_rows: [],
  });

  assert.deepEqual(dashboardSummaryText(normalized.summary), {
    totalTokens: "25",
    activeDays: "12",
    topTool: "codex",
    topModel: "gpt-5",
    generatedAt: "2026-04-07T12:00:00Z",
  });
});

test("formatCompactNumber switches between k m b t units automatically", () => {
  assert.equal(formatCompactNumber(950), "950");
  assert.equal(formatCompactNumber(1_200), "1.2K");
  assert.equal(formatCompactNumber(2_500_000), "2.5M");
  assert.equal(formatCompactNumber(3_400_000_000), "3.4B");
  assert.equal(formatCompactNumber(5_000_000_000_000), "5T");
});

test("buildSemilogTicks returns readable tick values for a half-log chart", () => {
  assert.deepEqual(buildSemilogTicks(0), [1]);
  assert.deepEqual(buildSemilogTicks(87), [1, 10, 100]);
  assert.deepEqual(buildSemilogTicks(9_500), [1, 10, 100, 1000, 10000]);
});

test("credentialSubmissionMode treats cancel as dismiss instead of submit", () => {
  assert.equal(credentialSubmissionMode({ submitterValue: "submit" }), "submit");
  assert.equal(credentialSubmissionMode({ submitterValue: "cancel" }), "cancel");
  assert.equal(credentialSubmissionMode({ submitterValue: "" }), "submit");
});

test("describeInputRequest distinguishes confirm, ssh_password, and generic text input requests", () => {
  const confirmUi = describeInputRequest({
    kind: "confirm",
    message: "Save this temporary remote to .env?",
    choices: ["Save", "Skip"],
  });
  const passwordUi = describeInputRequest({
    kind: "ssh_password",
    message: "Enter the SSH password",
  });
  const textUi = describeInputRequest({
    kind: "ssh_host",
    message: "SSH host",
  });

  assert.equal(confirmUi.inputType, "confirm");
  assert.deepEqual(confirmUi.choices, ["Save", "Skip"]);
  assert.equal(confirmUi.submitValue, "Save");
  assert.equal(confirmUi.cancelValue, "Skip");
  assert.equal(passwordUi.inputType, "password");
  assert.deepEqual(passwordUi.choices, []);
  assert.equal(textUi.inputType, "text");
  assert.deepEqual(textUi.choices, []);
});

test("inputRequestSubmissionValue preserves confirm button values and text input values", () => {
  const confirmUi = describeInputRequest({
    kind: "confirm",
    message: "Save this temporary remote to .env?",
    choices: ["Save", "Skip"],
  });
  const textUi = describeInputRequest({
    kind: "ssh_host",
    message: "SSH host",
  });

  assert.equal(inputRequestSubmissionValue({ descriptor: confirmUi, submitterValue: "Save" }), "Save");
  assert.equal(inputRequestSubmissionValue({ descriptor: confirmUi, submitterValue: "Skip" }), "Skip");
  assert.equal(inputRequestSubmissionValue({ descriptor: textUi, fieldValue: "host-a" }), "host-a");
});

test("canDismissInputRequest only allows session password prompts to stay hidden", () => {
  assert.equal(canDismissInputRequest({ kind: "ssh_password" }), true);
  assert.equal(canDismissInputRequest({ kind: "ssh_host" }), false);
  assert.equal(canDismissInputRequest({ kind: "confirm" }), false);
});

test("nextCredentialPromptJob keeps a dismissed job hidden until another pending job appears", () => {
  const dismissedJob = {
    id: "job-1",
    status: "needs_input",
    input_request: {
      kind: "ssh_password",
      remote_alias: "SERVER_A",
      message: "Provide password",
    },
  };
  const completedJob = {
    id: "job-2",
    status: "succeeded",
    input_request: null,
  };
  const nextPendingJob = {
    id: "job-3",
    status: "needs_input",
    input_request: {
      kind: "ssh_password",
      remote_alias: "SERVER_B",
      message: "Provide password",
    },
  };

  assert.equal(nextCredentialPromptJob([dismissedJob], "")?.id, "job-1");
  assert.equal(nextCredentialPromptJob([dismissedJob], "job-1"), null);
  assert.equal(nextCredentialPromptJob([dismissedJob, completedJob], "job-1"), null);
  assert.equal(nextCredentialPromptJob([dismissedJob, completedJob, nextPendingJob], "job-1")?.id, "job-3");
  assert.equal(nextCredentialPromptJob([], "job-1"), null);
});

test("escapeHtml neutralizes HTML special characters", () => {
  assert.equal(escapeHtml("<script>alert(1)</script>"), "&lt;script&gt;alert(1)&lt;/script&gt;");
  assert.equal(escapeHtml('a&b"c'), "a&amp;b&quot;c");
  assert.equal(escapeHtml("plain text"), "plain text");
  assert.equal(escapeHtml(""), "");
});
