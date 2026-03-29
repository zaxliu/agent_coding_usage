import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";

import { FileCollector } from "../src/collectors/file-collector.js";
import { OpenCodeCollector } from "../src/collectors/opencode.js";
import { readEventsFromText } from "../src/collectors/parsing.js";

test("readEventsFromText parses codex token_count jsonl with turn_context model hint", () => {
  const text = [
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
  ].join("\n");

  const [events, warning] = readEventsFromText(
    text,
    "codex",
    "/tmp/session.jsonl",
    new Date("2026-03-08T02:00:00Z"),
    ".jsonl",
    "/tmp/9fce8d38-4be2-4bb4-8f43-c6d0b4058c4a.jsonl",
  );

  assert.equal(warning, null);
  assert.equal(events.length, 1);
  assert.equal(events[0].model, "gpt-5.4-codex");
  assert.equal(events[0].inputTokens, 12);
  assert.equal(events[0].cacheTokens, 3);
  assert.equal(events[0].outputTokens, 5);
  assert.match(events[0].sessionFingerprint, /^codex:/u);
});

test("readEventsFromText parses copilot vscode session files", () => {
  const text = JSON.stringify({
    sessionId: "session-a",
    requests: [
      {
        requestId: "req-1",
        timestamp: "2026-03-08T03:00:00Z",
        modelId: "copilot/gpt-4.1",
        result: {
          usage: {
            promptTokens: 20,
            completionTokens: 8,
            cacheReadTokens: 4,
          },
        },
      },
    ],
  });

  const [events, warning] = readEventsFromText(
    text,
    "copilot_vscode",
    "/tmp/vscode.json",
    new Date("2026-03-08T03:00:00Z"),
    ".json",
    "/tmp/vscode.json",
  );

  assert.equal(warning, null);
  assert.equal(events.length, 1);
  assert.equal(events[0].model, "gpt-4.1");
  assert.equal(events[0].cacheTokens, 4);
  assert.equal(events[0].sessionFingerprint, "copilot_vscode:session-a:req-1");
});

test("FileCollector probes and collects supported files", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "llm-usage-node-collector-"));
  const validPath = path.join(root, "logs", "claude.jsonl");
  fs.mkdirSync(path.dirname(validPath), { recursive: true });
  fs.writeFileSync(
    validPath,
    `${JSON.stringify({
      timestamp: "2026-03-08T03:00:00Z",
      input_tokens: 10,
      output_tokens: 3,
      cache_tokens: 1,
      model: "sonnet",
    })}\n`,
    "utf8",
  );
  fs.mkdirSync(path.join(root, "node_modules"), { recursive: true });
  fs.writeFileSync(path.join(root, "node_modules", "ignored.json"), "{}", "utf8");

  const collector = new FileCollector("claude_code", [path.join(root, "**", "*.json*")], {
    sourceHostHash: "local-hash",
  });

  const probe = collector.probe();
  assert.equal(probe.ok, true);
  assert.match(probe.message, /1 files detected, 1 parsable events/u);

  const result = collector.collect(new Date("2026-03-08T00:00:00Z"), new Date("2026-03-09T00:00:00Z"));
  assert.equal(result.events.length, 1);
  assert.equal(result.events[0].sourceHostHash, "local-hash");
  assert.equal(result.warnings.length, 0);
});

test("OpenCodeCollector reads token usage from sqlite", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "llm-usage-opencode-"));
  const dbPath = path.join(root, "opencode.db");
  const db = new DatabaseSync(dbPath);
  db.exec(`
    CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT);
    CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT);
    CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, data TEXT, time_created INTEGER);
  `);
  db.prepare("INSERT INTO session (id, directory) VALUES (?, ?)").run("s1", "/workspace/demo");
  db.prepare("INSERT INTO message (id, session_id) VALUES (?, ?)").run("m1", "s1");
  db.prepare("INSERT INTO part (id, message_id, data, time_created) VALUES (?, ?, ?, ?)")
    .run(
      "p1",
      "m1",
      JSON.stringify({
        type: "step-finish",
        model: "gpt-5.4-mini",
        tokens: {
          input: 21,
          output: 9,
          cache: { read: 4, write: 2 },
        },
      }),
      Date.parse("2026-03-08T05:00:00Z"),
    );
  db.close();

  const collector = new OpenCodeCollector({ dbPath, sourceHostHash: "local-hash" });
  const probe = collector.probe();
  assert.equal(probe.ok, true);
  assert.match(probe.message, /1 token records/u);

  const result = collector.collect(new Date("2026-03-08T00:00:00Z"), new Date("2026-03-09T00:00:00Z"));
  assert.equal(result.events.length, 1);
  assert.equal(result.events[0].tool, "opencode");
  assert.equal(result.events[0].model, "gpt-5.4-mini");
  assert.equal(result.events[0].inputTokens, 21);
  assert.equal(result.events[0].cacheTokens, 6);
  assert.equal(result.events[0].outputTokens, 9);
  assert.equal(result.events[0].sourceHostHash, "local-hash");
});
