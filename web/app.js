import { credentialSubmissionMode, nextCredentialPromptJob, normalizeResultsPayload } from "./app-state.js";

const state = {
  runtime: null,
  config: null,
  results: null,
  jobs: [],
  currentView: "dashboard",
  pendingCredentialJobId: "",
  dismissedCredentialJobId: "",
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
  credentialValue: document.querySelector("#credential-value"),
  credentialForm: document.querySelector("#credential-form"),
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
  const parsed = Number(value || 0);
  return Number.isFinite(parsed) ? parsed.toLocaleString() : "-";
}

function fmtTime(value) {
  if (!value) {
    return "Waiting for data";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString();
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
    refs.trendChart.innerHTML = `<div class="empty-state">No report data for the current range.</div>`;
    return;
  }

  const width = 860;
  const height = 260;
  const padding = 26;
  const values = timeseries.flatMap((item) => [Number(item.input || 0), Number(item.cache || 0), Number(item.output || 0)]);
  const maxValue = Math.max(...values, 1);
  const xStep = timeseries.length === 1 ? width - padding * 2 : (width - padding * 2) / (timeseries.length - 1);

  const buildLine = (key) =>
    timeseries
      .map((item, index) => {
        const x = padding + index * xStep;
        const y = height - padding - (Number(item[key] || 0) / maxValue) * (height - padding * 2);
        return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
      })
      .join(" ");

  const labels = timeseries
    .map((item, index) => {
      if (timeseries.length > 8 && index % Math.ceil(timeseries.length / 6) !== 0 && index !== timeseries.length - 1) {
        return "";
      }
      const x = padding + index * xStep;
      return `<text x="${x}" y="${height - 6}" text-anchor="middle" fill="#5B6B79" font-size="11">${item.date.slice(5)}</text>`;
    })
    .join("");

  refs.trendChart.innerHTML = `
    <svg class="chart-svg" viewBox="0 0 ${width} ${height}" aria-label="Token trend">
      <rect x="0" y="0" width="${width}" height="${height}" fill="transparent"></rect>
      ${[0, 0.25, 0.5, 0.75, 1]
        .map((tick) => {
          const y = height - padding - tick * (height - padding * 2);
          return `<line x1="${padding}" y1="${y}" x2="${width - padding}" y2="${y}" stroke="#D9E2EA" stroke-width="1"></line>`;
        })
        .join("")}
      <path d="${buildLine("input")}" fill="none" stroke="#1F6FEB" stroke-width="3" stroke-linecap="round"></path>
      <path d="${buildLine("cache")}" fill="none" stroke="#7B8EA3" stroke-width="3" stroke-linecap="round"></path>
      <path d="${buildLine("output")}" fill="none" stroke="#0F9D94" stroke-width="3" stroke-linecap="round"></path>
      ${labels}
    </svg>
    <div class="chart-legend">
      <span><span class="legend-dot legend-input"></span>Input</span>
      <span><span class="legend-dot legend-cache"></span>Cache</span>
      <span><span class="legend-dot legend-output"></span>Output</span>
    </div>
  `;
}

function renderBreakdown(target, items = [], className = "") {
  if (!items.length) {
    target.innerHTML = `<div class="empty-state">No comparison data yet.</div>`;
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
    : `<tr><td colspan="6"><div class="empty-state">No rows match the current filter.</div></td></tr>`;
}

function renderJobs(jobs = []) {
  refs.jobsList.innerHTML = jobs.length
    ? jobs
        .map((job) => {
          const result = job.result || {};
          const summary = result.row_count ? `${fmtNumber(result.row_count)} rows` : job.error || "No summary";
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
    : `<div class="empty-state">No jobs yet.</div>`;
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
    : `<div class="empty-state">No remote sources configured.</div>`;
}

function maybePromptForCredential(jobs = []) {
  const pending = nextCredentialPromptJob(jobs, state.dismissedCredentialJobId);
  if (!pending) {
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
  state.pendingCredentialJobId = pending.id;
  state.dismissedCredentialJobId = "";
  refs.credentialTitle.textContent = pending.input_request.kind === "ssh_password" ? "SSH Password Required" : "Input Required";
  refs.credentialCopy.textContent = pending.input_request.message || `Provide input for ${pending.input_request.remote_alias || "current job"}. Stored only for this session.`;
  refs.credentialValue.value = "";
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
  refs.latestRunTitle.textContent = latest ? `${latest.type} · ${latest.status}` : "No jobs yet";
  refs.latestRunMeta.textContent = latest ? fmtTime(latest.updated_at || latest.created_at) : "Start with Doctor or Collect";
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
  const mode = credentialSubmissionMode({ submitterValue: event.submitter?.value || "" });
  if (mode === "cancel") {
    state.dismissedCredentialJobId = state.pendingCredentialJobId;
    state.pendingCredentialJobId = "";
    refs.credentialModal.close();
    showFlash("Credential prompt dismissed.", "warning");
    return;
  }
  if (!state.pendingCredentialJobId) {
    refs.credentialModal.close();
    return;
  }
  try {
    await getJson(`/api/jobs/${state.pendingCredentialJobId}/input`, {
      method: "POST",
      body: JSON.stringify({ value: refs.credentialValue.value }),
    });
    state.dismissedCredentialJobId = "";
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
