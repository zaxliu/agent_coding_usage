import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const thisDir = path.dirname(fileURLToPath(import.meta.url));
const nodeRoot = path.resolve(thisDir, "..");
const repoRoot = path.resolve(nodeRoot, "..");
const outputDir = path.join(repoRoot, "dist");
const stamp = new Date().toISOString().replace(/[-:]/g, "").replace("T", "_").slice(0, 15);

const clearForAll = new Set([
  "ORG_USERNAME",
  "CURSOR_WEB_SESSION_TOKEN",
  "CURSOR_WEB_WORKOS_ID",
  "CLAUDE_LOG_PATHS",
  "CODEX_LOG_PATHS",
  "COPILOT_CLI_LOG_PATHS",
  "COPILOT_VSCODE_SESSION_PATHS",
  "CURSOR_LOG_PATHS",
]);

const clearForExternal = new Set([
  "HASH_SALT",
  "FEISHU_APP_TOKEN",
  "FEISHU_TABLE_ID",
  "FEISHU_APP_ID",
  "FEISHU_APP_SECRET",
  "FEISHU_BOT_TOKEN",
]);

const resetDefaults = new Map([
  ["CURSOR_DASHBOARD_BASE_URL", "https://cursor.com"],
  ["CURSOR_DASHBOARD_TEAM_ID", "0"],
  ["CURSOR_DASHBOARD_PAGE_SIZE", "300"],
  ["CURSOR_DASHBOARD_TIMEOUT_SEC", "15"],
]);

function sanitizeEnv(text, profile) {
  const values = new Map();
  for (const line of text.split(/\r?\n/)) {
    const match = /^([A-Z0-9_]+)=(.*)$/.exec(line.trim());
    if (match) {
      values.set(match[1], match[2]);
    }
  }

  for (const key of clearForAll) {
    values.set(key, "");
  }
  if (profile === "external") {
    for (const key of clearForExternal) {
      values.set(key, "");
    }
  }
  for (const [key, value] of resetDefaults) {
    values.set(key, value);
  }
  for (const key of [...values.keys()]) {
    if (key.startsWith("REMOTE_")) {
      values.set(key, "");
    }
  }

  return `${[...values.entries()].map(([key, value]) => `${key}=${value}`).join("\n")}\n`;
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function copyFile(source, target) {
  ensureDir(path.dirname(target));
  fs.copyFileSync(source, target);
}

function writeRuntimeFiles(stagingRoot, profile) {
  copyFile(path.join(repoRoot, "README.md"), path.join(stagingRoot, "README.md"));
  copyFile(path.join(nodeRoot, "package.json"), path.join(stagingRoot, "package.json"));

  for (const relative of [
    "bin/llm-usage-node.js",
    "bridge/collector_bridge.py",
    "src/core/hash.js",
    "src/core/identity.js",
    "src/core/models.js",
    "src/core/time.js",
    "src/core/aggregation.js",
    "src/core/privacy.js",
    "src/cli/main.js",
    "src/runtime/env.js",
    "src/runtime/feishu.js",
    "src/runtime/python-bridge.js",
    "src/runtime/reporting.js",
  ]) {
    copyFile(path.join(nodeRoot, relative), path.join(stagingRoot, "node", relative));
  }

  copyFile(
    path.join(repoRoot, "spec", "parity-vectors", "hash_vectors.json"),
    path.join(stagingRoot, "spec", "parity-vectors", "hash_vectors.json"),
  );
  copyFile(
    path.join(repoRoot, "spec", "parity-vectors", "aggregation_vectors.json"),
    path.join(stagingRoot, "spec", "parity-vectors", "aggregation_vectors.json"),
  );

  const envExample = fs.readFileSync(path.join(repoRoot, ".env.example"), "utf8");
  fs.writeFileSync(path.join(stagingRoot, ".env.example"), sanitizeEnv(envExample, profile), "utf8");
  ensureDir(path.join(stagingRoot, "reports"));
}

function buildArchive(profile) {
  const bundleName = `agent_coding_usage_node_${profile}_${stamp}`;
  const stagingParent = fs.mkdtempSync(path.join(os.tmpdir(), "llm_usage_node_bundle_"));
  const stagingRoot = path.join(stagingParent, bundleName);
  ensureDir(stagingRoot);
  writeRuntimeFiles(stagingRoot, profile);
  ensureDir(outputDir);

  const archivePath = path.join(outputDir, `${bundleName}.tar.gz`);
  const result = spawnSync("tar", ["-czf", archivePath, "-C", stagingParent, bundleName], {
    stdio: "inherit",
  });
  if (result.status !== 0) {
    throw new Error(`tar failed for ${profile}`);
  }
  console.log(`${profile}: ${archivePath}`);
}

buildArchive("internal");
buildArchive("external");
