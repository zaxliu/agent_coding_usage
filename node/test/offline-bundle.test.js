import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

function sampleRow(overrides = {}) {
  return {
    date_local: "2026-03-31",
    user_hash: "user-hash",
    source_host_hash: "source-a",
    tool: "codex",
    model: "gpt-5.4",
    input_tokens_sum: 10,
    cache_tokens_sum: 2,
    output_tokens_sum: 3,
    row_key: "row-a",
    updated_at: "2026-03-31T12:00:00+08:00",
    ...overrides,
  };
}

test("writeOfflineBundle and readOfflineBundle round-trip rows and manifest", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "llm-usage-node-bundle-"));
  const bundlePath = path.join(root, "offline.zip");
  const bundle = await import("../src/runtime/offline-bundle.js");

  await bundle.writeOfflineBundle([sampleRow()], bundlePath, {
    warnings: ["warn-a"],
    timezoneName: "Asia/Shanghai",
    lookbackDays: 30,
    toolVersion: "0.1.3",
    includeCsv: true,
  });

  const { rows, warnings, manifest } = await bundle.readOfflineBundle(bundlePath);
  assert.deepEqual(rows, [sampleRow()]);
  assert.deepEqual(warnings, ["warn-a"]);
  assert.equal(manifest.row_count, 1);
  assert.equal(manifest.warning_count, 1);
  assert.equal(manifest.tool_version, "0.1.3");
});

test("readOfflineBundle rejects bundle missing manifest", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "llm-usage-node-bundle-missing-"));
  const bundlePath = path.join(root, "broken.zip");
  const bundle = await import("../src/runtime/offline-bundle.js");

  await bundle.writeZipEntries(bundlePath, {
    "rows.jsonl": `${JSON.stringify(sampleRow())}\n`,
  });

  await assert.rejects(() => bundle.readOfflineBundle(bundlePath), /manifest\.json/u);
});
