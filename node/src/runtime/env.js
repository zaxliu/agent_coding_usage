import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { isInteractive, promptLine, warn } from "./ui.js";

const thisDir = path.dirname(fileURLToPath(import.meta.url));
export const repoRoot = path.resolve(thisDir, "../../..");
const bootstrapEnvPath = path.resolve(thisDir, "../../resources/bootstrap.env");
const APP_NAME = "llm-usage";
const ACCEPT_ANSWERS = new Set(["y", "yes", "是", "确认"]);
let runtimePathsCache = new Map();

function platformHomeDir(platform = process.platform) {
  if (platform === "darwin") {
    return String(process.env.HOME || "").trim() || os.homedir();
  }
  if (platform === "win32") {
    const userProfile = String(process.env.USERPROFILE || "").trim();
    if (userProfile) {
      return userProfile;
    }
    const homeDrive = String(process.env.HOMEDRIVE || "").trim();
    const homePath = String(process.env.HOMEPATH || "").trim();
    if (homeDrive && homePath) {
      return path.join(homeDrive, homePath);
    }
  }
  return os.homedir();
}

function configDir() {
  const envFile = String(process.env.LLM_USAGE_ENV_FILE || "").trim();
  if (envFile) {
    return path.dirname(path.resolve(envFile));
  }
  if (process.platform === "darwin") {
    return path.join(platformHomeDir("darwin"), "Library", "Application Support", APP_NAME);
  }
  if (process.platform === "win32") {
    return path.join(process.env.APPDATA || path.join(platformHomeDir("win32"), "AppData", "Roaming"), APP_NAME);
  }
  return path.join(process.env.XDG_CONFIG_HOME || path.join(platformHomeDir(), ".config"), APP_NAME);
}

function dataDir() {
  const override = String(process.env.LLM_USAGE_DATA_DIR || "").trim();
  if (override) {
    return path.resolve(override);
  }
  if (process.platform === "darwin") {
    return path.join(platformHomeDir("darwin"), "Library", "Application Support", APP_NAME);
  }
  if (process.platform === "win32") {
    return path.join(process.env.APPDATA || path.join(platformHomeDir("win32"), "AppData", "Roaming"), APP_NAME);
  }
  return path.join(process.env.XDG_DATA_HOME || path.join(platformHomeDir(), ".local", "share"), APP_NAME);
}

async function resolveFilePath(label, preferredPath, legacyPath, { allowLegacy = true } = {}) {
  if (fs.existsSync(preferredPath)) {
    return preferredPath;
  }
  if (!allowLegacy || !fs.existsSync(legacyPath) || preferredPath === legacyPath) {
    return preferredPath;
  }
  if (isInteractive()) {
    const answer = (
      await promptLine(`检测到旧版 ${label} 在 ${legacyPath}，是否迁移到 ${preferredPath}？[y/N]: `)
    )
      .trim()
      .toLowerCase();
    if (ACCEPT_ANSWERS.has(answer)) {
      fs.mkdirSync(path.dirname(preferredPath), { recursive: true });
      fs.copyFileSync(legacyPath, preferredPath);
      console.log(`info: migrated ${label} from ${legacyPath} to ${preferredPath}`);
      return preferredPath;
    }
    console.log(warn(`using legacy ${label} for this run: ${legacyPath}`));
    return legacyPath;
  }
  console.log(
    warn(`found legacy ${label} at ${legacyPath}; new default is ${preferredPath}. Using legacy file for this run.`),
  );
  return legacyPath;
}

export async function prepareRuntimePaths(legacyRoot = repoRoot) {
  const root = path.resolve(legacyRoot);
  const cacheKey = [
    root,
    String(process.env.LLM_USAGE_ENV_FILE || "").trim(),
    String(process.env.LLM_USAGE_DATA_DIR || "").trim(),
  ].join("|");
  if (runtimePathsCache.has(cacheKey)) {
    return runtimePathsCache.get(cacheKey);
  }

  const resolvedConfigDir = configDir();
  const resolvedDataDir = dataDir();
  const explicitEnvPath = String(process.env.LLM_USAGE_ENV_FILE || "").trim();
  const explicitDataDir = String(process.env.LLM_USAGE_DATA_DIR || "").trim();
  const preferredEnvPath = explicitEnvPath ? path.resolve(explicitEnvPath) : path.join(resolvedConfigDir, ".env");
  const preferredRuntimeStatePath = path.join(resolvedDataDir, "runtime_state.json");
  const envPath = await resolveFilePath(".env", preferredEnvPath, path.join(root, ".env"), {
    allowLegacy: !explicitEnvPath,
  });
  const runtimeStatePath = await resolveFilePath(
    "runtime state",
    preferredRuntimeStatePath,
    path.join(root, "reports", "runtime_state.json"),
    { allowLegacy: !explicitDataDir },
  );
  const resolved = {
    configDir: resolvedConfigDir,
    dataDir: resolvedDataDir,
    envPath,
    reportsDir: path.join(resolvedDataDir, "reports"),
    runtimeStatePath,
  };
  runtimePathsCache.set(cacheKey, resolved);
  return resolved;
}

export function resetRuntimePathsCache() {
  runtimePathsCache = new Map();
}

function defaultRuntimePaths(legacyRoot = repoRoot) {
  const root = path.resolve(legacyRoot);
  const resolvedConfigDir = configDir();
  const resolvedDataDir = dataDir();
  const envFile = String(process.env.LLM_USAGE_ENV_FILE || "").trim();
  return {
    configDir: resolvedConfigDir,
    dataDir: resolvedDataDir,
    envPath: envFile ? path.resolve(envFile) : path.join(resolvedConfigDir, ".env"),
    reportsDir: path.join(resolvedDataDir, "reports"),
    runtimeStatePath: path.join(resolvedDataDir, "runtime_state.json"),
    legacyEnvPath: path.join(root, ".env"),
    legacyRuntimeStatePath: path.join(root, "reports", "runtime_state.json"),
  };
}

function currentPaths(legacyRoot = repoRoot) {
  const prepared = [...runtimePathsCache.values()][0];
  return prepared || defaultRuntimePaths(legacyRoot);
}

export function getEnvPath() {
  return currentPaths().envPath;
}

export function getReportsDir() {
  return currentPaths().reportsDir;
}

export function getRuntimeStatePath() {
  return currentPaths().runtimeStatePath;
}

function readBootstrapEnvText() {
  return fs.readFileSync(bootstrapEnvPath, "utf8");
}

function ensureEnvFileExists(filePath = getEnvPath()) {
  if (fs.existsSync(filePath)) {
    return;
  }
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, readBootstrapEnvText(), "utf8");
}

export function loadDotenv(filePath = getEnvPath()) {
  ensureEnvFileExists(filePath);
  const text = fs.readFileSync(filePath, "utf8");
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) {
      continue;
    }
    const [rawKey, ...rest] = line.split("=");
    const key = rawKey.trim();
    const value = rest.join("=").trim().replace(/^['"]|['"]$/g, "");
    if (!(key in process.env)) {
      process.env[key] = value;
    }
  }
}

export function getEnv(name, fallback = "") {
  const value = process.env[name];
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

export function requiredEnv(name) {
  const value = getEnv(name);
  if (!value) {
    throw new Error(`missing env var: ${name}`);
  }
  return value;
}

export function intEnv(name, fallback) {
  const raw = getEnv(name, String(fallback));
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function readEnvFile(filePath = getEnvPath()) {
  const out = [];
  if (!fs.existsSync(filePath)) {
    return out;
  }
  const text = fs.readFileSync(filePath, "utf8");
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) {
      continue;
    }
    const [rawKey, ...rest] = line.split("=");
    out.push([rawKey.trim(), rest.join("=").trim().replace(/^['"]|['"]$/g, "")]);
  }
  return out;
}

export function upsertEnvVar(key, value, filePath = getEnvPath()) {
  const normalizedKey = String(key || "").trim();
  if (!normalizedKey) {
    throw new Error("env key cannot be empty");
  }

  const encoded = `${normalizedKey}=${value}`;
  if (!fs.existsSync(filePath)) {
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    fs.writeFileSync(filePath, `${encoded}\n`, "utf8");
    return;
  }

  const lines = fs.readFileSync(filePath, "utf8").split(/\r?\n/);
  const output = [];
  let replaced = false;
  for (const raw of lines) {
    if (raw.trim().startsWith(`${normalizedKey}=`)) {
      output.push(encoded);
      replaced = true;
    } else if (raw !== "" || output.length > 0) {
      output.push(raw);
    }
  }

  if (!replaced) {
    if (output.length > 0 && output[output.length - 1].trim()) {
      output.push("");
    }
    output.push(encoded);
  }
  fs.writeFileSync(filePath, `${output.join("\n").replace(/\n+$/u, "")}\n`, "utf8");
}
