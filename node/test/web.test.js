import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  createWebRequestHandler,
  JobManager,
  loadConfigPayload,
  loadLatestResults,
  validateConfigPayload,
  writeConfigPayload,
} from "../src/runtime/web.js";

function makeRequest(method, url, body = undefined) {
  return {
    method,
    url,
    async *[Symbol.asyncIterator]() {
      if (body === undefined) {
        return;
      }
      yield Buffer.from(JSON.stringify(body), "utf8");
    },
  };
}

function makeResponse() {
  const chunks = [];
  return {
    statusCode: 0,
    headers: {},
    bodyText: "",
    writeHead(status, headers) {
      this.statusCode = status;
      this.headers = headers;
    },
    end(chunk) {
      if (chunk) {
        chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk), "utf8"));
      }
      this.bodyText = Buffer.concat(chunks).toString("utf8");
    },
  };
}

async function invokeRoute(handler, method, url, body) {
  const request = makeRequest(method, url, body);
  const response = makeResponse();
  await handler(request, response);
  return {
    status: response.statusCode,
    headers: response.headers,
    body: response.bodyText ? JSON.parse(response.bodyText) : null,
  };
}

async function waitForJobStatus(jobs, jobId, statuses, timeoutMs = 1500) {
  const expected = new Set(Array.isArray(statuses) ? statuses : [statuses]);
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const job = jobs.get(jobId);
    if (job && expected.has(job.status)) {
      return job;
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  return jobs.get(jobId);
}

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
      "REMOTE_SERVER_A_CLINE_VSCODE_SESSION_PATHS=/remote/cline/api_conversation_history.json",
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
    CURSOR_WEB_SESSION_TOKEN: "test-session-token",
    FEISHU_APP_TOKEN: "",
    FEISHU_TABLE_ID: "",
    FEISHU_APP_ID: "",
    FEISHU_APP_SECRET: "",
    FEISHU_BOT_TOKEN: "",
    FEISHU_TARGETS: "",
  });

  const configPayload = loadConfigPayload();
  assert.equal(configPayload.basic.ORG_USERNAME, "san.zhang");
  assert.equal(configPayload.feishu_targets[0].name, "team_b");
  assert.equal(configPayload.remotes[0].alias, "SERVER_A");
  assert.deepEqual(configPayload.remotes[0].cline_vscode_session_paths, ["/remote/cline/api_conversation_history.json"]);

  const resultsPayload = loadLatestResults();
  assert.deepEqual(resultsPayload.summary, {
    totals: {
      rows: 1,
      input_tokens_sum: 10,
      cache_tokens_sum: 2,
      output_tokens_sum: 3,
      total_tokens: 15,
    },
    active_days: 1,
    top_tool: { name: "codex", total_tokens: 15 },
    top_model: { name: "gpt-5", total_tokens: 15 },
    generated_at: resultsPayload.generated_at,
  });
  assert.equal(resultsPayload.timeseries[0].date_local, "2026-04-06");
  assert.equal(resultsPayload.timeseries[0].total_tokens, 15);
  assert.equal(resultsPayload.breakdowns.tools[0].name, "codex");
  assert.equal(resultsPayload.breakdowns.models[0].name, "gpt-5");
  assert.equal(resultsPayload.table_rows[0].source_host_hash, "host-a");
  assert.equal(resultsPayload.table_rows[0].tool, "codex");
  assert.equal(resultsPayload.table_rows[0].total_tokens, 15);
  assert.equal(resultsPayload.rows[0].row_key, "row-1");
  assert.ok(Array.isArray(resultsPayload.warnings));

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
    remotes: [
      {
        alias: "SERVER_A",
        ssh_host: "host-a",
        ssh_user: "alice",
        ssh_port: 22,
        source_label: "alice@host-a",
        cline_vscode_session_paths: ["/remote/cline/api_conversation_history.json"],
      },
    ],
    raw_env: [],
  });
  assert.equal(savePayload.ok, true);
  assert.match(fs.readFileSync(envFile, "utf8"), /LOOKBACK_DAYS=14/u);
  assert.match(fs.readFileSync(envFile, "utf8"), /REMOTE_SERVER_A_CLINE_VSCODE_SESSION_PATHS=\/remote\/cline\/api_conversation_history\.json/u);
});

test("node web routes do not request ssh passwords for ignored remotes", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "llm-usage-node-route-jobs-"));
  const envFile = path.join(root, "config", ".env");
  const dataDir = path.join(root, "data");
  fs.mkdirSync(path.dirname(envFile), { recursive: true });
  fs.mkdirSync(path.join(dataDir, "reports"), { recursive: true });
  fs.writeFileSync(
    envFile,
    [
      "ORG_USERNAME=san.zhang",
      "HASH_SALT=test-salt",
      "CURSOR_WEB_SESSION_TOKEN=test-session-token",
      "REMOTE_HOSTS=server_a",
      "REMOTE_SERVER_A_SSH_HOST=host-a",
      "REMOTE_SERVER_A_SSH_USER=alice",
      "REMOTE_SERVER_A_USE_SSHPASS=1",
      "",
    ].join("\n"),
    "utf8",
  );

  Object.assign(process.env, {
    ...process.env,
    LLM_USAGE_ENV_FILE: envFile,
    LLM_USAGE_DATA_DIR: dataDir,
  });

  const jobs = new JobManager();
  const handler = createWebRequestHandler(jobs, { maybeCaptureCursorTokenFn: async () => null });

  const collectResponse = await invokeRoute(handler, "POST", "/api/collect", {});
  assert.equal(collectResponse.status, 202);
  assert.ok(["queued", "running"].includes(collectResponse.body.status));

  const collectDone = await waitForJobStatus(jobs, jobs.list()[0].id, ["needs_input", "succeeded", "failed"], 3000);
  assert.notEqual(collectDone.status, "needs_input");
  assert.equal(collectDone.input_request, null);
  if (collectDone.status === "succeeded") {
    assert.match(String(collectDone.result.csv_path || ""), /usage_report\.csv/u);
  } else {
    assert.match(collectDone.error || "", /./u);
  }
  const envAfterCollect = fs.readFileSync(envFile, "utf8");
  assert.match(envAfterCollect, /ORG_USERNAME=san\.zhang/u);
  assert.match(envAfterCollect, /HASH_SALT=test-salt/u);
  assert.match(envAfterCollect, /REMOTE_HOSTS=server_a/u);
  assert.match(envAfterCollect, /REMOTE_SERVER_A_SSH_HOST=host-a/u);
  assert.match(envAfterCollect, /REMOTE_SERVER_A_SSH_USER=alice/u);
  assert.match(envAfterCollect, /REMOTE_SERVER_A_USE_SSHPASS=1/u);

  const syncJobs = new JobManager();
  const syncHandler = createWebRequestHandler(syncJobs, { maybeCaptureCursorTokenFn: async () => null });

  const previewResponse = await invokeRoute(syncHandler, "POST", "/api/sync/preview", {});
  assert.equal(previewResponse.status, 202);
  assert.ok(["queued", "running"].includes(previewResponse.body.status));

  const previewDone = await waitForJobStatus(syncJobs, syncJobs.list().find((item) => item.type === "sync_preview").id, ["needs_input", "failed", "succeeded"], 3000);
  assert.notEqual(previewDone.status, "needs_input");
  assert.equal(previewDone.input_request, null);

  const syncResponse = await invokeRoute(syncHandler, "POST", "/api/sync", { confirm_sync: true });
  assert.equal(syncResponse.status, 202);
  assert.ok(["queued", "running"].includes(syncResponse.body.status));

  const syncDone = await waitForJobStatus(syncJobs, syncJobs.list().find((item) => item.type === "sync").id, ["needs_input", "failed", "succeeded"], 3000);
  assert.notEqual(syncDone.status, "needs_input");
  assert.equal(syncDone.input_request, null);
  if (syncDone.status === "failed") {
    assert.match(syncDone.error || "", /./u);
  } else {
    assert.ok(syncDone.result);
  }
});
