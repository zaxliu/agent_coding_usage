import path from "node:path";
import process from "node:process";

import { getEnv, intEnv } from "../runtime/env.js";
import { FileCollector } from "./file-collector.js";

const CURSOR_BASE_URL = "https://cursor.com";

function splitCsvEnv(name, defaults) {
  const raw = String(process.env[name] || "").trim();
  if (!raw) {
    return [...defaults];
  }
  return raw
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function defaultCursorPaths() {
  return [
    "~/.cursor/logs/**/*.jsonl",
    "~/.cursor/logs/**/*.json",
    "~/.config/Cursor/User/workspaceStorage/**/*.json",
    "~/.config/Cursor/User/globalStorage/**/*.json",
    "~/Library/Application Support/Cursor/User/workspaceStorage/**/*.json",
    "~/Library/Application Support/Cursor/User/globalStorage/**/*.json",
    "~/AppData/Roaming/Cursor/User/workspaceStorage/**/*.json",
    "~/AppData/Roaming/Cursor/User/globalStorage/**/*.json",
  ];
}

async function requestCursorDashboard({
  sessionToken,
  workosId = "",
  baseUrl = CURSOR_BASE_URL,
  timeoutMs = 15_000,
  body,
}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${baseUrl}/api/dashboard/get-filtered-usage-events`, {
      method: "POST",
      headers: {
        Accept: "application/json, text/plain, */*",
        "Content-Type": "application/json",
        Origin: baseUrl,
        Referer: `${baseUrl}/dashboard/usage`,
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        Cookie: [`WorkosCursorSessionToken=${sessionToken}`, workosId ? `workos_id=${workosId}` : ""]
          .filter(Boolean)
          .join("; "),
      },
      body: JSON.stringify(body),
      signal: controller.signal,
      credentials: "omit",
    });
    const text = await response.text();
    let payload = {};
    try {
      payload = JSON.parse(text);
    } catch {
      payload = {};
    }
    return { status: response.status, payload, text };
  } finally {
    clearTimeout(timeout);
  }
}

function mapUsageEvent(item, sourceHostHash) {
  const tokenUsage = item?.tokenUsage || {};
  return {
    tool: "cursor",
    model: String(item?.model || "unknown"),
    eventTime: new Date(Number(item?.timestamp || Date.now())),
    inputTokens: Number(tokenUsage.inputTokens || 0),
    cacheTokens: Number(tokenUsage.cacheReadTokens || 0) + Number(tokenUsage.cacheWriteTokens || 0),
    outputTokens: Number(tokenUsage.outputTokens || 0),
    sessionFingerprint: `cursor_dashboard:${item?.timestamp || "unknown"}`,
    sourceRef: "cursor_dashboard",
    sourceHostHash,
  };
}

export class CursorDashboardCollector {
  constructor({
    sessionToken,
    workosId = "",
    baseUrl = getEnv("CURSOR_DASHBOARD_BASE_URL", CURSOR_BASE_URL),
    teamId = intEnv("CURSOR_DASHBOARD_TEAM_ID", 0),
    pageSize = intEnv("CURSOR_DASHBOARD_PAGE_SIZE", 300),
    timeoutSec = intEnv("CURSOR_DASHBOARD_TIMEOUT_SEC", 15),
    sourceName = "local",
    sourceHostHash = "",
  } = {}) {
    this.name = "cursor";
    this.sourceName = sourceName;
    this.sourceHostHash = sourceHostHash;
    this.sessionToken = sessionToken;
    this.workosId = workosId;
    this.baseUrl = baseUrl;
    this.teamId = teamId;
    this.pageSize = pageSize;
    this.timeoutMs = Math.max(1, timeoutSec) * 1000;
  }

  async probe() {
    const end = Date.now();
    const start = end - 24 * 60 * 60 * 1000;
    const body = {
      teamId: this.teamId,
      startDate: String(start),
      endDate: String(end),
      page: 1,
      pageSize: 1,
    };
    const { status, payload, text } = await requestCursorDashboard({
      sessionToken: this.sessionToken,
      workosId: this.workosId,
      baseUrl: this.baseUrl,
      timeoutMs: this.timeoutMs,
      body,
    });
    if (status === 401 || status === 403) {
      return { ok: false, message: `authentication failed (${status}): ${String(text).slice(0, 140)}` };
    }
    if (status >= 400) {
      return { ok: false, message: `http error ${status}` };
    }
    if (Array.isArray(payload?.usageEventsDisplay)) {
      return { ok: true, message: "cursor dashboard token valid" };
    }
    return { ok: true, message: "cursor dashboard response accepted" };
  }

  async collect(start, end) {
    const events = [];
    const warnings = [];
    const startDate = String(start.getTime());
    const endDate = String(end.getTime());
    let page = 1;
    let totalCount = Infinity;

    while (events.length < totalCount) {
      const { status, payload, text } = await requestCursorDashboard({
        sessionToken: this.sessionToken,
        workosId: this.workosId,
        baseUrl: this.baseUrl,
        timeoutMs: this.timeoutMs,
        body: {
          teamId: this.teamId,
          startDate,
          endDate,
          page,
          pageSize: this.pageSize,
        },
      });
      if (status === 401 || status === 403) {
        warnings.push(`cursor dashboard authentication failed (${status})`);
        break;
      }
      if (status >= 400) {
        warnings.push(`cursor dashboard http error ${status}: ${String(text).slice(0, 140)}`);
        break;
      }
      const items = Array.isArray(payload?.usageEventsDisplay) ? payload.usageEventsDisplay : [];
      totalCount = Number(payload?.totalUsageEventsCount || items.length || 0);
      for (const item of items) {
        events.push(mapUsageEvent(item, this.sourceHostHash));
      }
      if (!items.length || items.length < this.pageSize) {
        break;
      }
      page += 1;
    }

    if (!events.length && !warnings.length) {
      warnings.push("cursor: no usage events in selected time range");
    }
    return { events, warnings };
  }
}

export function buildCursorCollector({ sourceHostHash = "", sourceName = "local" } = {}) {
  const sessionToken = getEnv("CURSOR_WEB_SESSION_TOKEN");
  if (sessionToken) {
    return new CursorDashboardCollector({
      sessionToken,
      workosId: getEnv("CURSOR_WEB_WORKOS_ID"),
      sourceHostHash,
      sourceName,
    });
  }

  return new FileCollector("cursor", splitCsvEnv("CURSOR_LOG_PATHS", defaultCursorPaths()), {
    sourceHostHash,
    sourceName,
  });
}

export function cursorCollectorDebugName() {
  return path.basename(import.meta.url);
}
