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
  formatCompactNumber,
  describeInputRequest,
  inputRequestSubmissionValue,
  normalizeResultsPayload,
  credentialSubmissionMode,
  nextCredentialPromptJob,
  settingsPanelMode,
} from "../../web/app-state.js";

test("index.html exposes a single-page console shell and removes old nav/view markers", () => {
  const html = fs.readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");

  assert.match(html, /console-layout/u);
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
  const js = fs.readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
  const html = fs.readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");

  assert.match(js, /buildConfigSummary/u);
  assert.match(js, /createUiFlags/u);
  assert.match(js, /settingsPanelMode/u);
  assert.match(js, /config-summary-list/u);
  assert.match(js, /toggle-settings/u);
  assert.match(html, /id="settings-toggle"/u);
  assert.doesNotMatch(js, /currentView/u);
  assert.doesNotMatch(js, /data-view-target/u);
  assert.doesNotMatch(js, /navigate\(view\)/u);
});

test("createUiFlags keeps inline settings collapsed by default", () => {
  assert.deepEqual(createUiFlags(), { settingsOpen: false });
});

test("single-page console layout exposes dedicated sidebar, main, operations, and settings hooks", () => {
  const css = fs.readFileSync(new URL("../../web/app.css", import.meta.url), "utf8");
  const html = fs.readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");

  assert.match(css, /\.console-layout\b/u);
  assert.match(css, /\.console-sidebar\b/u);
  assert.match(css, /\.console-main\b/u);
  assert.match(css, /\.operations-bar\b/u);
  assert.match(css, /\.settings-panel\b/u);
  assert.match(css, /\.settings-panel\[hidden\]/u);
  assert.doesNotMatch(css, /\.view\s*\{/u);
  assert.doesNotMatch(css, /\.view\.view-active\s*\{/u);
  assert.doesNotMatch(css, /\.nav\s*\{/u);
  assert.doesNotMatch(css, /\.nav-link\b/u);
  assert.match(css, /@media[\s\S]*\.actions\s*\{[\s\S]*justify-content:\s*flex-start;/u);
  assert.match(html, /id="operations-bar"/u);
  assert.match(html, /class="hero-actions operations-bar"/u);
});

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

test("app.css allows shell sections and action rows to shrink and wrap", () => {
  const css = fs.readFileSync(new URL("../../web/app.css", import.meta.url), "utf8");

  assert.match(css, /\.console-main,[\s\S]*\.console-sidebar,[\s\S]*\.panel,[\s\S]*\.metric-card[\s\S]*min-width:\s*0/u);
  assert.match(css, /\.hero[\s\S]*flex-wrap:\s*wrap/u);
  assert.match(css, /\.panel-head[\s\S]*flex-wrap:\s*wrap/u);
  assert.match(css, /\.actions[\s\S]*width:\s*100%/u);
});

test("index.html exposes responsive form hooks for settings and dialogs", () => {
  const html = fs.readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");

  assert.match(html, /class="form-grid settings-form-grid"/u);
  assert.match(html, /class="form-grid modal-form-grid"/u);
});

test("dialog markup and styles support viewport-safe modal content", () => {
  const html = fs.readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");
  const css = fs.readFileSync(new URL("../../web/app.css", import.meta.url), "utf8");

  assert.match(html, /id="credential-modal" class="credential-modal"/u);
  assert.match(css, /\.credential-modal[\s\S]*max-width:\s*calc\(100vw - 16px\)/u);
  assert.match(css, /\.credential-form[\s\S]*width:\s*min\(460px,\s*calc\(100vw - 32px\)\)/u);
  assert.match(css, /\.credential-form[\s\S]*word-break:\s*break-word/u);
  assert.match(css, /\.credential-actions[\s\S]*justify-content:\s*flex-end/u);
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
      tool: "codex",
      model: "gpt-5",
      input: 10,
      cache: 2,
      output: 3,
    },
  ]);
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
  assert.deepEqual(buildSemilogTicks(0), [0, 1]);
  assert.deepEqual(buildSemilogTicks(87), [0, 1, 10, 100]);
  assert.deepEqual(buildSemilogTicks(9_500), [0, 1, 10, 100, 1000, 10000]);
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
