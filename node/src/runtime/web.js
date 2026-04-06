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
const ADVANCED_KEYS = ["CLAUDE_LOG_PATHS", "CODEX_LOG_PATHS", "COPILOT_CLI_LOG_PATHS", "COPILOT_VSCODE_SESSION_PATHS"];

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
    })),
    remotes: parseRemoteConfigsFromEnv(values).map((remote) => ({
      ...remote,
      use_sshpass: false,
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
    return { ok: true, csv_path: csvPath, rows: [], generated_at: null };
  }
  const [headerLine, ...lines] = fs.readFileSync(csvPath, "utf8").trim().split(/\r?\n/u);
  const headers = headerLine.split(",");
  const rows = lines.filter(Boolean).map((line) => {
    const values = line.split(",");
    return Object.fromEntries(headers.map((header, index) => [header, values[index] || ""]));
  });
  return {
    ok: true,
    csv_path: csvPath,
    rows,
    generated_at: new Date(fs.statSync(csvPath).mtimeMs).toISOString(),
  };
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

async function buildAggregates(payload = {}) {
  const lookbackDays = Number.isFinite(Number(payload.lookback_days)) ? Number(payload.lookback_days) : Math.max(1, intEnv("LOOKBACK_DAYS", 7));
  await maybeCaptureCursorToken({
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

class JobManager {
  constructor() {
    this.jobs = new Map();
    this.writeJobId = "";
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
    };
    this.jobs.set(id, job);
    if (writeOperation) {
      this.writeJobId = id;
    }
    Promise.resolve()
      .then(async () => {
        job.status = "running";
        job.updated_at = new Date().toISOString();
        job.result = await handler();
        job.status = "succeeded";
        job.updated_at = new Date().toISOString();
      })
      .catch((error) => {
        job.status = "failed";
        job.error = error.message;
        job.updated_at = new Date().toISOString();
      })
      .finally(() => {
        if (this.writeJobId === id) {
          this.writeJobId = "";
        }
      });
    return job;
  }
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

export async function createWebServer({ host = "127.0.0.1", port = 0, openBrowser = true, env = null } = {}) {
  if (env) {
    Object.assign(process.env, env);
  }
  await prepareRuntimePaths(repoRoot);
  loadDotenv();
  const jobs = new JobManager();
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, "http://127.0.0.1");
    try {
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
        return json(response, 200, { jobs: jobs.list() });
      }
      if (request.method === "GET" && url.pathname.startsWith("/api/jobs/")) {
        const jobId = url.pathname.split("/")[3];
        const job = jobs.get(jobId);
        return json(response, job ? 200 : 404, job || { error: "job not found" });
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
        const job = jobs.start("doctor", async () => {
          if (payload.feishu) {
            return { exit_code: 0, mode: "feishu" };
          }
          return { probes: await probeLocalUsage() };
        });
        return json(response, 202, job);
      }
      if (request.method === "POST" && url.pathname === "/api/collect") {
        const payload = await readBody(request);
        const job = jobs.start("collect", async () => {
          const { rows, warnings } = await buildAggregates(payload);
          const csvPath = writeCsvReport(rows, getReportsDir());
          return { row_count: rows.length, warnings, csv_path: csvPath, host_labels: buildTerminalHostLabels() };
        }, { writeOperation: true });
        return json(response, 202, job);
      }
      if (request.method === "POST" && url.pathname === "/api/sync/preview") {
        const payload = await readBody(request);
        const names = (payload.feishu_targets || []).map((item) => String(item).trim()).filter(Boolean);
        const job = jobs.start("sync_preview", async () => {
          const { rows, warnings } = await buildAggregates(payload);
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
        const job = jobs.start("sync", async () => {
          const { rows, warnings } = await buildAggregates(payload);
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
