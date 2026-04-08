import {
  canDismissInputRequest,
  buildSemilogTicks,
  buildConfigSummary,
  dashboardSummaryText,
  createUiFlags,
  credentialSubmissionMode,
  describeInputRequest,
  escapeHtml,
  formatHostHash,
  formatCompactNumber,
  inputRequestSubmissionValue,
  nextCredentialPromptJob,
  normalizeResultsPayload,
  settingsPanelMode,
} from "./app-state.js";

const state = {
  ...createUiFlags(),
  runtime: null,
  config: null,
  results: null,
  jobs: [],
  tableFilters: {},
  tableSort: { column: "date", direction: "asc" },
  activeTableFilterColumn: "",
  pendingCredentialJobId: "",
  dismissedCredentialJobId: "",
  pendingCredentialRequest: null,
  editingRemoteIndex: -1,
  editingFeishuTargetIndex: -1,
};

const refs = {
  flashbar: document.querySelector("#flashbar"),
  runtimeStatusCard: document.querySelector("#runtime-status-card"),
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
  tableColumnFilter: document.querySelector("#table-column-filter"),
  tableColumnFilterTitle: document.querySelector("#table-column-filter-title"),
  tableColumnFilterOptions: document.querySelector("#table-column-filter-options"),
  tableColumnFilterClose: document.querySelector("#table-column-filter-close"),
  tableColumnFilterAll: document.querySelector("#table-column-filter-all"),
  tableColumnFilterClear: document.querySelector("#table-column-filter-clear"),
  tableColumnSortAsc: document.querySelector("#table-column-sort-asc"),
  tableColumnSortDesc: document.querySelector("#table-column-sort-desc"),
  jobsList: document.querySelector("#jobs-list"),
  remotesList: document.querySelector("#remotes-list"),
  feishuTargetsList: document.querySelector("#feishu-targets-list"),
  configSummaryList: document.querySelector("#config-summary-list"),
  settingsPanel: document.querySelector("#settings-panel"),
  settingsToggle: document.querySelector("#settings-toggle"),
  credentialModal: document.querySelector("#credential-modal"),
  credentialTitle: document.querySelector("#credential-title"),
  credentialCopy: document.querySelector("#credential-copy"),
  credentialField: document.querySelector("#credential-field"),
  credentialFieldLabel: document.querySelector("#credential-field-label"),
  credentialValue: document.querySelector("#credential-value"),
  credentialCancel: document.querySelector("#credential-cancel"),
  credentialForm: document.querySelector("#credential-form"),
  credentialSubmit: document.querySelector("#credential-submit"),
  remoteEditModal: document.querySelector("#remote-edit-modal"),
  remoteEditForm: document.querySelector("#remote-edit-form"),
  remoteEditTitle: document.querySelector("#remote-edit-title"),
  feishuTargetEditModal: document.querySelector("#feishu-target-edit-modal"),
  feishuTargetEditForm: document.querySelector("#feishu-target-edit-form"),
  feishuTargetEditTitle: document.querySelector("#feishu-target-edit-title"),
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

function applySettingsPanelState(open = state.settingsOpen) {
  state.settingsOpen = open;
  const mode = settingsPanelMode(open);
  refs.settingsPanel.classList.remove("collapsed", "expanded");
  refs.settingsPanel.classList.add(mode.className);
  refs.settingsPanel.hidden = mode.hidden;
  if (refs.settingsToggle) {
    refs.settingsToggle.textContent = open ? "收起设置" : "编辑设置";
  }
}

function renderConfigSummary(config = {}) {
  const summaryItems = buildConfigSummary(config);
  summaryItems.push({
    label: ".env",
    value: state.runtime?.env_path || "-",
  });
  refs.configSummaryList.innerHTML = summaryItems
    .map((item) => `<div class="summary-pair"><span>${escapeHtml(item.label)}</span><strong>${escapeHtml(item.value)}</strong></div>`)
    .join("");
}

function renderRuntimeStatus(status = "idle", title = "空闲", meta = "可以开始新的任务") {
  const className = String(status || "idle").replace(/_/g, "-");
  refs.runtimeStatusCard.classList.remove("is-idle", "is-running", "is-needs-input", "is-failed", "is-succeeded");
  refs.runtimeStatusCard.classList.add(`is-${className}`);
  refs.runtimeBackend.textContent = title;
  refs.runtimeMeta.textContent = meta;
}

function setActionRuntimeState(action, phase = "running", detail = "") {
  const messages = {
    init: {
      running: ["正在初始化", "检查配置文件 -> 准备本地控制台 -> 等待结果"],
      success: ["初始化完成", detail || "已准备好设置面板"],
      error: ["初始化失败", detail || "请检查当前配置与文件权限"],
    },
    "validate-config": {
      running: ["正在校验配置", "读取表单 -> 请求校验接口 -> 等待结果"],
      success: ["配置校验完成", detail || "当前表单可用于保存"],
      error: ["配置校验失败", detail || "请根据提示修正配置"],
    },
    "save-config": {
      running: ["正在保存设置", "读取表单 -> 写入配置文件 -> 刷新摘要"],
      success: ["设置已保存", detail || "设置面板已自动收起"],
      error: ["保存设置失败", detail || "请检查配置项后重试"],
    },
    collect: {
      running: ["正在采集数据", "准备任务 -> 请求采集接口 -> 等待任务状态"],
      success: ["采集任务已提交", detail || "请等待任务状态更新"],
      error: ["采集启动失败", detail || "请检查采集配置后重试"],
    },
    "sync-preview": {
      running: ["正在同步预览", "准备任务 -> 请求预览接口 -> 等待任务状态"],
      success: ["同步预览已提交", detail || "请等待任务状态更新"],
      error: ["同步预览启动失败", detail || "请检查同步配置后重试"],
    },
    sync: {
      running: ["正在执行同步", "准备确认参数 -> 请求同步接口 -> 等待任务状态"],
      success: ["同步任务已提交", detail || "请等待任务状态更新"],
      error: ["同步启动失败", detail || "请检查同步配置后重试"],
    },
    doctor: {
      running: ["正在执行诊断", "准备任务 -> 请求诊断接口 -> 等待任务状态"],
      success: ["诊断任务已提交", detail || "请等待任务状态更新"],
      error: ["诊断启动失败", detail || "请检查环境后重试"],
    },
  };
  const entry = messages[action]?.[phase];
  if (!entry) {
    return;
  }
  renderRuntimeStatus(phase === "error" ? "failed" : phase === "success" ? "succeeded" : "running", entry[0], entry[1]);
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
  const dashboardSummary = dashboardSummaryText(summary);
  refs.metricTotal.textContent = dashboardSummary.totalTokens;
  refs.metricDays.textContent = dashboardSummary.activeDays;
  refs.metricTool.textContent = dashboardSummary.topTool;
  refs.metricModel.textContent = dashboardSummary.topModel;
  refs.generatedAt.textContent = fmtTime(dashboardSummary.generatedAt);
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

function currentTableRows() {
  return normalizeResultsPayload(state.results).table_rows || [];
}

function columnLabel(column) {
  const labels = {
    date: "日期",
    source_host_hash: "Host",
    tool: "工具",
    model: "模型",
    input: "输入",
    cache: "缓存",
    output: "输出",
  };
  return labels[column] || column;
}

function columnDisplayValue(column, value) {
  if (column === "source_host_hash") {
    return formatHostHash(value);
  }
  return String(value || "-");
}

function sortedColumnValues(column, rows = currentTableRows()) {
  const values = [...new Set(rows.map((row) => String(row[column] || "").trim()).filter(Boolean))];
  values.sort((left, right) => {
    if (column === "date") {
      return left.localeCompare(right);
    }
    return columnDisplayValue(column, left).localeCompare(columnDisplayValue(column, right), "zh-CN");
  });
  return values;
}

function closeColumnFilter() {
  state.activeTableFilterColumn = "";
  refs.tableColumnFilter.hidden = true;
  refs.tableColumnFilterOptions.innerHTML = "";
}

function renderColumnFilterOptions(column) {
  const values = sortedColumnValues(column);
  const selected = state.tableFilters[column] || new Set();
  refs.tableColumnFilterTitle.textContent = `${columnLabel(column)}筛选`;
  refs.tableColumnFilterOptions.innerHTML = values.length
    ? values
        .map(
          (value) => `
            <label class="filter-option">
              <input type="checkbox" data-column-value="${escapeHtml(value)}" ${selected.has(value) ? "checked" : ""}>
              <span>${escapeHtml(columnDisplayValue(column, value))}</span>
            </label>
          `,
        )
        .join("")
    : `<div class="empty-state empty-state-compact">当前列没有可筛选值。</div>`;

  for (const input of refs.tableColumnFilterOptions.querySelectorAll("input[data-column-value]")) {
    input.addEventListener("change", () => {
      const next = new Set(state.tableFilters[column] || []);
      const rawValue = input.getAttribute("data-column-value") || "";
      if (input.checked) {
        next.add(rawValue);
      } else {
        next.delete(rawValue);
      }
      if (next.size) {
        state.tableFilters[column] = next;
      } else {
        delete state.tableFilters[column];
      }
      applyTableView();
    });
  }
}

function openColumnFilter(column) {
  state.activeTableFilterColumn = column;
  refs.tableColumnFilter.hidden = false;
  renderColumnFilterOptions(column);
}

function sortRows(rows) {
  const { column, direction } = state.tableSort;
  const multiplier = direction === "desc" ? -1 : 1;
  return [...rows].sort((left, right) => {
    const leftValue = left[column];
    const rightValue = right[column];
    if (["input", "cache", "output"].includes(column)) {
      return (Number(leftValue || 0) - Number(rightValue || 0)) * multiplier;
    }
    return String(leftValue || "").localeCompare(String(rightValue || ""), "zh-CN") * multiplier;
  });
}

function updateSortButtons() {
  for (const button of document.querySelectorAll(".table-sort-button")) {
    button.classList.remove("is-asc", "is-desc");
    if (button.dataset.column === state.tableSort.column) {
      button.classList.add(state.tableSort.direction === "desc" ? "is-desc" : "is-asc");
    }
  }
}

function applyTableView() {
  const query = refs.tableFilter.value.trim().toLowerCase();
  let rows = currentTableRows().filter((row) => {
    for (const [column, values] of Object.entries(state.tableFilters)) {
      if (values instanceof Set && values.size && !values.has(String(row[column] || "").trim())) {
        return false;
      }
    }
    if (!query) {
      return true;
    }
    return [row.date, row.source_host_hash, row.tool, row.model].some((value) => String(value || "").toLowerCase().includes(query));
  });
  rows = sortRows(rows);
  updateSortButtons();
  renderTable(rows);
}

function renderTable(rows = []) {
  refs.resultsTable.innerHTML = rows.length
    ? rows
        .map(
          (row) => `
            <tr>
              <td>${escapeHtml(row.date || "-")}</td>
              <td>${escapeHtml(formatHostHash(row.source_host_hash))}</td>
              <td>${escapeHtml(row.tool || "-")}</td>
              <td>${escapeHtml(row.model || "-")}</td>
              <td>${fmtNumber(row.input)}</td>
              <td>${fmtNumber(row.cache)}</td>
              <td>${fmtNumber(row.output)}</td>
            </tr>
          `,
        )
        .join("")
    : `<tr><td colspan="7"><div class="empty-state">没有匹配当前筛选条件的数据。</div></td></tr>`;
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
          (remote, index) => `
            <article class="remote-item">
              <div class="remote-head">
                <strong>${escapeHtml(remote.alias)}</strong>
                <span class="pill">${escapeHtml(remote.source_label || `${remote.ssh_user}@${remote.ssh_host}`)}</span>
              </div>
              <div class="remote-meta">${escapeHtml(remote.ssh_user)}@${escapeHtml(remote.ssh_host)}:${remote.ssh_port}${remote.use_sshpass ? " (sshpass)" : ""}</div>
              <div class="item-actions">
                <button class="button-subtle button-small" data-action="edit-remote" data-index="${index}">编辑</button>
                <button class="button-subtle button-small button-danger" data-action="delete-remote" data-index="${index}">删除</button>
              </div>
            </article>
          `,
        )
        .join("")
    : `<div class="empty-state empty-state-compact">还没有配置远端来源。</div>`;
  for (const btn of refs.remotesList.querySelectorAll("[data-action]")) {
    btn.addEventListener("click", () => handleRemoteAction(btn.dataset.action, Number(btn.dataset.index)));
  }
}

function renderFeishuTargets(targets = []) {
  refs.feishuTargetsList.innerHTML = targets.length
    ? targets
        .map(
          (target, index) => `
            <article class="remote-item">
              <div class="remote-head">
                <strong>${escapeHtml(target.name)}</strong>
                <span class="pill">${escapeHtml(target.app_token ? target.app_token.slice(0, 16) + "..." : "未配置")}</span>
              </div>
              <div class="remote-meta">table: ${escapeHtml(target.table_id || "-")} | app: ${escapeHtml(target.app_id || "-")}</div>
              <div class="item-actions">
                <button class="button-subtle button-small" data-action="edit-feishu-target" data-index="${index}">编辑</button>
                <button class="button-subtle button-small button-danger" data-action="delete-feishu-target" data-index="${index}">删除</button>
              </div>
            </article>
          `,
        )
        .join("")
    : `<div class="empty-state empty-state-compact">还没有配置飞书命名目标。</div>`;
  for (const btn of refs.feishuTargetsList.querySelectorAll("[data-action]")) {
    btn.addEventListener("click", () => handleFeishuTargetAction(btn.dataset.action, Number(btn.dataset.index)));
  }
}

function openRemoteEditModal(remote = null, index = -1) {
  state.editingRemoteIndex = index;
  refs.remoteEditTitle.textContent = remote ? `编辑远端: ${remote.alias}` : "新增远端";
  document.querySelector("#remote-edit-alias").value = remote?.alias || "";
  document.querySelector("#remote-edit-ssh-host").value = remote?.ssh_host || "";
  document.querySelector("#remote-edit-ssh-user").value = remote?.ssh_user || "";
  document.querySelector("#remote-edit-ssh-port").value = remote?.ssh_port || 22;
  document.querySelector("#remote-edit-source-label").value = remote?.source_label || "";
  document.querySelector("#remote-edit-use-sshpass").checked = remote?.use_sshpass || false;
  document.querySelector("#remote-edit-claude-paths").value = (remote?.claude_log_paths || []).join(",");
  document.querySelector("#remote-edit-codex-paths").value = (remote?.codex_log_paths || []).join(",");
  document.querySelector("#remote-edit-copilot-cli-paths").value = (remote?.copilot_cli_log_paths || []).join(",");
  document.querySelector("#remote-edit-copilot-vscode-paths").value = (remote?.copilot_vscode_session_paths || []).join(",");
  refs.remoteEditModal.showModal();
}

function collectRemoteFromModal() {
  const splitPaths = (val) => val.split(",").map((s) => s.trim()).filter(Boolean);
  return {
    alias: document.querySelector("#remote-edit-alias").value.trim(),
    ssh_host: document.querySelector("#remote-edit-ssh-host").value.trim(),
    ssh_user: document.querySelector("#remote-edit-ssh-user").value.trim(),
    ssh_port: parseInt(document.querySelector("#remote-edit-ssh-port").value, 10) || 22,
    source_label: document.querySelector("#remote-edit-source-label").value.trim(),
    use_sshpass: document.querySelector("#remote-edit-use-sshpass").checked,
    claude_log_paths: splitPaths(document.querySelector("#remote-edit-claude-paths").value),
    codex_log_paths: splitPaths(document.querySelector("#remote-edit-codex-paths").value),
    copilot_cli_log_paths: splitPaths(document.querySelector("#remote-edit-copilot-cli-paths").value),
    copilot_vscode_session_paths: splitPaths(document.querySelector("#remote-edit-copilot-vscode-paths").value),
  };
}

function handleRemoteAction(action, index) {
  const remotes = state.config?.remotes || [];
  if (action === "edit-remote") {
    openRemoteEditModal(remotes[index], index);
  } else if (action === "delete-remote") {
    if (!confirm(`确定要删除远端 "${remotes[index]?.alias}" 吗？`)) return;
    remotes.splice(index, 1);
    state.config.remotes = remotes;
    renderRemotes(remotes);
    saveCurrentConfig();
  }
}

function openFeishuTargetEditModal(target = null, index = -1) {
  state.editingFeishuTargetIndex = index;
  refs.feishuTargetEditTitle.textContent = target ? `编辑飞书目标: ${target.name}` : "新增飞书目标";
  document.querySelector("#feishu-target-edit-name").value = target?.name || "";
  document.querySelector("#feishu-target-edit-name").readOnly = target !== null;
  document.querySelector("#feishu-target-edit-app-token").value = target?.app_token || "";
  document.querySelector("#feishu-target-edit-table-id").value = target?.table_id || "";
  document.querySelector("#feishu-target-edit-app-id").value = target?.app_id || "";
  document.querySelector("#feishu-target-edit-app-secret").value = target?.app_secret || "";
  document.querySelector("#feishu-target-edit-bot-token").value = target?.bot_token || "";
  refs.feishuTargetEditModal.showModal();
}

function collectFeishuTargetFromModal() {
  return {
    name: document.querySelector("#feishu-target-edit-name").value.trim(),
    app_token: document.querySelector("#feishu-target-edit-app-token").value.trim(),
    table_id: document.querySelector("#feishu-target-edit-table-id").value.trim(),
    app_id: document.querySelector("#feishu-target-edit-app-id").value.trim(),
    app_secret: document.querySelector("#feishu-target-edit-app-secret").value.trim(),
    bot_token: document.querySelector("#feishu-target-edit-bot-token").value.trim(),
  };
}

function handleFeishuTargetAction(action, index) {
  const targets = state.config?.feishu_targets || [];
  if (action === "edit-feishu-target") {
    openFeishuTargetEditModal(targets[index], index);
  } else if (action === "delete-feishu-target") {
    if (!confirm(`确定要删除飞书目标 "${targets[index]?.name}" 吗？`)) return;
    targets.splice(index, 1);
    state.config.feishu_targets = targets;
    renderFeishuTargets(targets);
    saveCurrentConfig();
  }
}

async function saveCurrentConfig() {
  try {
    const result = await getJson("/api/config", {
      method: "PUT",
      body: JSON.stringify(settingsPayload()),
    });
    if (!result.ok) {
      showFlash(result.errors?.[0] || "保存失败。", "error");
      return false;
    }
    showFlash("配置已保存。");
    await refreshConfig();
    return true;
  } catch (error) {
    showFlash(error.message, "error");
    return false;
  }
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
  renderConfigSummary(state.config);
  if (!state.jobs.length) {
    renderRuntimeStatus("idle", "空闲", "本地控制台已就绪");
  }
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
  renderConfigSummary(state.config);
  renderRemotes(state.config.remotes || []);
  renderFeishuTargets(state.config.feishu_targets || []);
}

async function refreshResults() {
  state.results = await getJson("/api/results/latest");
  renderDashboard(state.results);
  if (state.activeTableFilterColumn) {
    renderColumnFilterOptions(state.activeTableFilterColumn);
  }
}

async function refreshJobs() {
  const jobsPayload = await getJson("/api/jobs");
  state.jobs = jobsPayload.jobs || [];
  renderJobs(state.jobs);
  const latest = state.jobs[0];
  refs.latestRunTitle.textContent = latest ? `${latest.type} · ${latest.status}` : "还没有任务";
  refs.latestRunMeta.textContent = latest ? fmtTime(latest.updated_at || latest.created_at) : "先运行诊断或采集";
  if (!latest) {
    renderRuntimeStatus("idle", "空闲", "本地控制台已就绪");
  } else if (latest.status === "needs_input") {
    renderRuntimeStatus("needs_input", "等待输入", latest.input_request?.message || `${latest.type} 需要当前会话输入`);
  } else if (latest.status === "failed") {
    renderRuntimeStatus("failed", "任务失败", latest.error || `${latest.type} 执行失败`);
  } else if (latest.status === "succeeded") {
    renderRuntimeStatus("succeeded", "最近完成", `${latest.type} -> 执行完成 -> ${fmtTime(latest.updated_at || latest.created_at)}`);
  } else {
    renderRuntimeStatus("running", "运行中", `${latest.type} -> 正在执行 -> ${fmtTime(latest.updated_at || latest.created_at)}`);
  }
  maybePromptForCredential(state.jobs);
}

async function runAction(action) {
  try {
    if (action === "toggle-settings") {
      applySettingsPanelState(!state.settingsOpen);
      return;
    }
    if (action === "init") {
      setActionRuntimeState("init", "running");
      const result = await getJson("/api/init", { method: "POST", body: "{}" });
      if (result.created_env) {
        showFlash("初始化完成，已创建配置文件。");
      } else {
        showFlash("已就绪，配置文件已存在。");
      }
      await Promise.all([refreshRuntime(), refreshConfig()]);
      applySettingsPanelState(true);
      setActionRuntimeState("init", "success", "配置文件已就绪，设置面板已展开");
      return;
    }
    if (action === "add-remote") {
      openRemoteEditModal(null, -1);
      return;
    }
    if (action === "add-feishu-target") {
      openFeishuTargetEditModal(null, -1);
      return;
    }
    if (action === "save-config") {
      setActionRuntimeState("save-config", "running");
      await getJson("/api/config", {
        method: "PUT",
        body: JSON.stringify(settingsPayload()),
      });
      showFlash("设置已保存。", "success");
      await refreshConfig();
      applySettingsPanelState(false);
      setActionRuntimeState("save-config", "success", "配置已写入，设置面板已收起");
      return;
    }
    if (action === "validate-config") {
      setActionRuntimeState("validate-config", "running");
      const result = await getJson("/api/config/validate", {
        method: "POST",
        body: JSON.stringify(settingsPayload()),
      });
      if (result.ok) {
        showFlash("配置校验通过。", "success");
        setActionRuntimeState("validate-config", "success", "表单检查通过，可以直接保存");
      } else {
        showFlash(result.errors?.[0] || "配置校验失败。", "error");
        setActionRuntimeState("validate-config", "error", result.errors?.[0] || "请修正配置后重新校验");
      }
      return;
    }

    let url = "/api/doctor";
    let payload = {};
    if (action === "collect") {
      url = "/api/collect";
      setActionRuntimeState("collect", "running");
    } else if (action === "sync-preview") {
      url = "/api/sync/preview";
      setActionRuntimeState("sync-preview", "running");
    } else if (action === "sync") {
      url = "/api/sync";
      payload = { confirm_sync: true };
      setActionRuntimeState("sync", "running");
    } else {
      setActionRuntimeState("doctor", "running");
    }
    const job = await getJson(url, { method: "POST", body: JSON.stringify(payload) });
    showFlash(`Started ${job.type}.`);
    setActionRuntimeState(action, "success");
    await refreshJobs();
    await refreshResults();
  } catch (error) {
    setActionRuntimeState(action, "error", error.message);
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

applySettingsPanelState();
refs.tableFilter.addEventListener("input", applyTableView);
refs.credentialForm.addEventListener("submit", submitCredential);

for (const button of document.querySelectorAll(".table-filter-button")) {
  button.addEventListener("click", () => openColumnFilter(button.dataset.column || ""));
}

for (const button of document.querySelectorAll(".table-sort-button")) {
  button.addEventListener("click", () => {
    const column = button.dataset.column || "";
    if (!column) {
      return;
    }
    if (state.tableSort.column === column) {
      state.tableSort.direction = state.tableSort.direction === "asc" ? "desc" : "asc";
    } else {
      state.tableSort = { column, direction: "desc" };
    }
    applyTableView();
  });
}

refs.tableColumnFilterClose.addEventListener("click", closeColumnFilter);
refs.tableColumnFilterAll.addEventListener("click", () => {
  const column = state.activeTableFilterColumn;
  if (!column) {
    return;
  }
  state.tableFilters[column] = new Set(sortedColumnValues(column));
  renderColumnFilterOptions(column);
  applyTableView();
});
refs.tableColumnFilterClear.addEventListener("click", () => {
  const column = state.activeTableFilterColumn;
  if (!column) {
    return;
  }
  delete state.tableFilters[column];
  renderColumnFilterOptions(column);
  applyTableView();
});
refs.tableColumnSortAsc.addEventListener("click", () => {
  const column = state.activeTableFilterColumn;
  if (!column) {
    return;
  }
  state.tableSort = { column, direction: "asc" };
  applyTableView();
});
refs.tableColumnSortDesc.addEventListener("click", () => {
  const column = state.activeTableFilterColumn;
  if (!column) {
    return;
  }
  state.tableSort = { column, direction: "desc" };
  applyTableView();
});

refs.remoteEditForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitterValue = event.submitter?.value || "";
  if (submitterValue === "cancel") {
    refs.remoteEditModal.close();
    return;
  }
  const remote = collectRemoteFromModal();
  if (!remote.ssh_host || !remote.ssh_user) {
    showFlash("SSH Host 和 SSH User 为必填项。", "error");
    return;
  }
  if (!remote.alias) {
    remote.alias = `${remote.ssh_user}_${remote.ssh_host}`.replace(/[^A-Za-z0-9]/g, "_").toUpperCase();
  }
  if (!remote.source_label) {
    remote.source_label = `${remote.ssh_user}@${remote.ssh_host}`;
  }
  const remotes = state.config?.remotes || [];
  const prevRemotes = [...remotes];
  if (state.editingRemoteIndex >= 0) {
    remotes[state.editingRemoteIndex] = remote;
  } else {
    remotes.push(remote);
  }
  state.config.remotes = remotes;
  const ok = await saveCurrentConfig();
  if (ok) {
    refs.remoteEditModal.close();
  } else {
    state.config.remotes = prevRemotes;
  }
});

refs.feishuTargetEditForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitterValue = event.submitter?.value || "";
  if (submitterValue === "cancel") {
    refs.feishuTargetEditModal.close();
    return;
  }
  const target = collectFeishuTargetFromModal();
  if (!target.name) {
    showFlash("飞书目标名称为必填项。", "error");
    return;
  }
  const normalizedName = target.name.toLowerCase();
  const targets = state.config?.feishu_targets || [];
  if (state.editingFeishuTargetIndex >= 0) {
    targets[state.editingFeishuTargetIndex] = target;
  } else {
    if (targets.some((t) => t.name.toLowerCase() === normalizedName)) {
      showFlash(`飞书目标 "${target.name}" 已存在（不区分大小写）。`, "error");
      return;
    }
    targets.push(target);
  }
  const prevTargets = [...state.config.feishu_targets];
  state.config.feishu_targets = targets;
  const ok = await saveCurrentConfig();
  if (ok) {
    refs.feishuTargetEditModal.close();
  } else {
    state.config.feishu_targets = prevTargets;
  }
});

setInterval(() => {
  refreshJobs().catch(() => {});
}, 3000);

await Promise.all([refreshRuntime(), refreshConfig(), refreshResults(), refreshJobs()]);
applyTableView();
