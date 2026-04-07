import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import { aggregateEvents } from "../src/core/aggregation.js";
import { buildRowKey, hashSourceHost, hashUser } from "../src/core/identity.js";
import { toFeishuFields } from "../src/core/privacy.js";

const repoRoot = process.cwd();

function loadJson(relativePath) {
  return JSON.parse(fs.readFileSync(path.join(repoRoot, relativePath), "utf8"));
}

test("hash vectors match shared spec", () => {
  const vectors = loadJson("spec/parity-vectors/hash_vectors.json");

  for (const item of vectors.userHashes) {
    assert.equal(hashUser(item.username, item.salt), item.expected);
  }

  for (const item of vectors.sourceHostHashes) {
    assert.equal(hashSourceHost(item.username, item.sourceLabel, item.salt), item.expected);
  }

  for (const item of vectors.rowKeys) {
    assert.equal(
      buildRowKey({
        userHash: item.userHash,
        sourceHostHash: item.sourceHostHash,
        dateLocal: item.dateLocal,
        tool: item.tool,
        model: item.model,
        sessionFingerprint: item.sessionFingerprint,
      }),
      item.expected,
    );
  }
});

test("aggregation vectors match shared spec", () => {
  const vectors = loadJson("spec/parity-vectors/aggregation_vectors.json");

  const rows = aggregateEvents(vectors.events, {
    userHash: vectors.userHash,
    timeZone: vectors.timeZone,
    now: new Date(vectors.now),
  });

  assert.deepEqual(rows, vectors.expectedRows);
});

test("feishu fields stay on the public whitelist", () => {
  const vectors = loadJson("spec/parity-vectors/aggregation_vectors.json");
  const rows = aggregateEvents(vectors.events, {
    userHash: vectors.userHash,
    timeZone: vectors.timeZone,
    now: new Date(vectors.now),
  });

  assert.deepEqual(toFeishuFields(rows[0]), {
    date_local: "2026-03-08",
    user_hash: vectors.userHash,
    source_host_hash: "source-a",
    tool: "codex",
    model: "gpt-5.3-codex",
    input_tokens_sum: 15,
    cache_tokens_sum: 3,
    output_tokens_sum: 5,
    row_key: rows[0].row_key,
    updated_at: "2026-03-08T02:00:00+00:00",
  });
});
