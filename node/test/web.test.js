import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import process from "node:process";
import { fileURLToPath } from "node:url";

import {
  loadConfigPayload,
  loadLatestResults,
  validateConfigPayload,
  writeConfigPayload,
} from "../src/runtime/web.js";

const nodeBin = process.execPath;
const nodeBinDir = path.dirname(nodeBin);
const testDir = path.dirname(fileURLToPath(import.meta.url));
const cliPath = path.resolve(testDir, "../bin/llm-usage-node.js");

test("top-level help shows web command", () => {
  const result = spawnSync(nodeBin, [cliPath, "--help"], {
    cwd: path.resolve(testDir, ".."),
    encoding: "utf8",
    env: {
      ...process.env,
      PATH: nodeBinDir,
    },
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /\bweb\b/u);
});

test("web help shows local console flags", () => {
  const result = spawnSync(nodeBin, [cliPath, "web", "--help"], {
    cwd: path.resolve(testDir, ".."),
    encoding: "utf8",
    env: {
      ...process.env,
      PATH: nodeBinDir,
    },
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /--host/u);
  assert.match(result.stdout, /--port/u);
  assert.match(result.stdout, /--no-open/u);
});

test("web helpers load config validate payload and results", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "llm-usage-node-web-"));
  const dataDir = path.join(root, "data");
  const envFile = path.join(root, "config", ".env");
  fs.mkdirSync(path.dirname(envFile), { recursive: true });
  fs.mkdirSync(path.join(dataDir, "reports"), { recursive: true });
  fs.writeFileSync(
    envFile,
    [
      "ORG_USERNAME=san.zhang",
      "HASH_SALT=test-salt",
      "TIMEZONE=Asia/Shanghai",
      "LOOKBACK_DAYS=30",
      "FEISHU_APP_TOKEN=app-token",
      "FEISHU_TARGETS=team_b",
      "FEISHU_TEAM_B_APP_TOKEN=team-token",
      "REMOTE_HOSTS=server_a",
      "REMOTE_SERVER_A_SSH_HOST=host-a",
      "REMOTE_SERVER_A_SSH_USER=alice",
      "",
    ].join("\n"),
    "utf8",
  );
  fs.writeFileSync(
    path.join(dataDir, "reports", "usage_report.csv"),
    [
      "date_local,user_hash,source_host_hash,tool,model,input_tokens_sum,cache_tokens_sum,output_tokens_sum,row_key,updated_at",
      "2026-04-06,user-a,host-a,codex,gpt-5,10,2,3,row-1,2026-04-06T10:00:00+08:00",
      "",
    ].join("\n"),
    "utf8",
  );

  Object.assign(process.env, {
    ...process.env,
    LLM_USAGE_ENV_FILE: envFile,
    LLM_USAGE_DATA_DIR: dataDir,
  });

  const configPayload = loadConfigPayload();
  assert.equal(configPayload.basic.ORG_USERNAME, "san.zhang");
  assert.equal(configPayload.feishu_targets[0].name, "team_b");
  assert.equal(configPayload.remotes[0].alias, "SERVER_A");

  const resultsPayload = loadLatestResults();
  assert.equal(resultsPayload.rows[0].tool, "codex");

  const validatePayload = validateConfigPayload({ feishu_targets: [{ name: "bad-name" }] });
  assert.equal(validatePayload.ok, false);
  assert.equal(Array.isArray(validatePayload.errors), true);

  const savePayload = writeConfigPayload({
    basic: {
      ORG_USERNAME: "san.zhang",
      HASH_SALT: "test-salt",
      TIMEZONE: "Asia/Shanghai",
      LOOKBACK_DAYS: "14",
    },
    cursor: {},
    feishu_default: { FEISHU_APP_TOKEN: "app-token" },
    feishu_targets: [{ name: "team_b", app_token: "team-token" }],
    remotes: [],
    raw_env: [],
  });
  assert.equal(savePayload.ok, true);
  assert.match(fs.readFileSync(envFile, "utf8"), /LOOKBACK_DAYS=14/u);
});
