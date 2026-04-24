import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import process from "node:process";
import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import { readFile } from "node:fs/promises";

import { aggregateEvents } from "../core/aggregation.js";
import { hashSourceHost, hashUser } from "../core/identity.js";
import { toFeishuFields } from "../core/privacy.js";
import { collectLocalUsage, probeLocalUsage } from "../collectors/local.js";
import { buildCursorCollector } from "../collectors/cursor-dashboard.js";
import { maybeCaptureCursorToken } from "./cursor-login.js";
import {
  fetchFirstTableId,
  fetchTenantAccessToken,
  FeishuBitableClient,
} from "./feishu.js";
import {
  getEnv,
  getEnvPath,
  getReportsDir,
  getRuntimeStatePath,
  intEnv,
  loadDotenv,
  prepareRuntimePaths,
  readEnvFile,
  repoRoot,
} from "./env.js";
import { resolveFeishuTargetsFromEnv, selectFeishuTargets } from "./feishu-targets.js";
import { parseRemoteConfigsFromEnv } from "./remotes.js";
import { writeCsvReport } from "./reporting.js";

const thisDir = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(thisDir, "../../../../web");

const BASIC_KEYS = ["ORG_USERNAME", "HASH_SALT", "TIMEZONE", "LOOKBACK_DAYS"];
const FEISHU_KEYS = ["FEISHU_APP_TOKEN", "FEISHU_TABLE_ID", "FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_BOT_TOKEN"];
const CURSOR_KEYS = [
  "CURSOR_LOG_PATHS",
  "CURSOR_WEB_SESSION_TOKEN",
  "CURSOR_WEB_WORKOS_ID",
  "CURSOR_DASHBOARD_BASE_URL",
  "CURSOR_DASHBOARD_TEAM_ID",
  "CURSOR_DASHBOARD_PAGE_SIZE",
  "CURSOR_DASHBOARD_TIMEOUT_SEC",
];
const ADVANCED_KEYS = ["CLAUDE_LOG_PATHS", "CODEX_LOG_PATHS", "COPILOT_CLI_LOG_PATHS", "COPILOT_VSCODE_SESSION_PATHS", "CLINE_VSCODE_SESSION_PATHS"];

function packageVersion() {
  const packageJsonPath = path.resolve(thisDir, "../../package.json");
  return JSON.parse(fs.readFileSync(packageJsonPath, "utf8")).version || "0.0.0";
}

function renderEnvValue(value) {
  const text = String(value ?? "");
  if (!text) {
    return '""';
  }
  if (![...text].some((char) => /\s/u.test(char) || `#"'`.includes(char))) {
    return text;
  }
  return `"${text.replaceAll("\\", "\\\\").replaceAll('"', '\\"')}"`;
}

function envMap() {
  return Object.fromEntries(readEnvFile(getEnvPath()));
}

function rawEnvEntries(values) {
  const managed = new Set([...BASIC_KEYS, ...FEISHU_KEYS, ...CURSOR_KEYS, ...ADVANCED_KEYS, "FEISHU_TARGETS"]);
  return Object.keys(values)
    .sort()
    .filter((key) => !managed.has(key) && !key.startsWith("REMOTE_") && !/^FEISHU_[A-Z0-9_]+_(APP_TOKEN|TABLE_ID|APP_ID|APP_SECRET|BOT_TOKEN)$/u.test(key))
    .map((key) => ({ key, value: values[key] }));
}

function splitAliases(raw) {
  return String(raw || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => item.toUpperCase());
}

function envFlag(value) {
  return ["1", "true", "yes", "y", "on"].includes(String(value || "").trim().toLowerCase());
}

function parseCsvReport(csvText) {
  const text = String(csvText || "").trim();
  if (!text) {
    return [];
  }
  const [headerLine, ...lines] = text.split(/\r?\n/u);
  const headers = headerLine.split(",");
  return lines.filter(Boolean).map((line) => {
    const values = line.split(",");
    return Object.fromEntries(headers.map((header, index) => [header, values[index] || ""]));
  });
}

function toNumber(value) {
  const parsed = Number(value || 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function sumRowMetrics(rows) {
  return rows.reduce(
    (totals, row) => {
      totals.raw_rows += 1;
      totals.input_tokens_sum += toNumber(row.input_tokens_sum);
      totals.cache_tokens_sum += toNumber(row.cache_tokens_sum);
      totals.output_tokens_sum += toNumber(row.output_tokens_sum);
      if (row.date_local) {
        totals.active_days.add(row.date_local);
      }
      if (row.tool) {
        totals.tools.add(row.tool);
      }
      if (row.model) {
        totals.models.add(row.model);
      }
      return totals;
    },
    {
      raw_rows: 0,
      input_tokens_sum: 0,
      cache_tokens_sum: 0,
      output_tokens_sum: 0,
      active_days: new Set(),
      tools: new Set(),
      models: new Set(),
    },
  );
}

function groupRows(rows, keyFields) {
  const buckets = new Map();
  for (const row of rows) {
    const key = keyFields.map((field) => String(row[field] || "")).join("\u0000");
    const current = buckets.get(key) || {
      row_count: 0,
      input_tokens_sum: 0,
      cache_tokens_sum: 0,
      output_tokens_sum: 0,
      sample: row,
      updated_at: String(row.updated_at || ""),
    };
    current.row_count += 1;
    current.input_tokens_sum += toNumber(row.input_tokens_sum);
    current.cache_tokens_sum += toNumber(row.cache_tokens_sum);
    current.output_tokens_sum += toNumber(row.output_tokens_sum);
    if (String(row.updated_at || "") > current.updated_at) {
      current.updated_at = String(row.updated_at || "");
      current.sample = row;
    }
    buckets.set(key, current);
  }
  return [...buckets.values()]
    .map((bucket) => ({
      ...bucket.sample,
      row_count: bucket.row_count,
      input_tokens_sum: bucket.input_tokens_sum,
      cache_tokens_sum: bucket.cache_tokens_sum,
      output_tokens_sum: bucket.output_tokens_sum,
      total_tokens: bucket.input_tokens_sum + bucket.cache_tokens_sum + bucket.output_tokens_sum,
      updated_at: bucket.updated_at,
    }))
    .sort((left, right) =>
      [left.date_local || "", left.tool || "", String(left.model || "")].join("\u0000").localeCompare(
        [right.date_local || "", right.tool || "", String(right.model || "")].join("\u0000"),
      ),
    );
}

function buildDashboardBreakdown(rows, key) {
  const buckets = new Map();
  for (const row of rows) {
    const name = String(row[key] || "");
    const current = buckets.get(name) || {
      name,
      row_count: 0,
      input_tokens_sum: 0,
      cache_tokens_sum: 0,
      output_tokens_sum: 0,
      total_tokens: 0,
    };
    current.row_count += toNumber(row.row_count || 1);
    current.input_tokens_sum += toNumber(row.input_tokens_sum);
    current.cache_tokens_sum += toNumber(row.cache_tokens_sum);
    current.output_tokens_sum += toNumber(row.output_tokens_sum);
    current.total_tokens += toNumber(row.input_tokens_sum) + toNumber(row.cache_tokens_sum) + toNumber(row.output_tokens_sum);
    buckets.set(name, current);
  }
  return [...buckets.values()].sort((left, right) => {
    if (right.total_tokens !== left.total_tokens) {
      return right.total_tokens - left.total_tokens;
    }
    return left.name.localeCompare(right.name);
  });
}

function buildLatestResultsPayload(rows, { csvPath, generatedAt, warnings = [] } = {}) {
  const tableRows = groupRows(rows, ["date_local", "source_host_hash", "tool", "model"]);
  const totals = sumRowMetrics(rows);
  const timeseriesBuckets = new Map();
  for (const row of tableRows) {
    const current = timeseriesBuckets.get(row.date_local) || {
      date_local: row.date_local,
      row_count: 0,
      input_tokens_sum: 0,
      cache_tokens_sum: 0,
      output_tokens_sum: 0,
      total_tokens: 0,
    };
    current.row_count += row.row_count || 1;
    current.input_tokens_sum += toNumber(row.input_tokens_sum);
    current.cache_tokens_sum += toNumber(row.cache_tokens_sum);
    current.output_tokens_sum += toNumber(row.output_tokens_sum);
    current.total_tokens += toNumber(row.total_tokens);
    timeseriesBuckets.set(row.date_local, current);
  }
  const timeseries = [...timeseriesBuckets.values()].sort((left, right) => left.date_local.localeCompare(right.date_local));
  const toolBreakdowns = buildDashboardBreakdown(tableRows, "tool");
  const modelBreakdowns = buildDashboardBreakdown(tableRows, "model");
  return {
    ok: true,
    csv_path: csvPath,
    generated_at: generatedAt,
    warnings: [...warnings],
    summary: {
      totals: {
        rows: rows.length,
        input_tokens_sum: totals.input_tokens_sum,
        cache_tokens_sum: totals.cache_tokens_sum,
        output_tokens_sum: totals.output_tokens_sum,
        total_tokens: totals.input_tokens_sum + totals.cache_tokens_sum + totals.output_tokens_sum,
      },
      active_days: totals.active_days.size,
      top_tool: toolBreakdowns[0] ? { name: toolBreakdowns[0].name, total_tokens: toolBreakdowns[0].total_tokens } : { name: "", total_tokens: 0 },
      top_model: modelBreakdowns[0] ? { name: modelBreakdowns[0].name, total_tokens: modelBreakdowns[0].total_tokens } : { name: "", total_tokens: 0 },
      generated_at: generatedAt,
    },
    timeseries,
    breakdowns: {
      tools: toolBreakdowns,
      models: modelBreakdowns,
    },
    table_rows: tableRows,
    rows,
  };
}

export function loadConfigPayload() {
  const values = envMap();
  const targets = resolveFeishuTargetsFromEnv(values).filter((target) => target.name !== "default");
  return {
    basic: Object.fromEntries(BASIC_KEYS.map((key) => [key, values[key] || ""])),
    cursor: Object.fromEntries(CURSOR_KEYS.map((key) => [key, values[key] || ""])),
    feishu_default: Object.fromEntries(FEISHU_KEYS.map((key) => [key, values[key] || ""])),
    feishu_targets: targets.map((target) => ({
      name: target.name,
      app_token: target.appToken,
      table_id: target.tableId,
      app_id: target.appId,
      app_secret: target.appSecret,
      bot_token: target.botToken,
      use_sshpass: envFlag(values[`REMOTE_${target.name.toUpperCase()}_USE_SSHPASS`]),
    })),
    remotes: parseRemoteConfigsFromEnv(values).map((remote) => ({
      ...remote,
      use_sshpass: envFlag(values[`REMOTE_${remote.alias}_USE_SSHPASS`]),
    })),
    raw_env: rawEnvEntries(values),
    reports_dir: getReportsDir(),
    env_path: getEnvPath(),
  };
}

export function validateConfigPayload(payload) {
  const errors = [];
  const warnings = [];
  const names = new Set();
  for (const target of payload.feishu_targets || []) {
    const name = String(target.name || "").trim();
    if (!/^[a-z0-9_]+$/u.test(name) || name === "default") {
      errors.push(`invalid feishu target name: ${JSON.stringify(name)}`);
    } else if (names.has(name)) {
      errors.push(`duplicate feishu target name: ${name}`);
    } else {
      names.add(name);
    }
  }
  for (const remote of payload.remotes || []) {
    if (!String(remote.alias || "").trim()) {
      errors.push("Remote alias is required");
    }
    if (!String(remote.ssh_host || "").trim()) {
      errors.push(`remote ${remote.alias || "<new>"}: SSH host is required`);
    }
    if (!String(remote.ssh_user || "").trim()) {
      errors.push(`remote ${remote.alias || "<new>"}: SSH user is required`);
    }
  }
  if (payload.basic?.ORG_USERNAME && !payload.basic?.HASH_SALT) {
    warnings.push("HASH_SALT is empty; collect/sync will fail until set");
  }
  return { ok: errors.length === 0, errors, warnings };
}

export function writeConfigPayload(payload) {
  const validation = validateConfigPayload(payload);
  if (!validation.ok) {
    return validation;
  }
  const out = new Map();
  for (const item of payload.raw_env || []) {
    const key = String(item.key || "").trim().toUpperCase();
    if (key && !key.startsWith("REMOTE_")) {
      out.set(key, String(item.value || ""));
    }
  }
  for (const [group, keys] of [
    ["basic", BASIC_KEYS],
    ["cursor", CURSOR_KEYS],
    ["feishu_default", FEISHU_KEYS],
  ]) {
    for (const key of keys) {
      out.set(key, String(payload[group]?.[key] || ""));
    }
  }
  const targetNames = [];
  for (const target of payload.feishu_targets || []) {
    const normalized = String(target.name || "").trim().toLowerCase();
    targetNames.push(normalized);
    const prefix = `FEISHU_${normalized.toUpperCase()}_`;
    out.set(`${prefix}APP_TOKEN`, String(target.app_token || ""));
    out.set(`${prefix}TABLE_ID`, String(target.table_id || ""));
    out.set(`${prefix}APP_ID`, String(target.app_id || ""));
    out.set(`${prefix}APP_SECRET`, String(target.app_secret || ""));
    out.set(`${prefix}BOT_TOKEN`, String(target.bot_token || ""));
  }
  if (targetNames.length) {
    out.set("FEISHU_TARGETS", targetNames.join(","));
  }
  const aliases = [];
  for (const remote of payload.remotes || []) {
    const alias = String(remote.alias || "").trim().toUpperCase();
    aliases.push(alias);
    const prefix = `REMOTE_${alias}_`;
    out.set(`${prefix}SSH_HOST`, String(remote.ssh_host || ""));
    out.set(`${prefix}SSH_USER`, String(remote.ssh_user || ""));
    out.set(`${prefix}SSH_PORT`, String(remote.ssh_port || 22));
    out.set(`${prefix}LABEL`, String(remote.source_label || `${remote.ssh_user}@${remote.ssh_host}`));
    out.set(`${prefix}CLAUDE_LOG_PATHS`, (remote.claude_log_paths || []).join(","));
    out.set(`${prefix}CODEX_LOG_PATHS`, (remote.codex_log_paths || []).join(","));
    out.set(`${prefix}COPILOT_CLI_LOG_PATHS`, (remote.copilot_cli_log_paths || []).join(","));
    out.set(`${prefix}COPILOT_VSCODE_SESSION_PATHS`, (remote.copilot_vscode_session_paths || []).join(","));
    out.set(`${prefix}CLINE_VSCODE_SESSION_PATHS`, (remote.cline_vscode_session_paths || []).join(","));
    out.set(`${prefix}USE_SSHPASS`, remote.use_sshpass ? "1" : "0");
  }
  out.set("REMOTE_HOSTS", aliases.join(","));
  const lines = [...out.entries()].map(([key, value]) => `${key}=${renderEnvValue(value)}`);
  fs.mkdirSync(path.dirname(getEnvPath()), { recursive: true });
  fs.writeFileSync(getEnvPath(), `${lines.join("\n")}\n`, "utf8");
  loadDotenv(getEnvPath());
  return { ok: true, errors: [], warnings: validation.warnings };
}

export function loadLatestResults() {
  const csvPath = path.join(getReportsDir(), "usage_report.csv");
  if (!fs.existsSync(csvPath)) {
    return buildLatestResultsPayload([], { csvPath, generatedAt: null, warnings: [] });
  }
  const rows = parseCsvReport(fs.readFileSync(csvPath, "utf8"));
  return buildLatestResultsPayload(rows, {
    csvPath,
    generatedAt: new Date(fs.statSync(csvPath).mtimeMs).toISOString(),
    warnings: [],
  });
}

function buildTerminalHostLabels() {
  const username = getEnv("ORG_USERNAME");
  const salt = getEnv("HASH_SALT");
  if (!username || !salt) {
    return {};
  }
  const labels = { [hashSourceHost(username, "local", salt)]: "local" };
  for (const config of parseRemoteConfigsFromEnv(process.env)) {
    labels[hashSourceHost(username, config.source_label, salt)] = config.source_label;
  }
  return labels;
}

function remoteCollectionWarnings() {
  const remotes = parseRemoteConfigsFromEnv(process.env);
  return remotes.length ? [`remote collection is not supported in llm-usage-node yet; ignoring ${remotes.length} configured remote(s)`] : [];
}

async function buildAggregates(payload = {}, { maybeCaptureCursorTokenFn = maybeCaptureCursorToken } = {}) {
  const lookbackDays = Number.isFinite(Number(payload.lookback_days)) ? Number(payload.lookback_days) : Math.max(1, intEnv("LOOKBACK_DAYS", 7));
  await maybeCaptureCursorTokenFn({
    timeoutSec: 600,
    browser: "default",
    userDataDir: "",
    loginMode: "auto",
    lookbackDays,
    envPath: getEnvPath(),
    buildCursorCollector: () => ({
      probe() {
        return buildCursorCollector().probe();
      },
      collect(start, end) {
        return buildCursorCollector().collect(start, end);
      },
    }),
  });
  const localPayload = await collectLocalUsage(lookbackDays);
  const username = getEnv("ORG_USERNAME");
  const salt = getEnv("HASH_SALT");
  const userHash = hashUser(username, salt);
  const timeZone = getEnv("TIMEZONE", "Asia/Shanghai");
  const rows = aggregateEvents(localPayload.events, { userHash, timeZone });
  return { rows, warnings: [...remoteCollectionWarnings(), ...localPayload.warnings] };
}

function selectedRemoteConfigs(payload = {}) {
  const values = envMap();
  const configured = parseRemoteConfigsFromEnv(values).map((remote) => ({
    ...remote,
    use_sshpass: envFlag(values[`REMOTE_${remote.alias}_USE_SSHPASS`]),
  }));
  const selectedAliases = new Set((payload.selected_remotes || []).map((item) => String(item).trim().toUpperCase()).filter(Boolean));
  if (!selectedAliases.size) {
    return configured;
  }
  return configured.filter((config) => selectedAliases.has(config.alias));
}

function runtimePasswordRequest(payload = {}, runtimeCredentials = new Map()) {
  return null;
}

function resolveTargetSummary(names = [], selectAll = false) {
  return selectFeishuTargets(resolveFeishuTargetsFromEnv(process.env), {
    names,
    all: selectAll,
    defaultOnly: !names.length && !selectAll,
  }).map((target) => ({
    name: target.name,
    app_token: target.appToken,
    table_id: target.tableId,
  }));
}

async function resolveFeishuBotToken(target) {
  if (target.botToken) {
    return target.botToken;
  }
  if (!target.appId || !target.appSecret) {
    throw new Error(`target ${target.name}: missing Feishu app credentials`);
  }
  return fetchTenantAccessToken({ appId: target.appId, appSecret: target.appSecret });
}

async function resolveFeishuTableId(target, botToken) {
  if (!target.appToken) {
    throw new Error(`target ${target.name}: missing Feishu app token`);
  }
  if (target.tableId) {
    return target.tableId;
  }
  return fetchFirstTableId({ appToken: target.appToken, botToken });
}

async function syncRowsToFeishu(rows, names = [], selectAll = false) {
  const targets = selectFeishuTargets(resolveFeishuTargetsFromEnv(process.env), {
    names,
    all: selectAll,
    defaultOnly: !names.length && !selectAll,
  });
  if (!targets.length) {
    throw new Error("no Feishu targets configured");
  }
  let exitCode = 0;
  for (const target of targets) {
    const botToken = await resolveFeishuBotToken(target);
    const tableId = await resolveFeishuTableId(target, botToken);
    const client = new FeishuBitableClient({ appToken: target.appToken, tableId, botToken });
    const result = await client.upsert(rows, toFeishuFields);
    if (exitCode === 0 && result.failed > 0) {
      exitCode = 2;
    }
  }
  return exitCode;
}

export class JobManager {
  constructor() {
    this.jobs = new Map();
    this.writeJobId = "";
    this.runtimeCredentials = new Map();
    this.handlers = new Map();
  }

  list() {
    return [...this.jobs.values()].sort((left, right) => right.created_at.localeCompare(left.created_at));
  }

  get(jobId) {
    return this.jobs.get(jobId) || null;
  }

  start(type, handler, { writeOperation = false } = {}) {
    if (writeOperation && this.writeJobId) {
      throw new Error("another write operation is already running");
    }
    const id = `job-${this.jobs.size + 1}-${Date.now()}`;
    const job = {
      id,
      type,
      status: "queued",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      logs: [],
      result: null,
      error: null,
      write_operation: writeOperation,
      input_request: null,
    };
    this.jobs.set(id, job);
    this.handlers.set(id, handler);
    if (writeOperation) {
      this.writeJobId = id;
    }
    this.run(id);
    return job;
  }

  async run(jobId, inputValue = undefined) {
    const job = this.jobs.get(jobId);
    const handler = this.handlers.get(jobId);
    if (!job || !handler) {
      return null;
    }
    job.status = "running";
    job.updated_at = new Date().toISOString();
    try {
      const result = await handler({
        inputValue,
        job: { ...job },
        runtimeCredentials: new Map(this.runtimeCredentials),
      });
      if (result && result.status === "needs_input") {
        job.status = "needs_input";
        job.input_request = normalizeInputRequest(result.input_request);
        job.result = null;
        job.error = null;
      } else {
        job.status = "succeeded";
        job.result = result;
        job.input_request = null;
        job.error = null;
        if (this.writeJobId === jobId) {
          this.writeJobId = "";
        }
      }
      job.updated_at = new Date().toISOString();
      return this.get(jobId);
    } catch (error) {
      job.status = "failed";
      job.error = error.message;
      job.updated_at = new Date().toISOString();
      if (this.writeJobId === jobId) {
        this.writeJobId = "";
      }
      return this.get(jobId);
    }
  }

  async resumeInput(jobId, value) {
    const job = this.jobs.get(jobId);
    if (!job) {
      throw new Error("job not found");
    }
    if (job.status !== "needs_input" || !job.input_request) {
      throw new Error("job is not waiting for input");
    }
    if (job.input_request.cache_scope !== "none" && job.input_request.remote_alias) {
      this.runtimeCredentials.set(job.input_request.remote_alias, value);
    }
    job.input_request = null;
    job.status = "running";
    job.updated_at = new Date().toISOString();
    if (job.write_operation) {
      if (this.writeJobId && this.writeJobId !== jobId) {
        throw new Error("another write operation is already running");
      }
      this.writeJobId = jobId;
    }
    const next = this.run(jobId, value);
    return this.get(jobId) || (await next);
  }
}

export async function submitJobInput(jobManager, jobId, value) {
  return jobManager.resumeInput(jobId, value);
}

function normalizeInputRequest(request) {
  if (!request || typeof request !== "object") {
    throw new Error("input_request is required");
  }
  const kind = String(request.kind || "").trim();
  const remoteAlias = String(request.remote_alias || "").trim().toUpperCase();
  const message = String(request.message || "").trim();
  const cacheScope = String(request.cache_scope || "session").trim().toLowerCase() || "session";
  if (!kind) {
    throw new Error("input_request.kind is required");
  }
  if (!remoteAlias) {
    throw new Error("input_request.remote_alias is required");
  }
  if (!message) {
    throw new Error("input_request.message is required");
  }
  return {
    kind,
    remote_alias: remoteAlias,
    message,
    cache_scope: cacheScope,
  };
}

function json(response, status, payload) {
  const body = Buffer.from(JSON.stringify(payload));
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": body.length,
  });
  response.end(body);
}

async function readBody(request) {
  const chunks = [];
  for await (const chunk of request) {
    chunks.push(chunk);
  }
  if (!chunks.length) {
    return {};
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

async function maybeOpen(baseUrl, openBrowser) {
  if (!openBrowser) {
    return;
  }
  const command = process.platform === "darwin" ? "open" : process.platform === "win32" ? "cmd" : "xdg-open";
  const args =
    process.platform === "win32"
      ? ["/c", "start", "", baseUrl]
      : [baseUrl];
  execFile(command, args, { stdio: "ignore" }, () => {});
}

export function createWebRequestHandler(jobManager, { maybeCaptureCursorTokenFn } = {}) {
  return async (request, response) => {
    const url = new URL(request.url, "http://127.0.0.1");
    if (request.method === "GET" && url.pathname === "/api/runtime") {
      return json(response, 200, {
        backend: "node",
        version: packageVersion(),
        env_path: getEnvPath(),
        reports_dir: getReportsDir(),
        capabilities: { config: true, collect: true, sync: true, doctor: true },
      });
    }
    if (request.method === "GET" && url.pathname === "/api/config") {
      return json(response, 200, loadConfigPayload());
    }
    if (request.method === "GET" && url.pathname === "/api/results/latest") {
      return json(response, 200, loadLatestResults());
    }
    if (request.method === "GET" && url.pathname === "/api/jobs") {
      return json(response, 200, { jobs: jobManager.list() });
    }
    if (request.method === "GET" && url.pathname.startsWith("/api/jobs/")) {
      const jobId = url.pathname.split("/")[3];
      const job = jobManager.get(jobId);
      return json(response, job ? 200 : 404, job || { error: "job not found" });
    }
    if (request.method === "POST" && url.pathname.startsWith("/api/jobs/") && url.pathname.endsWith("/input")) {
      const jobId = url.pathname.split("/")[3];
      const payload = await readBody(request);
      if (!Object.prototype.hasOwnProperty.call(payload, "value")) {
        return json(response, 400, { error: "value is required" });
      }
      const job = await submitJobInput(jobManager, jobId, String(payload.value ?? ""));
      return json(response, 202, job || { error: "job not found" });
    }
    if (request.method === "POST" && url.pathname === "/api/config/validate") {
      return json(response, 200, validateConfigPayload(await readBody(request)));
    }
    if (request.method === "PUT" && url.pathname === "/api/config") {
      const result = writeConfigPayload(await readBody(request));
      return json(response, result.ok ? 200 : 400, result);
    }
    if (request.method === "POST" && url.pathname === "/api/doctor") {
      const payload = await readBody(request);
      const job = jobManager.start("doctor", async () => {
        if (payload.feishu) {
          return { exit_code: 0, mode: "feishu" };
        }
        return { probes: await probeLocalUsage() };
      });
      return json(response, 202, job);
    }
    if (request.method === "POST" && url.pathname === "/api/collect") {
      const payload = await readBody(request);
      const job = jobManager.start("collect", async ({ runtimeCredentials }) => {
        const inputRequest = runtimePasswordRequest(payload, runtimeCredentials);
        if (inputRequest) {
          return { status: "needs_input", input_request: inputRequest };
        }
        const { rows, warnings } = await buildAggregates(payload, { maybeCaptureCursorTokenFn });
        const csvPath = writeCsvReport(rows, getReportsDir());
        return { row_count: rows.length, warnings, csv_path: csvPath, host_labels: buildTerminalHostLabels() };
      }, { writeOperation: true });
      return json(response, 202, job);
    }
    if (request.method === "POST" && url.pathname === "/api/sync/preview") {
      const payload = await readBody(request);
      const names = (payload.feishu_targets || []).map((item) => String(item).trim()).filter(Boolean);
      const job = jobManager.start("sync_preview", async ({ runtimeCredentials }) => {
        const inputRequest = runtimePasswordRequest(payload, runtimeCredentials);
        if (inputRequest) {
          return { status: "needs_input", input_request: inputRequest };
        }
        const { rows, warnings } = await buildAggregates(payload, { maybeCaptureCursorTokenFn });
        return { row_count: rows.length, warnings, targets: resolveTargetSummary(names, Boolean(payload.all_feishu_targets)) };
      });
      return json(response, 202, job);
    }
    if (request.method === "POST" && url.pathname === "/api/sync") {
      const payload = await readBody(request);
      if (!payload.confirm_sync) {
        return json(response, 400, { error: "confirm_sync is required" });
      }
      const names = (payload.feishu_targets || []).map((item) => String(item).trim()).filter(Boolean);
      const job = jobManager.start("sync", async ({ runtimeCredentials }) => {
        const inputRequest = runtimePasswordRequest(payload, runtimeCredentials);
        if (inputRequest) {
          return { status: "needs_input", input_request: inputRequest };
        }
        const { rows, warnings } = await buildAggregates(payload, { maybeCaptureCursorTokenFn });
        const csvPath = writeCsvReport(rows, getReportsDir());
        const exitCode = await syncRowsToFeishu(rows, names, Boolean(payload.all_feishu_targets));
        return { row_count: rows.length, warnings, csv_path: csvPath, exit_code: exitCode };
      }, { writeOperation: true });
      return json(response, 202, job);
    }

    const relative = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
    let filePath = path.resolve(webRoot, relative);
    if (!filePath.startsWith(webRoot) || !fs.existsSync(filePath)) {
      filePath = path.resolve(webRoot, "index.html");
    }
    const contentType = filePath.endsWith(".js")
      ? "application/javascript; charset=utf-8"
      : filePath.endsWith(".css")
        ? "text/css; charset=utf-8"
        : "text/html; charset=utf-8";
    const body = await readFile(filePath);
    response.writeHead(200, { "content-type": contentType, "content-length": body.length });
    response.end(body);
  };
}

export async function createWebServer({ host = "127.0.0.1", port = 0, openBrowser = true, env = null, jobs = null } = {}) {
  if (env) {
    Object.assign(process.env, env);
  }
  await prepareRuntimePaths(repoRoot);
  loadDotenv();
  const jobManager = jobs || new JobManager();
  const handler = createWebRequestHandler(jobManager);
  const server = http.createServer(async (request, response) => {
    try {
      await handler(request, response);
    } catch (error) {
      json(response, 500, { error: error.message });
    }
  });

  return {
    get baseUrl() {
      const address = server.address();
      return `http://${host}:${address.port}`;
    },
    async start() {
      await new Promise((resolve) => server.listen(port, host, resolve));
      await maybeOpen(this.baseUrl, openBrowser);
    },
    async stop() {
      await new Promise((resolve) => server.close(resolve));
    },
  };
}
