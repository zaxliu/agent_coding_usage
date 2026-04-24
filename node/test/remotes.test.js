import test from "node:test";
import assert from "node:assert/strict";

import {
  buildEnvWithTemporaryRemotes,
  buildTemporaryRemote,
  defaultSourceLabel,
  normalizeAlias,
  parseRemoteConfigsFromEnv,
  uniqueAlias,
} from "../src/runtime/remotes.js";

test("parseRemoteConfigsFromEnv reads configured remotes", () => {
  const configs = parseRemoteConfigsFromEnv({
    REMOTE_HOSTS: "server_a,server-b",
    REMOTE_SERVER_A_SSH_HOST: "host-a",
    REMOTE_SERVER_A_SSH_USER: "alice",
    REMOTE_SERVER_A_LABEL: "prod-a",
    REMOTE_SERVER_A_COPILOT_CLI_LOG_PATHS: "/tmp/copilot-cli.jsonl",
    REMOTE_SERVER_A_CLINE_VSCODE_SESSION_PATHS: "/tmp/cline-history.json",
    REMOTE_SERVER_B_SSH_HOST: "host-b",
    REMOTE_SERVER_B_SSH_USER: "bob",
  });
  assert.equal(configs[0].alias, "SERVER_A");
  assert.equal(configs[0].source_label, "prod-a");
  assert.deepEqual(configs[0].copilot_cli_log_paths, ["/tmp/copilot-cli.jsonl"]);
  assert.deepEqual(configs[0].cline_vscode_session_paths, ["/tmp/cline-history.json"]);
  assert.equal(configs[1].alias, "SERVER_B");
});

test("remote alias helpers normalize and dedupe", () => {
  assert.equal(normalizeAlias("prod-a"), "PROD_A");
  assert.equal(uniqueAlias("prod-a", ["PROD_A"]), "PROD_A_2");
  assert.equal(defaultSourceLabel("alice", "10.0.0.8"), "alice@10.0.0.8");
});

test("buildEnvWithTemporaryRemotes injects ephemeral remotes", () => {
  const env = { REMOTE_HOSTS: "SERVER_A", REMOTE_SERVER_A_SSH_HOST: "host-a", REMOTE_SERVER_A_SSH_USER: "alice" };
  const temp = buildTemporaryRemote("host-b", "bob", 2200);
  const next = buildEnvWithTemporaryRemotes(env, [temp]);
  assert.match(next.env.REMOTE_HOSTS, /SERVER_A/);
  assert.match(next.env.REMOTE_HOSTS, /BOB_HOST_B/);
  assert.deepEqual(next.aliases, ["BOB_HOST_B"]);
  assert.equal(next.env.REMOTE_BOB_HOST_B_SSH_HOST, "host-b");
  assert.equal(next.env.REMOTE_BOB_HOST_B_SSH_PORT, "2200");
});
