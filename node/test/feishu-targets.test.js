import test from "node:test";
import assert from "node:assert/strict";

import { UPLOAD_FIELDS } from "../src/core/privacy.js";
import { REQUIRED_FEISHU_FIELDS, fieldNames } from "../src/runtime/feishu-schema.js";
import {
  normalizeFeishuTargetName,
  resolveFeishuTargetsFromEnv,
  selectFeishuTargets,
} from "../src/runtime/feishu-targets.js";

test("resolveFeishuTargetsFromEnv keeps legacy default target", () => {
  const targets = resolveFeishuTargetsFromEnv({
    FEISHU_APP_TOKEN: "app-default",
    FEISHU_TABLE_ID: "tbl-default",
    FEISHU_APP_ID: "cli-default",
    FEISHU_APP_SECRET: "sec-default",
  });

  assert.deepEqual(targets.map((item) => item.name), ["default"]);
  assert.equal(targets[0].appToken, "app-default");
  assert.equal(targets[0].tableId, "tbl-default");
});

test("resolveFeishuTargetsFromEnv supports named targets with auth inheritance", () => {
  const targets = resolveFeishuTargetsFromEnv({
    FEISHU_APP_TOKEN: "app-default",
    FEISHU_APP_ID: "cli-default",
    FEISHU_APP_SECRET: "sec-default",
    FEISHU_BOT_TOKEN: "bot-default",
    FEISHU_TARGETS: "team_b,finance",
    FEISHU_TEAM_B_APP_TOKEN: "app-team-b",
    FEISHU_TEAM_B_TABLE_ID: "tbl-team-b",
    FEISHU_FINANCE_APP_TOKEN: "app-finance",
  });

  assert.deepEqual(targets.map((item) => item.name), ["default", "team_b", "finance"]);
  assert.equal(targets[1].appId, "cli-default");
  assert.equal(targets[2].appSecret, "sec-default");
  assert.equal(targets[2].botToken, "bot-default");
});

test("selectFeishuTargets defaults to default target only", () => {
  const selected = selectFeishuTargets(
    [
      { name: "default", appToken: "app-default" },
      { name: "team_b", appToken: "app-team-b" },
    ],
    { names: [], all: false, defaultOnly: true },
  );

  assert.deepEqual(selected.map((item) => item.name), ["default"]);
});

test("normalizeFeishuTargetName rejects reserved default", () => {
  assert.throws(() => normalizeFeishuTargetName("default"), /reserved/u);
});

test("fieldNames stay aligned with public upload whitelist", () => {
  assert.deepEqual(fieldNames(REQUIRED_FEISHU_FIELDS), [...UPLOAD_FIELDS]);
});
