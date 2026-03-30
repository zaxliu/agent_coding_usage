import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";

import {
  _defaultManagedProfileDir,
  _openUrlInSystemBrowser,
  fetchCursorSessionTokenViaBrowser,
  maybeCaptureCursorToken,
  resolveCursorLoginMode,
  saveCursorWebCredentials,
} from "../src/runtime/cursor-login.js";

function withPlatform(platform, fn) {
  const descriptor = Object.getOwnPropertyDescriptor(process, "platform");
  Object.defineProperty(process, "platform", { value: platform });
  try {
    return fn();
  } finally {
    Object.defineProperty(process, "platform", descriptor);
  }
}

test("resolveCursorLoginMode routes Windows Chromium auto to managed-profile", () => {
  assert.equal(resolveCursorLoginMode("auto", "chrome", "win32"), "managed-profile");
  assert.equal(resolveCursorLoginMode("auto", "default", "win32"), "auto");
  assert.equal(resolveCursorLoginMode("auto", "firefox", "win32"), "auto");
  assert.equal(resolveCursorLoginMode("manual", "chrome", "win32"), "manual");
});

test("defaultManagedProfileDir uses LOCALAPPDATA on Windows", () => {
  const originalLocalAppData = process.env.LOCALAPPDATA;
  process.env.LOCALAPPDATA = String.raw`C:\Users\me\AppData\Local`;
  try {
    const profile = _defaultManagedProfileDir("chrome", "win32");
    assert.match(profile, /llm-usage/i);
    assert.match(profile, /cursor-login/i);
    assert.match(profile, /chrome-profile/i);
  } finally {
    if (originalLocalAppData === undefined) {
      delete process.env.LOCALAPPDATA;
    } else {
      process.env.LOCALAPPDATA = originalLocalAppData;
    }
  }
});

test("openUrlInSystemBrowser launches Windows managed Chromium profile", async () => {
  const calls = [];
  await withPlatform("win32", async () => {
    await _openUrlInSystemBrowser("https://cursor.com/dashboard/usage", {
      browser: "chrome",
      userDataDir: String.raw`C:\tmp\cursor-profile`,
      spawnProcess: async (command, args) => {
        calls.push([command, ...args]);
      },
      resolveWindowsBrowserCommand: () => [String.raw`C:\Chrome\chrome.exe`],
    });
  });

  assert.deepEqual(calls, [
    [
      String.raw`C:\Chrome\chrome.exe`,
      String.raw`--user-data-dir=C:\tmp\cursor-profile`,
      "--no-first-run",
      "--new-window",
      "https://cursor.com/dashboard/usage",
    ],
  ]);
});

test("fetchCursorSessionTokenViaBrowser managed-profile reads explicit profile", async () => {
  const calls = [];
  const token = await fetchCursorSessionTokenViaBrowser({
    timeoutSec: 30,
    browser: "chrome",
    userDataDir: "C:/tmp/cursor-profile",
    loginMode: "managed-profile",
    openBrowser: async ({ url, browser, userDataDir }) => {
      calls.push({ url, browser, userDataDir });
    },
    readManagedProfileCandidates: async () => ["token-abc"],
    findValidToken: async (candidates) => candidates[0] || null,
    sleep: async () => {},
  });

  assert.equal(token, "token-abc");
  assert.deepEqual(calls, [
    {
      url: "https://cursor.com/dashboard/usage",
      browser: "chrome",
      userDataDir: "C:/tmp/cursor-profile",
    },
  ]);
});

test("maybeCaptureCursorToken routes Windows Chromium auto to managed-profile", async () => {
  const calls = [];
  await maybeCaptureCursorToken({
    timeoutSec: 60,
    browser: "chrome",
    userDataDir: "",
    loginMode: "auto",
    platform: "win32",
    env: { CURSOR_WEB_SESSION_TOKEN: "" },
    loadEnv: () => {},
    buildCursorCollector: () => ({
      async probe() {
        return { ok: false, message: "cursor dashboard unavailable" };
      },
    }),
    captureAndSaveCursorToken: async ({ timeoutSec, browser, userDataDir, loginMode }) => {
      calls.push({ timeoutSec, browser, userDataDir, loginMode });
      return "token-from-browser";
    },
    promptForManualCursorToken: async () => null,
    logInfo: () => {},
    logWarn: () => {},
  });

  assert.deepEqual(calls, [
    {
      timeoutSec: 60,
      browser: "chrome",
      userDataDir: "",
      loginMode: "managed-profile",
    },
  ]);
});

test("saveCursorWebCredentials writes token and workos id to env file", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "llm-usage-node-cursor-login-"));
  const envPath = path.join(root, ".env");
  saveCursorWebCredentials({ envPath, token: "token-abc", workosId: "workos-1", environ: process.env });
  const text = fs.readFileSync(envPath, "utf8");
  assert.match(text, /CURSOR_WEB_SESSION_TOKEN=token-abc/u);
  assert.match(text, /CURSOR_WEB_WORKOS_ID=workos-1/u);
});
