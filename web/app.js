import {
  canDismissInputRequest,
  buildSemilogTicks,
  credentialSubmissionMode,
  describeInputRequest,
  formatCompactNumber,
  inputRequestSubmissionValue,
  nextCredentialPromptJob,
  normalizeResultsPayload,
} from "./app-state.js";

const state = {
  runtime: null,
  config: null,
  results: null,
  jobs: [],
  currentView: "dashboard",
  pendingCredentialJobId: "",
  dismissedCredentialJobId: "",
  pendingCredentialRequest: null,
};

const refs = {
  flashbar: document.querySelector("#flashbar"),
  runtimeBackend: document.querySelector("#runtime-backend"),
  runtimeMeta: document.querySelector("#runtime-meta"),
  latestRunTitle: document.querySelector("#latest-run-title"),
  latestRunMeta: document.querySelector("#latest-run-meta"),
  generatedAt: document.querySelector("#generated-at"),
  metricTotal: document.querySelector("#metric-total"),
  metricDays: document.querySelector("#metric-days"),
  metricTool: document.querySelector("#metric-tool"),
  metricModel: document.querySelector("#metric-model"),
  trendChart: document.querySelector("#trend-chart"),
  toolBreakdown: document.querySelector("#tool-breakdown"),
  modelBreakdown: document.querySelector("#model-breakdown"),
  resultsTable: document.querySelector("#results-table"),
  tableFilter: document.querySelector("#table-filter"),
  jobsList: document.querySelector("#jobs-list"),
  remotesList: document.querySelector("#remotes-list"),
  credentialModal: document.querySelector("#credential-modal"),
  credentialTitle: document.querySelector("#credential-title"),
  credentialCopy: document.querySelector("#credential-copy"),
  credentialField: document.querySelector("#credential-field"),
  credentialFieldLabel: document.querySelector("#credential-field-label"),
  credentialValue: document.querySelector("#credential-value"),
  credentialCancel: document.querySelector("#credential-cancel"),
  credentialForm: document.querySelector("#credential-form"),
  credentialSubmit: document.querySelector("#credential-submit"),
};

async function getJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "content-type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    const message = payload.error || payload.message || `Request failed: ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

function fmtNumber(value) {
  return formatCompactNumber(value);
}

function fmtTime(value) {
  if (!value) {
    return "等待数据";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString("zh-CN");
}

function showFlash(message, tone = "") {
  refs.flashbar.textContent = message;
  refs.flashbar.className = `flashbar${tone ? ` is-${tone}` : ""}`;
}

function hideFlash() {
  refs.flashbar.className = "flashbar hidden";
  refs.flashbar.textContent = "";
}

function navigate(view) {
  state.currentView = view;
  for (const node of document.querySelectorAll(".view")) {
    node.classList.toggle("view-active", node.dataset.view === view);
  }
  for (const node of document.querySelectorAll(".nav-link")) {
    node.classList.toggle("is-active", node.dataset.viewTarget === view);
  }
}

function settingsPayload() {
  return {
    basic: {
      ORG_USERNAME: document.querySelector("#org-username").value,
      HASH_SALT: document.querySelector("#hash-salt").value,
      TIMEZONE: document.querySelector("#timezone").value,
      LOOKBACK_DAYS: document.querySelector("#lookback-days").value,
    },
    cursor: {},
    feishu_default: {
      FEISHU_APP_TOKEN: document.querySelector("#feishu-app-token").value,
      FEISHU_TABLE_ID: document.querySelector("#feishu-table-id").value,
      FEISHU_APP_ID: document.querySelector("#feishu-app-id").value,
      FEISHU_APP_SECRET: document.querySelector("#feishu-app-secret").value,
      FEISHU_BOT_TOKEN: document.querySelector("#feishu-bot-token").value,
    },
    feishu_targets: state.config?.feishu_targets || [],
    remotes: state.config?.remotes || [],
    raw_env: state.config?.raw_env || [],
  };
}

function renderSummary(summary = {}) {
  refs.metricTotal.textContent = fmtNumber(summary.total_tokens);
  refs.metricDays.textContent = fmtNumber(summary.active_days);
  refs.metricTool.textContent = summary.top_tool || "-";
  refs.metricModel.textContent = summary.top_model || "-";
  refs.generatedAt.textContent = fmtTime(summary.generated_at);
}

function renderTrendChart(timeseries = []) {
  if (!timeseries.length) {
    refs.trendChart.innerHTML = `<div class="empty-state">当前范围内还没有报表数据。</div>`;
    return;
  }

  const width = 860;
  const height = 260;
  const paddingLeft = 64;
  const paddingRight = 24;
  const paddingTop = 18;
  const paddingBottom = 30;
  const values = timeseries.flatMap((item) => [Number(item.input || 0), Number(item.cache || 0), Number(item.output || 0)]);
  const maxValue = Math.max(...values, 1);
  const ticks = buildSemilogTicks(maxValue);
  const maxLog = Math.log10(maxValue + 1);
  const plotWidth = width - paddingLeft - paddingRight;
  const plotHeight = height - paddingTop - paddingBottom;
  const xStep = timeseries.length === 1 ? plotWidth : plotWidth / (timeseries.length - 1);
  const yForValue = (value) => {
    const scaled = Math.log10(Number(value || 0) + 1) / maxLog;
    return height - paddingBottom - scaled * plotHeight;
  };

  const buildLine = (key) =>
    timeseries
      .map((item, index) => {
        const x = paddingLeft + index * xStep;
        const y = yForValue(item[key] || 0);
        return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
      })
      .join(" ");

  const labels = timeseries
    .map((item, index) => {
      if (timeseries.length > 8 && index % Math.ceil(timeseries.length / 6) !== 0 && index !== timeseries.length - 1) {
        return "";
      }
      const x = paddingLeft + index * xStep;
      return `<text x="${x}" y="${height - 6}" text-anchor="middle" fill="#5B6B79" font-size="11">${item.date.slice(5)}</text>`;
    })
    .join("");
  const yTicks = ticks
    .map((tick) => {
      const y = yForValue(tick);
      return `
        <line x1="${paddingLeft}" y1="${y}" x2="${width - paddingRight}" y2="${y}" stroke="#D9E2EA" stroke-width="1"></line>
        <text x="${paddingLeft - 10}" y="${y + 4}" text-anchor="end" fill="#5B6B79" font-size="11">${fmtNumber(tick)}</text>
      `;
    })
    .join("");

  refs.trendChart.innerHTML = `
    <svg class="chart-svg" viewBox="0 0 ${width} ${height}" aria-label="Token trend">
      <rect x="0" y="0" width="${width}" height="${height}" fill="transparent"></rect>
      ${yTicks}
      <path d="${buildLine("input")}" fill="none" stroke="#1F6FEB" stroke-width="3" stroke-linecap="round"></path>
      <path d="${buildLine("cache")}" fill="none" stroke="#7B8EA3" stroke-width="3" stroke-linecap="round"></path>
      <path d="${buildLine("output")}" fill="none" stroke="#0F9D94" stroke-width="3" stroke-linecap="round"></path>
      ${labels}
    </svg>
    <div class="chart-legend">
      <span><span class="legend-dot legend-input"></span>输入</span>
      <span><span class="legend-dot legend-cache"></span>缓存</span>
      <span><span class="legend-dot legend-output"></span>输出</span>
    </div>
  `;
}

function renderBreakdown(target, items = [], className = "") {
  if (!items.length) {
    target.innerHTML = `<div class="empty-state">还没有可对比的数据。</div>`;
    return;
  }
  const max = Math.max(...items.map((item) => Number(item.total || 0)), 1);
  target.innerHTML = items
    .slice(0, 8)
    .map(
      (item) => `
        <div class="rank-item">
          <div class="rank-head">
            <span class="rank-label">${item.label}</span>
            <strong>${fmtNumber(item.total)}</strong>
          </div>
          <div class="rank-bar">
            <div class="rank-fill ${className}" style="width:${(Number(item.total || 0) / max) * 100}%"></div>
          </div>
        </div>
      `,
    )
    .join("");
}

function renderTable(rows = []) {
  const query = refs.tableFilter.value.trim().toLowerCase();
  const visible = rows.filter((row) => {
    if (!query) {
      return true;
    }
    return [row.date, row.tool, row.model].some((value) => String(value || "").toLowerCase().includes(query));
  });
  refs.resultsTable.innerHTML = visible.length
    ? visible
        .map(
          (row) => `
            <tr>
              <td>${row.date || "-"}</td>
              <td>${row.tool || "-"}</td>
              <td>${row.model || "-"}</td>
              <td>${fmtNumber(row.input)}</td>
              <td>${fmtNumber(row.cache)}</td>
              <td>${fmtNumber(row.output)}</td>
            </tr>
          `,
        )
        .join("")
    : `<tr><td colspan="6"><div class="empty-state">没有匹配当前筛选条件的数据。</div></td></tr>`;
}

function renderJobs(jobs = []) {
  refs.jobsList.innerHTML = jobs.length
    ? jobs
        .map((job) => {
          const result = job.result || {};
          const summary = result.row_count ? `${fmtNumber(result.row_count)} 行` : job.error || "暂无摘要";
          const statusClass = String(job.status || "").replace(/_/g, "-");
          return `
            <article class="job-item">
              <div class="job-head">
                <strong>${job.type}</strong>
                <span class="pill ${statusClass}">${job.status}</span>
              </div>
              <div class="job-meta">${summary}</div>
              <div class="job-meta">${fmtTime(job.updated_at || job.created_at)}</div>
            </article>
          `;
        })
        .join("")
    : `<div class="empty-state">还没有任务。</div>`;
}

function renderRemotes(remotes = []) {
  refs.remotesList.innerHTML = remotes.length
    ? remotes
        .map(
          (remote) => `
            <article class="remote-item">
              <div class="remote-head">
                <strong>${remote.alias}</strong>
                <span class="pill">${remote.source_label || `${remote.ssh_user}@${remote.ssh_host}`}</span>
              </div>
              <div class="remote-meta">${remote.ssh_user}@${remote.ssh_host}:${remote.ssh_port}</div>
            </article>
          `,
        )
        .join("")
    : `<div class="empty-state">还没有配置远端来源。</div>`;
}

function maybePromptForCredential(jobs = []) {
  const pending = nextCredentialPromptJob(jobs, state.dismissedCredentialJobId);
  if (!pending) {
    state.pendingCredentialRequest = null;
    if (refs.credentialModal.open) {
      refs.credentialModal.close();
    }
    const waitingIds = new Set(jobs.filter((job) => job.status === "needs_input" && job.input_request).map((job) => job.id));
    if (!waitingIds.has(state.dismissedCredentialJobId)) {
      state.dismissedCredentialJobId = "";
      state.pendingCredentialJobId = "";
    }
    return;
  }
  if (state.pendingCredentialJobId === pending.id && refs.credentialModal.open) {
    return;
  }
  const descriptor = describeInputRequest(pending.input_request);
  state.pendingCredentialJobId = pending.id;
  state.dismissedCredentialJobId = "";
  state.pendingCredentialRequest = pending.input_request;
  refs.credentialTitle.textContent = descriptor.title;
  refs.credentialCopy.textContent =
    descriptor.message || `Provide input for ${pending.input_request.remote_alias || "current job"}. Stored only for this session.`;
  refs.credentialField.hidden = descriptor.inputType === "confirm";
  refs.credentialFieldLabel.textContent = descriptor.fieldLabel || "";
  refs.credentialValue.type = descriptor.inputType === "password" ? "password" : "text";
  refs.credentialValue.inputMode = pending.input_request.kind === "ssh_port" ? "numeric" : "text";
  refs.credentialValue.placeholder = descriptor.placeholder || "";
  refs.credentialValue.value = "";
  refs.credentialCancel.value = descriptor.cancelValue || "cancel";
  refs.credentialCancel.textContent = descriptor.cancelLabel;
  refs.credentialSubmit.value = descriptor.submitValue || "submit";
  refs.credentialSubmit.textContent = descriptor.submitLabel;
  refs.credentialModal.showModal();
}

function renderDashboard(results) {
  const normalized = normalizeResultsPayload(results);
  renderSummary(normalized.summary);
  renderTrendChart(normalized.timeseries);
  renderBreakdown(refs.toolBreakdown, normalized.breakdowns?.tools || []);
  renderBreakdown(refs.modelBreakdown, normalized.breakdowns?.models || [], "model");
  renderTable(normalized.table_rows || []);
  if ((normalized.warnings || []).length) {
    showFlash(normalized.warnings[0], "warning");
  } else {
    hideFlash();
  }
}

async function refreshRuntime() {
  state.runtime = await getJson("/api/runtime");
  refs.runtimeBackend.textContent = `${state.runtime.backend || "unknown"} ${state.runtime.version || ""}`.trim();
  refs.runtimeMeta.textContent = state.runtime.env_path || "No env path";
}

async function refreshConfig() {
  state.config = await getJson("/api/config");
  document.querySelector("#org-username").value = state.config.basic?.ORG_USERNAME || "";
  document.querySelector("#hash-salt").value = state.config.basic?.HASH_SALT || "";
  document.querySelector("#timezone").value = state.config.basic?.TIMEZONE || "";
  document.querySelector("#lookback-days").value = state.config.basic?.LOOKBACK_DAYS || "";
  document.querySelector("#feishu-app-token").value = state.config.feishu_default?.FEISHU_APP_TOKEN || "";
  document.querySelector("#feishu-table-id").value = state.config.feishu_default?.FEISHU_TABLE_ID || "";
  document.querySelector("#feishu-app-id").value = state.config.feishu_default?.FEISHU_APP_ID || "";
  document.querySelector("#feishu-app-secret").value = state.config.feishu_default?.FEISHU_APP_SECRET || "";
  document.querySelector("#feishu-bot-token").value = state.config.feishu_default?.FEISHU_BOT_TOKEN || "";
  renderRemotes(state.config.remotes || []);
}

async function refreshResults() {
  state.results = await getJson("/api/results/latest");
  renderDashboard(state.results);
}

async function refreshJobs() {
  const jobsPayload = await getJson("/api/jobs");
  state.jobs = jobsPayload.jobs || [];
  renderJobs(state.jobs);
  const latest = state.jobs[0];
  refs.latestRunTitle.textContent = latest ? `${latest.type} · ${latest.status}` : "还没有任务";
  refs.latestRunMeta.textContent = latest ? fmtTime(latest.updated_at || latest.created_at) : "先运行诊断或采集";
  maybePromptForCredential(state.jobs);
}

async function runAction(action) {
  try {
    if (action === "save-config") {
      await getJson("/api/config", {
        method: "PUT",
        body: JSON.stringify(settingsPayload()),
      });
      showFlash("Settings saved.");
      await refreshConfig();
      return;
    }
    if (action === "validate-config") {
      const result = await getJson("/api/config/validate", {
        method: "POST",
        body: JSON.stringify(settingsPayload()),
      });
      if (result.ok) {
        showFlash("Settings are valid.");
      } else {
        showFlash(result.errors?.[0] || "Validation failed.", "error");
      }
      return;
    }

    let url = "/api/doctor";
    let payload = {};
    if (action === "collect") {
      url = "/api/collect";
    } else if (action === "sync-preview") {
      url = "/api/sync/preview";
    } else if (action === "sync") {
      url = "/api/sync";
      payload = { confirm_sync: true };
    }
    const job = await getJson(url, { method: "POST", body: JSON.stringify(payload) });
    showFlash(`Started ${job.type}.`);
    await refreshJobs();
    await refreshResults();
  } catch (error) {
    showFlash(error.message, "error");
  }
}

async function submitCredential(event) {
  event.preventDefault();
  const request = state.pendingCredentialRequest || {};
  const descriptor = describeInputRequest(request);
  const mode = credentialSubmissionMode({ submitterValue: event.submitter?.value || "" });
  if (descriptor.inputType !== "confirm" && mode === "cancel") {
    if (canDismissInputRequest(request)) {
      state.dismissedCredentialJobId = state.pendingCredentialJobId;
      showFlash("Credential prompt dismissed.", "warning");
    } else {
      state.dismissedCredentialJobId = "";
      showFlash("This input is still required for the current job.", "warning");
    }
    state.pendingCredentialJobId = "";
    state.pendingCredentialRequest = null;
    refs.credentialModal.close();
    return;
  }
  if (!state.pendingCredentialJobId) {
    refs.credentialModal.close();
    return;
  }
  try {
    const value = inputRequestSubmissionValue({
      descriptor,
      submitterValue: event.submitter?.value || "",
      fieldValue: refs.credentialValue.value,
    });
    await getJson(`/api/jobs/${state.pendingCredentialJobId}/input`, {
      method: "POST",
      body: JSON.stringify({ value }),
    });
    state.dismissedCredentialJobId = "";
    state.pendingCredentialJobId = "";
    state.pendingCredentialRequest = null;
    refs.credentialModal.close();
    showFlash("Runtime credential submitted.");
    await refreshJobs();
  } catch (error) {
    showFlash(error.message, "error");
  }
}

for (const button of document.querySelectorAll("[data-action]")) {
  button.addEventListener("click", () => runAction(button.dataset.action));
}

for (const button of document.querySelectorAll("[data-view-target]")) {
  button.addEventListener("click", () => navigate(button.dataset.viewTarget));
}

refs.tableFilter.addEventListener("input", () => renderTable(normalizeResultsPayload(state.results).table_rows || []));
refs.credentialForm.addEventListener("submit", submitCredential);

setInterval(() => {
  refreshJobs().catch(() => {});
}, 3000);

await Promise.all([refreshRuntime(), refreshConfig(), refreshResults(), refreshJobs()]);
