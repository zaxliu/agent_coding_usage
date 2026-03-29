import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import process from "node:process";
import { fileURLToPath } from "node:url";

const nodeBin = process.execPath;
const nodeBinDir = path.dirname(nodeBin);
const testDir = path.dirname(fileURLToPath(import.meta.url));
const cliPath = path.resolve(testDir, "../bin/llm-usage-node.js");

function writeCodexLog(rootDir) {
  const logPath = path.join(rootDir, "codex.jsonl");
  const lines = [
    JSON.stringify({
      type: "turn_context",
      payload: {
        collaboration_mode: {
          settings: {
            model: "gpt-5.4-codex",
          },
        },
      },
    }),
    JSON.stringify({
      timestamp: "2026-03-08T02:00:00Z",
      type: "event_msg",
      payload: {
        type: "token_count",
        info: {
          last_token_usage: {
            input_tokens: 15,
            cached_input_tokens: 3,
            output_tokens: 5,
          },
        },
      },
    }),
  ];
  fs.writeFileSync(logPath, `${lines.join("\n")}\n`, "utf8");
  return logPath;
}

function runCli(command, extraEnv = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "llm-usage-node-cli-"));
  const homeDir = path.join(root, "home");
  const dataDir = path.join(root, "data");
  const envFile = path.join(root, "config", ".env");
  fs.mkdirSync(homeDir, { recursive: true });
  fs.mkdirSync(dataDir, { recursive: true });
  fs.mkdirSync(path.dirname(envFile), { recursive: true });
  const logPath = writeCodexLog(root);

  return spawnSync(nodeBin, [cliPath, command, "--ui", "none", "--lookback-days", "30"], {
    cwd: path.resolve(testDir, ".."),
    encoding: "utf8",
    env: {
      ...process.env,
      HOME: homeDir,
      PATH: nodeBinDir,
      LLM_USAGE_DATA_DIR: dataDir,
      LLM_USAGE_ENV_FILE: envFile,
      ORG_USERNAME: "san.zhang",
      HASH_SALT: "test-salt",
      CODEX_LOG_PATHS: logPath,
      ...extraEnv,
    },
  });
}

test("doctor succeeds without python3 when using only local collectors", () => {
  const result = runCli("doctor");
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /LLM Usage Node doctor/u);
  assert.match(result.stdout, /codex/u);
});

test("collect succeeds without python3 when using only local collectors", () => {
  const result = runCli("collect");
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /Usage Report/u);
  assert.match(result.stdout, /codex/u);
  assert.match(result.stdout, /usage_report\.csv/u);
});
