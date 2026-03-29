import process from "node:process";
import { execFileSync } from "node:child_process";

import { getEnvPath, readEnvFile, upsertEnvVar } from "./env.js";

export const DEFAULT_REMOTE_CLAUDE_LOG_PATHS = [
  "~/.claude/**/*.jsonl",
  "~/.claude/**/*.json",
  "~/.config/claude/**/*.jsonl",
];
export const DEFAULT_REMOTE_CODEX_LOG_PATHS = ["~/.codex/**/*.jsonl", "~/.codex/**/*.json"];
export const DEFAULT_REMOTE_COPILOT_CLI_LOG_PATHS = ["~/.copilot/session-state/**/*.jsonl"];
export const DEFAULT_REMOTE_COPILOT_VSCODE_SESSION_PATHS = [
  "~/.vscode-server/data/User/globalStorage/emptyWindowChatSessions/*.jsonl",
  "~/.vscode-server/data/User/workspaceStorage/**/chatEditingSessions/*/state.json",
];

export function defaultSourceLabel(sshUser, sshHost) {
  return `${String(sshUser).trim()}@${String(sshHost).trim()}`;
}

export function normalizeAlias(value) {
  const cleaned = String(value || "")
    .trim()
    .replace(/[^A-Za-z0-9]+/gu, "_")
    .replace(/^_+|_+$/gu, "");
  return (cleaned || "REMOTE").toUpperCase();
}

export function uniqueAlias(base, existingAliases) {
  const candidate = normalizeAlias(base);
  const used = new Set(existingAliases.map((item) => normalizeAlias(item)));
  if (!used.has(candidate)) {
    return candidate;
  }
  let index = 2;
  while (used.has(`${candidate}_${index}`)) {
    index += 1;
  }
  return `${candidate}_${index}`;
}

function splitAliases(raw) {
  return String(raw || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .map(normalizeAlias);
}

function splitPaths(raw, defaults) {
  const items = String(raw || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  return items.length ? items : [...defaults];
}

function safePort(raw) {
  const parsed = Number.parseInt(String(raw || "22"), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 22;
}

export function parseRemoteConfigsFromEnv(env = process.env) {
  const aliases = splitAliases(env.REMOTE_HOSTS || "");
  const out = [];
  for (const alias of aliases) {
    const prefix = `REMOTE_${alias}_`;
    const sshHost = String(env[`${prefix}SSH_HOST`] || "").trim();
    const sshUser = String(env[`${prefix}SSH_USER`] || "").trim();
    if (!sshHost || !sshUser) {
      continue;
    }
    out.push({
      alias,
      ssh_host: sshHost,
      ssh_user: sshUser,
      ssh_port: safePort(env[`${prefix}SSH_PORT`]),
      source_label: String(env[`${prefix}LABEL`] || "").trim() || defaultSourceLabel(sshUser, sshHost),
      claude_log_paths: splitPaths(env[`${prefix}CLAUDE_LOG_PATHS`], DEFAULT_REMOTE_CLAUDE_LOG_PATHS),
      codex_log_paths: splitPaths(env[`${prefix}CODEX_LOG_PATHS`], DEFAULT_REMOTE_CODEX_LOG_PATHS),
      copilot_cli_log_paths: splitPaths(
        env[`${prefix}COPILOT_CLI_LOG_PATHS`],
        DEFAULT_REMOTE_COPILOT_CLI_LOG_PATHS,
      ),
      copilot_vscode_session_paths: splitPaths(
        env[`${prefix}COPILOT_VSCODE_SESSION_PATHS`],
        DEFAULT_REMOTE_COPILOT_VSCODE_SESSION_PATHS,
      ),
      is_ephemeral: false,
    });
  }
  return out;
}

export function buildTemporaryRemote(sshHost, sshUser, sshPort = 22) {
  const sourceLabel = defaultSourceLabel(sshUser, sshHost);
  return {
    alias: uniqueAlias(normalizeAlias(sourceLabel), []),
    ssh_host: String(sshHost).trim(),
    ssh_user: String(sshUser).trim(),
    ssh_port: Math.max(1, Number.parseInt(String(sshPort), 10) || 22),
    source_label: sourceLabel,
    claude_log_paths: [...DEFAULT_REMOTE_CLAUDE_LOG_PATHS],
    codex_log_paths: [...DEFAULT_REMOTE_CODEX_LOG_PATHS],
    copilot_cli_log_paths: [...DEFAULT_REMOTE_COPILOT_CLI_LOG_PATHS],
    copilot_vscode_session_paths: [...DEFAULT_REMOTE_COPILOT_VSCODE_SESSION_PATHS],
    is_ephemeral: true,
  };
}

export function appendRemoteToEnv(config, existingAliases, filePath = getEnvPath()) {
  const alias = uniqueAlias(config.alias, existingAliases);
  upsertEnvVar("REMOTE_HOSTS", [...existingAliases, alias].join(","), filePath);
  const prefix = `REMOTE_${alias}_`;
  upsertEnvVar(`${prefix}SSH_HOST`, config.ssh_host, filePath);
  upsertEnvVar(`${prefix}SSH_USER`, config.ssh_user, filePath);
  upsertEnvVar(`${prefix}SSH_PORT`, String(config.ssh_port), filePath);
  upsertEnvVar(`${prefix}LABEL`, config.source_label, filePath);
  upsertEnvVar(`${prefix}CLAUDE_LOG_PATHS`, config.claude_log_paths.join(","), filePath);
  upsertEnvVar(`${prefix}CODEX_LOG_PATHS`, config.codex_log_paths.join(","), filePath);
  upsertEnvVar(`${prefix}COPILOT_CLI_LOG_PATHS`, config.copilot_cli_log_paths.join(","), filePath);
  upsertEnvVar(`${prefix}COPILOT_VSCODE_SESSION_PATHS`, config.copilot_vscode_session_paths.join(","), filePath);
  return alias;
}

export function buildEnvWithTemporaryRemotes(baseEnv, temporaryRemotes) {
  const nextEnv = { ...baseEnv };
  const assignedAliases = [];
  if (!temporaryRemotes.length) {
    return { env: nextEnv, aliases: assignedAliases };
  }
  const existing = parseRemoteConfigsFromEnv(baseEnv).map((item) => item.alias);
  const aliases = splitAliases(nextEnv.REMOTE_HOSTS || "");
  for (const config of temporaryRemotes) {
    const alias = uniqueAlias(config.alias, [...existing, ...aliases]);
    assignedAliases.push(alias);
    aliases.push(alias);
    const prefix = `REMOTE_${alias}_`;
    nextEnv[`${prefix}SSH_HOST`] = config.ssh_host;
    nextEnv[`${prefix}SSH_USER`] = config.ssh_user;
    nextEnv[`${prefix}SSH_PORT`] = String(config.ssh_port);
    nextEnv[`${prefix}LABEL`] = config.source_label;
    nextEnv[`${prefix}CLAUDE_LOG_PATHS`] = config.claude_log_paths.join(",");
    nextEnv[`${prefix}CODEX_LOG_PATHS`] = config.codex_log_paths.join(",");
    nextEnv[`${prefix}COPILOT_CLI_LOG_PATHS`] = config.copilot_cli_log_paths.join(",");
    nextEnv[`${prefix}COPILOT_VSCODE_SESSION_PATHS`] = config.copilot_vscode_session_paths.join(",");
  }
  nextEnv.REMOTE_HOSTS = aliases.join(",");
  return { env: nextEnv, aliases: assignedAliases };
}

export function probeRemoteSsh(config, timeoutSec = 10) {
  try {
    execFileSync(
      "ssh",
      [
        "-o",
        "BatchMode=yes",
        "-o",
        "ControlMaster=auto",
        "-o",
        "ControlPersist=5m",
        "-o",
        "ControlPath=/tmp/llm-usage-ssh-%C",
        "-p",
        String(config.ssh_port),
        `${config.ssh_user}@${config.ssh_host}`,
        "true",
      ],
      {
        stdio: ["ignore", "pipe", "pipe"],
        encoding: "utf8",
        timeout: Math.max(3, timeoutSec) * 1000,
      },
    );
    return [true, "SSH 连接正常"];
  } catch (error) {
    const message =
      error?.stderr?.trim?.() || error?.stdout?.trim?.() || error?.message || "SSH 连接失败";
    return [false, message];
  }
}

export function currentEnvMap(filePath = getEnvPath()) {
  return Object.fromEntries(readEnvFile(filePath));
}
