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

test("printTerminalReport(rows, { hostLabels }) renders a Host column", () => {
  const lines = captureLogs(() =>
    printTerminalReport([row({ source_host_hash: "any-hash" })], {
      hostLabels: { "any-hash": "local" },
    }),
  );

  assert.match(lines[0], /\bHost\b/u);
});

test("printTerminalReport keeps different source_host_hash values as separate terminal rows", () => {
  const lines = captureLogs(() =>
    printTerminalReport(
      [
        row({
          source_host_hash: "local-hash",
          input_tokens_sum: 10,
          cache_tokens_sum: 2,
          output_tokens_sum: 3,
        }),
        row({
          source_host_hash: "remote-hash",
          input_tokens_sum: 5,
          cache_tokens_sum: 7,
          output_tokens_sum: 11,
        }),
      ],
      {
        hostLabels: { "local-hash": "local", "remote-hash": "alice@host-a" },
      },
    ),
  );
  const localRowColumns = lines[2]?.split(" | ");
  const remoteRowColumns = lines[3]?.split(" | ");

  assert.equal(lines.length, 4);
  assert.equal(localRowColumns[1]?.trim(), "local");
  assert.equal(localRowColumns[4]?.trim(), "10");
  assert.equal(remoteRowColumns[1]?.trim(), "alice@host-a");
  assert.equal(remoteRowColumns[4]?.trim(), "5");
});

test("printTerminalReport renders provided host labels local and alice@host-a", () => {
  const lines = captureLogs(() =>
    printTerminalReport(
      [
        row({ source_host_hash: "local-hash" }),
        row({ source_host_hash: "remote-hash" }),
      ],
      {
        hostLabels: { "local-hash": "local", "remote-hash": "alice@host-a" },
      },
    ),
  );

  assert.match(lines[2], /\blocal\b/u);
  assert.match(lines[3], /alice@host-a/u);
});

test("printTerminalReport falls back to the first 8 characters of source_host_hash for unknown hosts", () => {
  const lines = captureLogs(() =>
    printTerminalReport([row({ source_host_hash: "abcdef1234567890" })], {
      hostLabels: {},
    }),
  );

  const cells = lines[2].split(" | ");
  assert.equal(cells[1].trim(), "abcdef12");
  assert.doesNotMatch(lines[2], /abcdef1234567890/u);
});

test("printTerminalReport renders local in the Host column when source_host_hash is empty", () => {
  const lines = captureLogs(() =>
    printTerminalReport([row()], {
      hostLabels: {},
    }),
  );
  const cells = lines[2].split(" | ");

  assert.equal(cells[1].trim(), "local");
});

test("printTerminalReport does not truncate long model names", () => {
  const longModel = "gpt-5.4-codex-with-a-very-long-suffix-for-dynamic-width";
  const lines = captureLogs(() =>
    printTerminalReport([row({ model: longModel })], {
      hostLabels: {},
    }),
  );
  const dataRow = lines[2];
  assert.ok(dataRow.includes(longModel));
});

test("printTerminalReport uses seven columns and dynamic widths for header, divider, and rows", () => {
  const lines = captureLogs(() =>
    printTerminalReport(
      [
        row({ source_host_hash: "short-hash", tool: "codex", model: "gpt-5" }),
        row({ source_host_hash: "long-hash", tool: "codex", model: "gpt-5" }),
      ],
      {
        hostLabels: {
          "short-hash": "local",
          "long-hash": "very-long-host-label@example.internal",
        },
      },
    ),
  );

  const header = lines[0];
  const divider = lines[1];
  const firstRow = lines[2];
  const secondRow = lines[3];
  const headerColumns = header.split(" | ");
  const dividerColumns = divider.split("-+-");
  const firstRowColumns = firstRow.split(" | ");
  const secondRowColumns = secondRow.split(" | ");

  assert.equal(headerColumns.length, 7);
  assert.equal(dividerColumns.length, 7);
  assert.equal(firstRowColumns.length, 7);
  assert.equal(secondRowColumns.length, 7);
  assert.equal(headerColumns[1].trim(), "Host");
  assert.equal(dividerColumns[1].length, "very-long-host-label@example.internal".length);
  assert.equal(firstRowColumns[1].trim(), "very-long-host-label@example.internal");
  assert.equal(secondRowColumns[1].trim(), "local");
  // Sorted by (date_local, source_host_hash, tool, model); "long-hash" < "short-hash" lexicographically.
  assert.match(firstRow, /very-long-host-label@example\.internal/u);
  assert.match(secondRow, /\blocal\b/u);
});

test("printTerminalReport merges duplicate rows that share date, host, tool, and model", () => {
  const lines = captureLogs(() =>
    printTerminalReport(
      [
        row({
          source_host_hash: "remote-hash",
          input_tokens_sum: 10,
          cache_tokens_sum: 2,
          output_tokens_sum: 3,
        }),
        row({
          source_host_hash: "remote-hash",
          input_tokens_sum: 5,
          cache_tokens_sum: 7,
          output_tokens_sum: 11,
        }),
      ],
      { hostLabels: { "remote-hash": "alice@host-a" } },
    ),
  );
  const cells = lines[2].split(" | ");

  assert.equal(lines.length, 3);
  assert.equal(cells[1].trim(), "alice@host-a");
  assert.equal(cells[4].trim(), "15");
  assert.equal(cells[5].trim(), "9");
  assert.equal(cells[6].trim(), "14");
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
