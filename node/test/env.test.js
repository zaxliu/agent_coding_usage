import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";

import {
  getEnvPath,
  getRuntimeStatePath,
  loadDotenv,
  prepareRuntimePaths,
  resetRuntimePathsCache,
} from "../src/runtime/env.js";

function withPlatform(platform, fn) {
  const descriptor = Object.getOwnPropertyDescriptor(process, "platform");
  Object.defineProperty(process, "platform", { value: platform });
  try {
    return fn();
  } finally {
    Object.defineProperty(process, "platform", descriptor);
  }
}

test("prepareRuntimePaths resolves macOS native defaults", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "llm-usage-home-"));
  const originalHome = process.env.HOME;
  const originalEnvFile = process.env.LLM_USAGE_ENV_FILE;
  const originalDataDir = process.env.LLM_USAGE_DATA_DIR;
  process.env.HOME = home;
  delete process.env.LLM_USAGE_ENV_FILE;
  delete process.env.LLM_USAGE_DATA_DIR;
  resetRuntimePathsCache();

  await withPlatform("darwin", async () => {
    const resolved = await prepareRuntimePaths(path.join(home, "repo"));
    assert.equal(resolved.envPath, path.join(home, "Library", "Application Support", "llm-usage", ".env"));
    assert.equal(
      resolved.runtimeStatePath,
      path.join(home, "Library", "Application Support", "llm-usage", "runtime_state.json"),
    );
  });

  process.env.HOME = originalHome;
  if (originalEnvFile === undefined) {
    delete process.env.LLM_USAGE_ENV_FILE;
  } else {
    process.env.LLM_USAGE_ENV_FILE = originalEnvFile;
  }
  if (originalDataDir === undefined) {
    delete process.env.LLM_USAGE_DATA_DIR;
  } else {
    process.env.LLM_USAGE_DATA_DIR = originalDataDir;
  }
});

test("prepareRuntimePaths resolves Windows APPDATA defaults", async () => {
  const appData = fs.mkdtempSync(path.join(os.tmpdir(), "llm-usage-appdata-"));
  const originalAppData = process.env.APPDATA;
  delete process.env.LLM_USAGE_ENV_FILE;
  delete process.env.LLM_USAGE_DATA_DIR;
  process.env.APPDATA = appData;
  resetRuntimePathsCache();

  await withPlatform("win32", async () => {
    const resolved = await prepareRuntimePaths(path.join(appData, "repo"));
    assert.equal(resolved.envPath, path.join(appData, "llm-usage", ".env"));
    assert.equal(resolved.runtimeStatePath, path.join(appData, "llm-usage", "runtime_state.json"));
  });

  if (originalAppData === undefined) {
    delete process.env.APPDATA;
  } else {
    process.env.APPDATA = originalAppData;
  }
});

test("prepareRuntimePaths respects explicit overrides", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "llm-usage-repo-"));
  const envFile = path.join(root, "custom", ".env");
  const dataDir = path.join(root, "data");
  process.env.LLM_USAGE_ENV_FILE = envFile;
  process.env.LLM_USAGE_DATA_DIR = dataDir;
  resetRuntimePathsCache();

  const resolved = await prepareRuntimePaths(root);
  assert.equal(resolved.envPath, envFile);
  assert.equal(resolved.runtimeStatePath, path.join(dataDir, "runtime_state.json"));
  assert.equal(getEnvPath(), envFile);
  assert.equal(getRuntimeStatePath(), path.join(dataDir, "runtime_state.json"));

  delete process.env.LLM_USAGE_ENV_FILE;
  delete process.env.LLM_USAGE_DATA_DIR;
});

test("prepareRuntimePaths falls back to legacy env in non-interactive mode", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "llm-usage-legacy-"));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "llm-usage-legacy-home-"));
  fs.writeFileSync(path.join(root, ".env"), "ORG_USERNAME=alice\n", "utf8");
  const originalHome = process.env.HOME;
  process.env.HOME = home;
  delete process.env.LLM_USAGE_ENV_FILE;
  delete process.env.LLM_USAGE_DATA_DIR;
  resetRuntimePathsCache();

  const originalStdin = process.stdin.isTTY;
  const originalStdout = process.stdout.isTTY;
  process.stdin.isTTY = false;
  process.stdout.isTTY = false;
  const resolved = await prepareRuntimePaths(root);
  process.stdin.isTTY = originalStdin;
  process.stdout.isTTY = originalStdout;

  assert.equal(resolved.envPath, path.join(root, ".env"));
  if (originalHome === undefined) {
    delete process.env.HOME;
  } else {
    process.env.HOME = originalHome;
  }
});

test("loadDotenv bootstraps missing user env from package template", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "llm-usage-bootstrap-"));
  const envFile = path.join(root, "user", ".env");
  process.env.LLM_USAGE_ENV_FILE = envFile;
  process.env.LLM_USAGE_DATA_DIR = path.join(root, "data");
  delete process.env.HASH_SALT;
  resetRuntimePathsCache();

  await prepareRuntimePaths(root);
  loadDotenv();

  const text = fs.readFileSync(envFile, "utf8");
  assert.match(text, /HASH_SALT=/);
  assert.equal(typeof process.env.HASH_SALT, "string");

  delete process.env.LLM_USAGE_ENV_FILE;
  delete process.env.LLM_USAGE_DATA_DIR;
  delete process.env.HASH_SALT;
});

test("explicit env and data overrides do not fall back to legacy repo paths", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "llm-usage-explicit-"));
  const envFile = path.join(root, "config", ".env");
  const dataDir = path.join(root, "data");
  fs.mkdirSync(path.dirname(envFile), { recursive: true });
  fs.mkdirSync(path.join(root, "reports"), { recursive: true });
  fs.writeFileSync(path.join(root, ".env"), "ORG_USERNAME=legacy\n", "utf8");
  fs.writeFileSync(path.join(root, "reports", "runtime_state.json"), "{\"selected_remote_aliases\":[\"LEGACY\"]}\n", "utf8");

  process.env.LLM_USAGE_ENV_FILE = envFile;
  process.env.LLM_USAGE_DATA_DIR = dataDir;
  resetRuntimePathsCache();

  await prepareRuntimePaths(root);

  assert.equal(getEnvPath(), envFile);
  assert.equal(getRuntimeStatePath(), path.join(dataDir, "runtime_state.json"));

  delete process.env.LLM_USAGE_ENV_FILE;
  delete process.env.LLM_USAGE_DATA_DIR;
});
