import { spawn, spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { DatabaseSync } from "node:sqlite";

import { getEnv, intEnv, loadDotenv, upsertEnvVar } from "./env.js";
import { isInteractive, promptLine } from "./ui.js";

const TOKEN_COOKIE_NAME = "WorkosCursorSessionToken";
const WORKOS_ID_COOKIE_NAME = "workos_id";
const CURSOR_DOMAIN = "cursor.com";
const CURSOR_BASE_URL = "https://cursor.com";

function normalizeBrowserName(browser) {
  const value = String(browser || "").trim().toLowerCase();
  if (value === "edge") {
    return "msedge";
  }
  if (value === "webkit") {
    return "safari";
  }
  return value || "default";
}

export function resolveCursorLoginMode(loginMode, browser, platform = process.platform) {
  const normalizedMode = String(loginMode || "auto").trim().toLowerCase() || "auto";
  const normalizedBrowser = normalizeBrowserName(browser || "default");
  if (normalizedMode !== "auto") {
    return normalizedMode;
  }
  if (platform === "win32" && ["chrome", "chromium", "edge", "msedge"].includes(normalizedBrowser)) {
    return "managed-profile";
  }
  return "auto";
}

export function _defaultManagedProfileDir(browser, platform = process.platform) {
  const normalized = normalizeBrowserName(browser);
  const slug = normalized === "msedge" ? "edge-profile" : "chrome-profile";
  if (platform === "win32") {
    const root = String(process.env.LOCALAPPDATA || "").trim() || path.join(os.homedir(), "AppData", "Local");
    return path.join(root, "llm-usage", "cursor-login", slug);
  }
  return path.join(os.homedir(), ".llm-usage", "cursor-login", slug);
}

function windowsBrowserCommand(browser) {
  const normalized = normalizeBrowserName(browser);
  const candidates = {
    chrome: [
      String.raw`${process.env.ProgramFiles || "C:\\Program Files"}\Google\Chrome\Application\chrome.exe`,
      String.raw`${process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)"}\Google\Chrome\Application\chrome.exe`,
    ],
    msedge: [
      String.raw`${process.env.ProgramFiles || "C:\\Program Files"}\Microsoft\Edge\Application\msedge.exe`,
      String.raw`${process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)"}\Microsoft\Edge\Application\msedge.exe`,
    ],
    chromium: [String.raw`${process.env.ProgramFiles || "C:\\Program Files"}\Chromium\Application\chrome.exe`],
  };
  for (const candidate of candidates[normalized] || []) {
    if (candidate && fs.existsSync(candidate)) {
      return [candidate];
    }
  }
  return null;
}

function macosAppName(browser) {
  const mapping = {
    chrome: "Google Chrome",
    msedge: "Microsoft Edge",
    safari: "Safari",
    firefox: "Firefox",
    chromium: "Chromium",
  };
  return mapping[normalizeBrowserName(browser)] || null;
}

function linuxBrowserCommand(browser) {
  const mapping = {
    chrome: "google-chrome",
    msedge: "microsoft-edge",
    firefox: "firefox",
    chromium: "chromium-browser",
  };
  return mapping[normalizeBrowserName(browser)] || null;
}

function spawnProcess(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      detached: true,
      stdio: "ignore",
    });
    child.once("error", reject);
    child.once("spawn", () => {
      child.unref();
      resolve();
    });
  });
}

export async function _openUrlInSystemBrowser(
  url,
  {
    browser = "default",
    userDataDir = "",
    platform = process.platform,
    spawnProcess: openProcess = spawnProcess,
    resolveWindowsBrowserCommand = windowsBrowserCommand,
  } = {},
) {
  const normalized = normalizeBrowserName(browser);
  if (platform === "win32") {
    const command = resolveWindowsBrowserCommand(normalized);
    if (userDataDir && command && ["chrome", "chromium", "msedge"].includes(normalized)) {
      await openProcess(command[0], [...command.slice(1), `--user-data-dir=${userDataDir}`, "--no-first-run", "--new-window", url]);
      return;
    }
    if (command) {
      await openProcess(command[0], [...command.slice(1), url]);
      return;
    }
    await openProcess("cmd", ["/c", "start", "", url]);
    return;
  }
  if (platform === "darwin") {
    const appName = macosAppName(normalized);
    if (appName) {
      await openProcess("open", ["-a", appName, url]);
      return;
    }
    await openProcess("open", [url]);
    return;
  }
  const command = linuxBrowserCommand(normalized);
  if (command) {
    await openProcess(command, [url]);
    return;
  }
  await openProcess("xdg-open", [url]);
}

function chromiumCookieFilesFromUserDataDir(userDataDir) {
  const roots = ["Default", "Profile 1", "Profile 2", "Profile 3", "Profile 4", "Guest Profile", "System Profile"];
  const rels = ["Cookies", path.join("Network", "Cookies")];
  const out = [];
  for (const root of roots) {
    for (const rel of rels) {
      const cookieFile = path.join(userDataDir, root, rel);
      if (fs.existsSync(cookieFile)) {
        out.push(cookieFile);
      }
    }
  }
  return out;
}

function chromiumKeyFileForCookieFile(cookieFile) {
  const lower = cookieFile.toLowerCase();
  if (!lower.endsWith(`${path.sep}cookies`)) {
    return null;
  }
  const parent = path.dirname(cookieFile);
  const userDataRoot = path.basename(parent).toLowerCase() === "network" ? path.dirname(path.dirname(parent)) : path.dirname(parent);
  const localState = path.join(userDataRoot, "Local State");
  return fs.existsSync(localState) ? localState : null;
}

function dpapiUnprotectWindows(bytes) {
  const encoded = Buffer.from(bytes).toString("base64");
  const command = [
    "[Convert]::ToBase64String(",
    "[System.Security.Cryptography.ProtectedData]::Unprotect(",
    `[Convert]::FromBase64String('${encoded}'),`,
    "$null,",
    "[System.Security.Cryptography.DataProtectionScope]::CurrentUser))",
  ].join("");
  const result = spawnSync("powershell", ["-NoProfile", "-Command", command], {
    encoding: "utf8",
  });
  if (result.status !== 0) {
    throw new Error((result.stderr || result.stdout || "powershell dpapi decrypt failed").trim());
  }
  return Buffer.from(String(result.stdout || "").trim(), "base64");
}

function readChromiumMasterKey(localStatePath, platform = process.platform) {
  if (!localStatePath || !fs.existsSync(localStatePath)) {
    return null;
  }
  const payload = JSON.parse(fs.readFileSync(localStatePath, "utf8"));
  const encoded = payload?.os_crypt?.encrypted_key;
  if (typeof encoded !== "string" || !encoded.trim()) {
    return null;
  }
  const raw = Buffer.from(encoded, "base64");
  if (platform === "win32" && raw.subarray(0, 5).toString("utf8") === "DPAPI") {
    return dpapiUnprotectWindows(raw.subarray(5));
  }
  return null;
}

function decryptChromiumCookieValue(encryptedValue, { localStatePath = null, platform = process.platform } = {}) {
  const bytes = Buffer.isBuffer(encryptedValue) ? encryptedValue : Buffer.from(encryptedValue || []);
  if (!bytes.length) {
    return "";
  }
  const prefix = bytes.subarray(0, 3).toString("utf8");
  if (platform === "win32" && (prefix === "v10" || prefix === "v11")) {
    const masterKey = readChromiumMasterKey(localStatePath, platform);
    if (!masterKey) {
      return "";
    }
    const nonce = bytes.subarray(3, 15);
    const ciphertext = bytes.subarray(15, -16);
    const tag = bytes.subarray(-16);
    const decipher = crypto.createDecipheriv("aes-256-gcm", masterKey, nonce);
    decipher.setAuthTag(tag);
    return Buffer.concat([decipher.update(ciphertext), decipher.final()]).toString("utf8");
  }
  if (platform === "win32") {
    return dpapiUnprotectWindows(bytes).toString("utf8");
  }
  return bytes.toString("utf8").replace(/^[\u0000-\u001f]+/u, "").trim();
}

function readCookieCandidatesFromChromiumProfile({ cookieName, userDataDir, platform = process.platform }) {
  const out = [];
  for (const cookieFile of chromiumCookieFilesFromUserDataDir(userDataDir)) {
    const tempDb = path.join(os.tmpdir(), `llm-usage-cursor-cookie-${process.pid}-${Date.now()}-${Math.random()}.sqlite`);
    try {
      fs.copyFileSync(cookieFile, tempDb);
      const db = new DatabaseSync(tempDb, { readonly: true });
      const rows = db
        .prepare(
          "SELECT host_key, name, value, encrypted_value FROM cookies WHERE name = ? AND (host_key = ? OR host_key LIKE ?)",
        )
        .all(cookieName, CURSOR_DOMAIN, `%.${CURSOR_DOMAIN}`);
      db.close();
      const localStatePath = chromiumKeyFileForCookieFile(cookieFile);
      for (const row of rows) {
        const plain = String(row.value || "").trim();
        const value =
          plain ||
          decryptChromiumCookieValue(row.encrypted_value, {
            localStatePath,
            platform,
          }).trim();
        if (value && !out.includes(value)) {
          out.push(value);
        }
      }
    } catch {
      // ignore unreadable cookie stores
    } finally {
      if (fs.existsSync(tempDb)) {
        fs.rmSync(tempDb, { force: true });
      }
    }
  }
  return out;
}

async function validateCursorSessionToken(token, workosId = "", { fetchImpl = fetch, baseUrl = CURSOR_BASE_URL } = {}) {
  const end = Date.now();
  const start = end - 24 * 60 * 60 * 1000;
  const payloads = [
    { teamId: 0, startDate: String(start), endDate: String(end), page: 1, pageSize: 1 },
    { startDate: String(start), endDate: String(end), page: 1, pageSize: 1 },
  ];
  for (const body of payloads) {
    try {
      const response = await fetchImpl(`${baseUrl}/api/dashboard/get-filtered-usage-events`, {
        method: "POST",
        headers: {
          Accept: "application/json, text/plain, */*",
          "Content-Type": "application/json",
          Origin: baseUrl,
          Referer: `${baseUrl}/dashboard/usage`,
          Cookie: [`${TOKEN_COOKIE_NAME}=${token}`, workosId ? `${WORKOS_ID_COOKIE_NAME}=${workosId}` : ""]
            .filter(Boolean)
            .join("; "),
        },
        body: JSON.stringify(body),
      });
      if (response.status === 401 || response.status === 403) {
        const text = await response.text();
        return { ok: false, reason: `authentication failed (${response.status}): ${text.slice(0, 140)}` };
      }
      if (response.status >= 400) {
        return { ok: false, reason: `http error ${response.status}` };
      }
      const payload = await response.json().catch(() => ({}));
      if (Array.isArray(payload?.usageEventsDisplay)) {
        return { ok: true, reason: "ok" };
      }
      return { ok: true, reason: "ok" };
    } catch (error) {
      return { ok: false, reason: `request failed: ${error instanceof Error ? error.message : String(error)}` };
    }
  }
  return { ok: false, reason: "authentication failed" };
}

async function findValidToken(candidates, deps = {}) {
  for (const token of candidates || []) {
    const result = await validateCursorSessionToken(token, "", deps);
    if (result.ok) {
      return token;
    }
  }
  return candidates?.[0] || null;
}

async function readManagedProfileCookieCandidates({
  browser,
  userDataDir,
  cookieName = TOKEN_COOKIE_NAME,
  platform = process.platform,
}) {
  return readCookieCandidatesFromChromiumProfile({
    cookieName,
    userDataDir,
    platform,
    browser,
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchCursorWorkosIdFromManagedProfile({ browser, userDataDir, platform = process.platform }) {
  const candidates = await readManagedProfileCookieCandidates({
    browser,
    userDataDir,
    cookieName: WORKOS_ID_COOKIE_NAME,
    platform,
  });
  return candidates[0] || "";
}

export async function fetchCursorSessionTokenViaBrowser({
  usageUrl = `${CURSOR_BASE_URL}/dashboard/usage`,
  timeoutSec = 600,
  browser = "default",
  userDataDir = "",
  loginMode = "auto",
  platform = process.platform,
  openBrowser = async ({ url, browser, userDataDir }) =>
    _openUrlInSystemBrowser(url, { browser, userDataDir, platform }),
  readManagedProfileCandidates = async ({ browser, userDataDir }) =>
    readManagedProfileCookieCandidates({ browser, userDataDir, platform }),
  findValidToken: pickToken = async (candidates) => findValidToken(candidates),
  sleep: sleepFn = sleep,
} = {}) {
  const resolvedMode = String(loginMode || "auto").trim().toLowerCase() || "auto";
  if (resolvedMode !== "managed-profile") {
    throw new Error(`cursor login mode not yet supported in Node: ${resolvedMode}`);
  }

  const managedDir = String(userDataDir || "").trim() || _defaultManagedProfileDir(browser, platform);
  fs.mkdirSync(managedDir, { recursive: true });
  await openBrowser({ url: usageUrl, browser, userDataDir: managedDir });

  const deadline = Date.now() + Math.max(30, timeoutSec) * 1000;
  while (Date.now() < deadline) {
    const candidates = await readManagedProfileCandidates({ browser, userDataDir: managedDir });
    const token = await pickToken(candidates);
    if (token) {
      return token;
    }
    await sleepFn(2000);
  }
  throw new Error("timed out waiting for Cursor session cookie in managed browser profile");
}

export function saveCursorWebCredentials({ envPath, token, workosId = "", environ = process.env }) {
  upsertEnvVar("CURSOR_WEB_SESSION_TOKEN", token, envPath);
  upsertEnvVar("CURSOR_WEB_WORKOS_ID", workosId, envPath);
  environ.CURSOR_WEB_SESSION_TOKEN = token;
  if (workosId) {
    environ.CURSOR_WEB_WORKOS_ID = workosId;
  } else {
    delete environ.CURSOR_WEB_WORKOS_ID;
  }
}

function clearSavedCursorToken({ envPath, environ = process.env }) {
  upsertEnvVar("CURSOR_WEB_SESSION_TOKEN", "", envPath);
  upsertEnvVar("CURSOR_WEB_WORKOS_ID", "", envPath);
  delete environ.CURSOR_WEB_SESSION_TOKEN;
  delete environ.CURSOR_WEB_WORKOS_ID;
}

export async function captureAndSaveCursorToken({
  timeoutSec,
  browser,
  userDataDir,
  loginMode = "auto",
  envPath,
  environ = process.env,
  platform = process.platform,
}) {
  const token = await fetchCursorSessionTokenViaBrowser({
    timeoutSec,
    browser,
    userDataDir,
    loginMode,
    platform,
  });
  const managedDir = String(userDataDir || "").trim() || _defaultManagedProfileDir(browser, platform);
  const workosId = await fetchCursorWorkosIdFromManagedProfile({
    browser,
    userDataDir: managedDir,
    platform,
  });
  saveCursorWebCredentials({ envPath, token, workosId, environ });
  return token;
}

async function promptForManualCursorToken({
  browser,
  automaticCaptureFailed,
  envPath,
  environ = process.env,
  platform = process.platform,
  openBrowser = async ({ url, browser }) => _openUrlInSystemBrowser(url, { browser, platform }),
}) {
  if (!isInteractive()) {
    return null;
  }
  if (automaticCaptureFailed) {
    console.log("warn: automatic Cursor token capture failed.");
  }
  try {
    await openBrowser({ url: `${CURSOR_BASE_URL}/dashboard/usage`, browser });
    console.log("info: opened https://cursor.com/dashboard/usage in your browser.");
  } catch (error) {
    console.log(`warn: failed to open browser automatically: ${error instanceof Error ? error.message : String(error)}`);
  }
  console.log("info: after login, open DevTools > Application > Cookies and copy WorkosCursorSessionToken.");
  const token = (await promptLine("CURSOR_WEB_SESSION_TOKEN (press Enter to skip): ")).trim();
  if (!token) {
    return null;
  }
  saveCursorWebCredentials({ envPath, token, workosId: "", environ });
  return token;
}

export async function maybeCaptureCursorToken({
  timeoutSec = 600,
  browser = "default",
  userDataDir = "",
  loginMode = "auto",
  lookbackDays = intEnv("LOOKBACK_DAYS", 7),
  env = process.env,
  envPath = getEnv("LLM_USAGE_ENV_FILE") || undefined,
  platform = process.platform,
  loadEnv = () => loadDotenv(),
  buildCursorCollector,
  captureAndSaveCursorToken: captureToken = captureAndSaveCursorToken,
  promptForManualCursorToken: promptToken = promptForManualCursorToken,
  logInfo = console.log,
  logWarn = console.log,
} = {}) {
  loadEnv();
  const effectiveLoginMode = resolveCursorLoginMode(loginMode, browser, platform);
  const collector = buildCursorCollector();

  if (String(env.CURSOR_WEB_SESSION_TOKEN || "").trim()) {
    const probe = await collector.probe();
    if (probe.ok) {
      return null;
    }
    if (String(probe.message || "").toLowerCase().includes("authentication failed")) {
      logWarn("warn: existing CURSOR_WEB_SESSION_TOKEN appears expired; clearing saved token and requesting a fresh login...");
      clearSavedCursorToken({ envPath, environ: env });
      try {
        await captureToken({ timeoutSec, browser, userDataDir, loginMode: effectiveLoginMode, envPath, environ: env, platform });
        return null;
      } catch (error) {
        logWarn(`warn: ${effectiveLoginMode} cursor login failed: ${error instanceof Error ? error.message : String(error)}`);
        if (
          await promptToken({
            browser,
            automaticCaptureFailed: true,
            envPath,
            environ: env,
            platform,
          })
        ) {
          return null;
        }
        return null;
      }
    }
    return `cursor dashboard probe failed with existing token: ${probe.message}`;
  }

  const probe = await collector.probe();
  if (probe.ok && typeof collector.collect === "function") {
    const end = new Date();
    const start = new Date(end.getTime() - Math.max(1, lookbackDays) * 24 * 60 * 60 * 1000);
    const localOut = await collector.collect(start, end);
    if (localOut?.events?.length) {
      return null;
    }
    logInfo("info: cursor local logs found but no events in selected lookback; opening browser login...");
  } else {
    logInfo("info: CURSOR_WEB_SESSION_TOKEN is empty and local cursor logs are unavailable; opening browser login...");
  }

  if (effectiveLoginMode === "manual") {
    if (await promptToken({ browser, automaticCaptureFailed: false, envPath, environ: env, platform })) {
      return null;
    }
    logWarn("warn: continuing with local cursor sources");
    return probe.message || null;
  }

  try {
    await captureToken({ timeoutSec, browser, userDataDir, loginMode: effectiveLoginMode, envPath, environ: env, platform });
    return null;
  } catch (error) {
    logWarn(`warn: ${effectiveLoginMode} cursor login failed: ${error instanceof Error ? error.message : String(error)}`);
    if (await promptToken({ browser, automaticCaptureFailed: true, envPath, environ: env, platform })) {
      return null;
    }
    logWarn("warn: continuing with local cursor sources");
    return probe.message || null;
  }
}
