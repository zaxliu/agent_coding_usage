import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";

function getDbPath() {
  const override = String(process.env.OPENCODE_DB_PATH || "").trim();
  if (override) {
    return override.startsWith("~") ? path.join(os.homedir(), override.slice(1)) : path.resolve(override);
  }
  return path.join(os.homedir(), ".local", "share", "opencode", "opencode.db");
}

function parseTimestamp(timestamp) {
  if (timestamp == null) {
    return new Date();
  }
  return new Date(Number(timestamp));
}

function extractTokensFromPartData(data) {
  let payload;
  try {
    payload = JSON.parse(String(data));
  } catch {
    return null;
  }
  if (payload?.type !== "step-finish" || !payload.tokens || typeof payload.tokens !== "object") {
    return null;
  }
  const inputTokens = Number(payload.tokens.input || 0);
  const outputTokens = Number(payload.tokens.output || 0);
  let cacheTokens = 0;
  if (payload.tokens.cache && typeof payload.tokens.cache === "object") {
    cacheTokens = Number(payload.tokens.cache.read || 0) + Number(payload.tokens.cache.write || 0);
  } else if (typeof payload.tokens.cache === "number") {
    cacheTokens = payload.tokens.cache;
  }
  return {
    inputTokens: Number.isFinite(inputTokens) ? inputTokens : 0,
    cacheTokens: Number.isFinite(cacheTokens) ? cacheTokens : 0,
    outputTokens: Number.isFinite(outputTokens) ? outputTokens : 0,
  };
}

function extractModelFromPartData(data) {
  let payload;
  try {
    payload = JSON.parse(String(data));
  } catch {
    return "unknown";
  }
  if (payload?.type === "step-start" && typeof payload.model === "string" && payload.model.trim()) {
    return payload.model.trim();
  }
  for (const key of ["model", "model_name", "modelName"]) {
    const value = payload?.[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return "unknown";
}

export class OpenCodeCollector {
  constructor({ sourceName = "local", sourceHostHash = "", dbPath = getDbPath() } = {}) {
    this.name = "opencode";
    this.sourceName = sourceName;
    this.sourceHostHash = sourceHostHash;
    this.dbPath = dbPath;
  }

  probe() {
    if (!fs.existsSync(this.dbPath)) {
      return { ok: false, message: `OpenCode database not found at ${this.dbPath}` };
    }
    try {
      const db = new DatabaseSync(this.dbPath, { open: true, readOnly: true });
      const row = db.prepare("SELECT COUNT(*) AS count FROM part WHERE data LIKE '%tokens%'").get();
      db.close();
      const count = Number(row?.count || 0);
      if (count === 0) {
        return { ok: false, message: "OpenCode database exists but no token records found" };
      }
      return { ok: true, message: `OpenCode database found with ${count} token records` };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      return { ok: false, message: `Failed to read OpenCode database: ${message}` };
    }
  }

  collect(start, end) {
    const events = [];
    const warnings = [];
    if (!fs.existsSync(this.dbPath)) {
      return {
        events,
        warnings: [`OpenCode database not found at ${this.dbPath}`],
      };
    }
    try {
      const db = new DatabaseSync(this.dbPath, { open: true, readOnly: true });
      const rows = db
        .prepare(`
          SELECT p.data, p.time_created, s.directory
          FROM part p
          JOIN message m ON p.message_id = m.id
          JOIN session s ON m.session_id = s.id
          WHERE p.data LIKE '%"type"%step-finish%'
            AND p.data LIKE '%tokens%'
          ORDER BY p.time_created
        `)
        .all();
      db.close();

      for (const row of rows) {
        const tokens = extractTokensFromPartData(row.data);
        if (!tokens) {
          continue;
        }
        if (tokens.inputTokens === 0 && tokens.cacheTokens === 0 && tokens.outputTokens === 0) {
          continue;
        }
        const eventTime = parseTimestamp(row.time_created);
        if (eventTime < start || eventTime > end) {
          continue;
        }
        events.push({
          tool: this.name,
          model: extractModelFromPartData(row.data),
          eventTime,
          inputTokens: tokens.inputTokens,
          cacheTokens: tokens.cacheTokens,
          outputTokens: tokens.outputTokens,
          sourceRef: `opencode:${row.directory}`,
          sourceHostHash: this.sourceHostHash,
        });
      }
      if (!events.length) {
        warnings.push(`${this.name}: no usage events in selected time range`);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      warnings.push(`Failed to read OpenCode database: ${message}`);
    }
    return { events, warnings };
  }
}
