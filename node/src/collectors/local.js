import process from "node:process";

import { hashSourceHost } from "../core/identity.js";
import { getEnv, intEnv } from "../runtime/env.js";
import { FileCollector } from "./file-collector.js";
import { OpenCodeCollector } from "./opencode.js";

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

function defaultCopilotCliPaths() {
  if (process.platform === "win32") {
    return [
      "~/.copilot/session-state/*.json",
      "~/.copilot/session-state/*.jsonl",
      "~/.copilot/session-state/**/*.jsonl",
    ];
  }
  return [
    "~/.copilot/session-state/*.json",
    "~/.copilot/session-state/*.jsonl",
    "~/.copilot/session-state/**/*.jsonl",
  ];
}

function defaultCopilotVscodePaths() {
  let roots;
  if (process.platform === "win32") {
    const appdata = String(process.env.APPDATA || "").trim();
    roots = appdata
      ? ["Code", "Code - Insiders", "Code - Exploration", "Cursor", "VSCodium"].map((variant) => `${appdata}/${variant}/User`)
      : ["Code", "Code - Insiders", "Code - Exploration", "Cursor", "VSCodium"].map(
          (variant) => `~/AppData/Roaming/${variant}/User`,
        );
  } else if (process.platform === "darwin") {
    roots = [
      "~/Library/Application Support/Code/User",
      "~/Library/Application Support/Code - Insiders/User",
      "~/Library/Application Support/Code - Exploration/User",
      "~/Library/Application Support/Cursor/User",
      "~/Library/Application Support/VSCodium/User",
    ];
  } else {
    roots = [
      "~/.config/Code/User",
      "~/.config/Code - Insiders/User",
      "~/.config/Code - Exploration/User",
      "~/.config/Cursor/User",
      "~/.config/VSCodium/User",
      "~/.vscode-server/data/User",
      "~/.vscode-server-insiders/data/User",
      "~/.vscode-remote/data/User",
      "/tmp/.vscode-server/data/User",
      "/workspace/.vscode-server/data/User",
    ];
  }
  return roots.flatMap((root) => [
    `${root}/workspaceStorage/**/chatSessions/*.json`,
    `${root}/workspaceStorage/**/chatSessions/*.jsonl`,
    `${root}/globalStorage/emptyWindowChatSessions/*.json`,
    `${root}/globalStorage/emptyWindowChatSessions/*.jsonl`,
    `${root}/globalStorage/github.copilot-chat/**/*.json`,
    `${root}/globalStorage/github.copilot-chat/**/*.jsonl`,
  ]);
}

function includeCursorLocalCollector() {
  return !getEnv("CURSOR_WEB_SESSION_TOKEN");
}

function buildCollectors() {
  const username = getEnv("ORG_USERNAME");
  const salt = getEnv("HASH_SALT");
  const sourceHostHash = username && salt ? hashSourceHost(username, "local", salt) : "";
  const collectors = [
    new FileCollector("claude_code", splitCsvEnv("CLAUDE_LOG_PATHS", ["~/.claude/**/*.jsonl", "~/.claude/**/*.json", "~/.config/claude/**/*.jsonl"]), {
      sourceHostHash,
    }),
    new FileCollector("codex", splitCsvEnv("CODEX_LOG_PATHS", ["~/.codex/**/*.jsonl", "~/.codex/**/*.json"]), {
      sourceHostHash,
    }),
    new FileCollector("copilot_cli", splitCsvEnv("COPILOT_CLI_LOG_PATHS", defaultCopilotCliPaths()), {
      sourceHostHash,
    }),
    new FileCollector("copilot_vscode", splitCsvEnv("COPILOT_VSCODE_SESSION_PATHS", defaultCopilotVscodePaths()), {
      sourceHostHash,
    }),
  ];
  collectors.push(new OpenCodeCollector({ sourceHostHash }));
  if (includeCursorLocalCollector()) {
    collectors.push(
      new FileCollector(
        "cursor",
        splitCsvEnv("CURSOR_LOG_PATHS", [
          "~/.cursor/logs/**/*.jsonl",
          "~/.cursor/logs/**/*.json",
          "~/.config/Cursor/User/workspaceStorage/**/*.json",
          "~/.config/Cursor/User/globalStorage/**/*.json",
          "~/Library/Application Support/Cursor/User/workspaceStorage/**/*.json",
          "~/Library/Application Support/Cursor/User/globalStorage/**/*.json",
        ]),
        { sourceHostHash },
      ),
    );
  }
  return collectors;
}

export function localCollectorNames() {
  return buildCollectors().map((collector) => collector.name);
}

export function collectLocalUsage(lookbackDays = intEnv("LOOKBACK_DAYS", 7)) {
  const end = new Date();
  const start = new Date(end.getTime() - Math.max(1, lookbackDays) * 24 * 60 * 60 * 1000);
  const events = [];
  const warnings = [];
  for (const collector of buildCollectors()) {
    const result = collector.collect(start, end);
    events.push(...result.events);
    warnings.push(...result.warnings);
  }
  return { events, warnings };
}

export function probeLocalUsage() {
  return buildCollectors().map((collector) => ({
    name: collector.name,
    source_name: collector.sourceName,
    source_host_hash: collector.sourceHostHash,
    ...collector.probe(),
  }));
}
