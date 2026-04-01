import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { hashSourceHost, hashUser } from "../src/core/identity.js";

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

function runCli(args, extraEnv = {}, spawnOptions = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "llm-usage-node-cli-"));
  const homeDir = path.join(root, "home");
  const dataDir = path.join(root, "data");
  const envFile = path.join(root, "config", ".env");
  fs.mkdirSync(homeDir, { recursive: true });
  fs.mkdirSync(dataDir, { recursive: true });
  fs.mkdirSync(path.dirname(envFile), { recursive: true });
  const logPath = writeCodexLog(root);

  const argv = Array.isArray(args) ? args : [args];

  return {
    root,
    dataDir,
    envFile,
    result: spawnSync(nodeBin, [cliPath, ...argv], {
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
      ...spawnOptions,
    }),
  };
}

test("doctor succeeds when using only local collectors", () => {
  const { result } = runCli(["doctor", "--ui", "none", "--lookback-days", "30"]);
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /LLM Usage Node doctor/u);
  assert.match(result.stdout, /codex/u);
});

test("collect succeeds when using only local collectors", () => {
  const { result } = runCli(["collect", "--ui", "none", "--lookback-days", "30"]);
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /日期/u);
  assert.match(result.stdout, /codex/u);
  assert.match(result.stdout, /usage_report\.csv/u);
});

test("top-level help shows parity commands", () => {
  const { result } = runCli(["--help"]);
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /init/u);
  assert.match(result.stdout, /whoami/u);
  assert.match(result.stdout, /import-config/u);
  assert.match(result.stdout, /export-bundle/u);
});

test("init creates runtime env and reports directory", () => {
  const { dataDir, envFile, result } = runCli(["init"]);
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.equal(fs.existsSync(envFile), true);
  assert.equal(fs.existsSync(path.join(dataDir, "reports")), true);
});

test("whoami prints user and per-host hashes", () => {
  const { result } = runCli(["whoami"], {
    REMOTE_HOSTS: "server_a",
    REMOTE_SERVER_A_SSH_HOST: "host-a",
    REMOTE_SERVER_A_SSH_USER: "alice",
    REMOTE_SERVER_A_LABEL: "prod-a",
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /ORG_USERNAME: san\.zhang/u);
  assert.match(result.stdout, new RegExp(hashUser("san.zhang", "test-salt"), "u"));
  assert.match(result.stdout, new RegExp(hashSourceHost("san.zhang", "local", "test-salt"), "u"));
  assert.match(result.stdout, new RegExp(hashSourceHost("san.zhang", "prod-a", "test-salt"), "u"));
});

test("import-config dry-run shows copy plan for legacy files", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "llm-usage-node-import-"));
  const dataDir = path.join(root, "data");
  const envFile = path.join(root, "config", ".env");
  fs.mkdirSync(dataDir, { recursive: true });
  fs.mkdirSync(path.dirname(envFile), { recursive: true });
  const legacyRoot = path.join(root, "legacy");
  fs.mkdirSync(path.join(legacyRoot, "reports"), { recursive: true });
  fs.writeFileSync(path.join(legacyRoot, ".env"), "ORG_USERNAME=legacy\nHASH_SALT=legacy-salt\n", "utf8");
  fs.writeFileSync(
    path.join(legacyRoot, "reports", "runtime_state.json"),
    '{"selected_remote_aliases":["SERVER_A"]}\n',
    "utf8",
  );

  const rerun = runCli(["import-config", "--from", legacyRoot, "--dry-run"], {
    LLM_USAGE_DATA_DIR: dataDir,
    LLM_USAGE_ENV_FILE: envFile,
  });
  assert.equal(rerun.result.status, 0, rerun.result.stderr || rerun.result.stdout);
  assert.match(rerun.result.stdout, /plan: copy .*\.env/u);
  assert.match(rerun.result.stdout, /plan: copy .*runtime_state\.json/u);
  assert.match(rerun.result.stdout, /dry-run: no files were written/u);
});

test("import-config defaults to current working directory when --from is omitted", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "llm-usage-node-import-cwd-"));
  const dataDir = path.join(root, "data");
  const envFile = path.join(root, "config", ".env");
  const legacyRoot = path.join(root, "legacy");
  fs.mkdirSync(dataDir, { recursive: true });
  fs.mkdirSync(path.dirname(envFile), { recursive: true });
  fs.mkdirSync(path.join(legacyRoot, "reports"), { recursive: true });
  fs.writeFileSync(path.join(legacyRoot, ".env"), "ORG_USERNAME=legacy\nHASH_SALT=legacy-salt\n", "utf8");
  fs.writeFileSync(path.join(legacyRoot, "reports", "runtime_state.json"), '{"selected_remote_aliases":[]}\n', "utf8");

  const { result } = runCli(["import-config", "--dry-run"], {
    LLM_USAGE_DATA_DIR: dataDir,
    LLM_USAGE_ENV_FILE: envFile,
  }, { cwd: legacyRoot });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, new RegExp(`plan: copy .*${legacyRoot.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&")}`, "u"));
});

test("export-bundle writes bundle that sync --from-bundle can dry-run", () => {
  const exported = runCli(["export-bundle", "--ui", "none", "--lookback-days", "30"]);
  assert.equal(exported.result.status, 0, exported.result.stderr || exported.result.stdout);
  assert.match(exported.result.stdout, /bundle: .*\.zip/u);
  const bundlePath = exported.result.stdout.match(/bundle: (.*\.zip)/u)?.[1]?.trim();
  assert.equal(typeof bundlePath, "string");
  assert.equal(fs.existsSync(bundlePath), true);

  const replay = runCli(["sync", "--from-bundle", bundlePath, "--dry-run"]);
  assert.equal(replay.result.status, 0, replay.result.stderr || replay.result.stdout);
  assert.match(replay.result.stdout, /codex/u);
  assert.doesNotMatch(replay.result.stdout, /Feishu Upsert/u);
});

test("sync --from-bundle --dry-run does not require identity env vars for host-label fallback", () => {
  const exported = runCli(["export-bundle", "--ui", "none", "--lookback-days", "30"]);
  assert.equal(exported.result.status, 0, exported.result.stderr || exported.result.stdout);
  const bundlePath = exported.result.stdout.match(/bundle: (.*\.zip)/u)?.[1]?.trim();
  assert.equal(typeof bundlePath, "string");
  assert.equal(fs.existsSync(bundlePath), true);

  const replay = runCli(["sync", "--from-bundle", bundlePath, "--dry-run"], {
    ORG_USERNAME: "",
    HASH_SALT: "",
  });
  assert.equal(replay.result.status, 0, replay.result.stderr || replay.result.stdout);
  assert.match(replay.result.stdout, /\bHost\b/u);
  assert.doesNotMatch(replay.result.stderr, /missing env var/u);
});

test("collect warns that configured remotes are ignored in local-first Node mode", () => {
  const { result } = runCli(
    ["collect", "--ui", "none", "--lookback-days", "30"],
    {
      REMOTE_HOSTS: "server_a",
      REMOTE_SERVER_A_SSH_HOST: "host-a",
      REMOTE_SERVER_A_SSH_USER: "alice",
    },
  );
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /remote collection is not supported in llm-usage-node yet/u);
  assert.match(result.stdout, /usage_report\.csv/u);
});

test("sync help shows from-bundle support", () => {
  const result = spawnSync(nodeBin, [cliPath, "sync", "--help"], {
    cwd: path.resolve(testDir, ".."),
    encoding: "utf8",
    env: {
      ...process.env,
      PATH: nodeBinDir,
    },
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /--from/u);
  assert.match(result.stdout, /--dry-run/u);
  assert.match(result.stdout, /--feishu-target/u);
  assert.match(result.stdout, /--all-feishu-targets/u);
});

test("collect help shows Cursor login options", () => {
  const result = spawnSync(nodeBin, [cliPath, "collect", "--help"], {
    cwd: path.resolve(testDir, ".."),
    encoding: "utf8",
    env: {
      ...process.env,
      PATH: nodeBinDir,
    },
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /--cursor-login-mode/u);
  assert.match(result.stdout, /--cursor-login-browser/u);
  assert.match(result.stdout, /--cursor-login-user-data-dir/u);
  assert.match(result.stdout, /--cursor-login-timeout-sec/u);
});

test("doctor help shows Feishu target options", () => {
  const result = spawnSync(nodeBin, [cliPath, "doctor", "--help"], {
    cwd: path.resolve(testDir, ".."),
    encoding: "utf8",
    env: {
      ...process.env,
      PATH: nodeBinDir,
    },
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /--feishu/u);
  assert.match(result.stdout, /--feishu-target/u);
  assert.match(result.stdout, /--all-feishu-targets/u);
});

test("init help shows Feishu schema options", () => {
  const result = spawnSync(nodeBin, [cliPath, "init", "--help"], {
    cwd: path.resolve(testDir, ".."),
    encoding: "utf8",
    env: {
      ...process.env,
      PATH: nodeBinDir,
    },
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /--feishu-bitable-schema/u);
  assert.match(result.stdout, /--feishu-target/u);
  assert.match(result.stdout, /--all-feishu-targets/u);
});

test("doctor rejects Feishu target flags without --feishu", () => {
  const { result } = runCli(["doctor", "--feishu-target", "team_b"]);
  assert.equal(result.status, 2, result.stderr || result.stdout);
  assert.match(result.stdout, /require --feishu/u);
});

test("init rejects Feishu target flags without --feishu-bitable-schema", () => {
  const { result } = runCli(["init", "--feishu-target", "team_b"]);
  assert.equal(result.status, 2, result.stderr || result.stdout);
  assert.match(result.stdout, /require --feishu-bitable-schema/u);
});
