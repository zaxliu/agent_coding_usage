import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { printTerminalReport, writeCsvReport } from "../src/runtime/reporting.js";

function row(overrides = {}) {
  return {
    date_local: "2026-03-29",
    user_hash: "user-hash",
    source_host_hash: "",
    tool: "codex",
    model: "gpt-5",
    input_tokens_sum: 0,
    cache_tokens_sum: 0,
    output_tokens_sum: 0,
    row_key: "row-key",
    updated_at: "2026-03-29T00:00:00+08:00",
    ...overrides,
  };
}

function captureLogs(fn) {
  const lines = [];
  const originalLog = console.log;
  console.log = (...args) => {
    lines.push(args.join(" "));
  };
  try {
    fn();
  } finally {
    console.log = originalLog;
  }
  return lines;
}

test("printTerminalReport groups rows by day tool model like the Python CLI", () => {
  const lines = captureLogs(() =>
    printTerminalReport([
      row({
        source_host_hash: "source-a",
        input_tokens_sum: 10,
        cache_tokens_sum: 6,
        output_tokens_sum: 8,
      }),
      row({
        source_host_hash: "source-b",
        input_tokens_sum: 5,
        cache_tokens_sum: 3,
        output_tokens_sum: 6,
      }),
    ]),
  );

  assert.match(lines[0], /日期/u);
  assert.match(lines[0], /工具/u);
  assert.match(lines[0], /模型/u);
  assert.match(lines[0], /输入/u);
  assert.match(lines[0], /缓存/u);
  assert.match(lines[0], /输出/u);
  assert.doesNotMatch(lines[0], /来源/u);
  assert.equal(lines.length, 3);
  assert.match(lines[2], /2026-03-29/u);
  assert.match(lines[2], /codex/u);
  assert.match(lines[2], /gpt-5/u);
  assert.match(lines[2], /15/u);
  assert.match(lines[2], /9/u);
  assert.match(lines[2], /14/u);
  assert.doesNotMatch(lines[2], /source-a/u);
  assert.doesNotMatch(lines[2], /source-b/u);
});

test("writeCsvReport keeps original rows", () => {
  const outputDir = fs.mkdtempSync(path.join(os.tmpdir(), "llm-usage-node-reporting-"));
  const filePath = writeCsvReport(
    [
      row({ source_host_hash: "source-a", input_tokens_sum: 10, row_key: "row-a" }),
      row({ source_host_hash: "source-b", input_tokens_sum: 5, row_key: "row-b" }),
    ],
    outputDir,
  );

  const text = fs.readFileSync(filePath, "utf8");
  assert.match(text, /source-a/u);
  assert.match(text, /source-b/u);
  assert.match(text, /row-a/u);
  assert.match(text, /row-b/u);
});
