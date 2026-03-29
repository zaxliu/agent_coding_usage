import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { aggregateEvents } from "../core/aggregation.js";
import { buildRowKey, hashSourceHost, hashUser } from "../core/identity.js";
import { toFeishuFields } from "../core/privacy.js";

function printHelp() {
  console.log(`Usage: llm-usage-node <command>

Commands:
  doctor        Print Node conservative implementation status
  parity-demo   Run a small deterministic parity demo
`);
}

const thisDir = path.dirname(fileURLToPath(import.meta.url));
const repoRootDir = path.resolve(thisDir, "../../..");

function readParityVectors() {
  const filePath = path.join(repoRootDir, "spec", "parity-vectors", "hash_vectors.json");
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function runDoctor() {
  console.log("llm-usage-node conservative core is available");
  console.log("modules: identity, aggregation, privacy");
  console.log(`spec: ${path.join(repoRootDir, "spec", "parity-vectors")}`);
  return 0;
}

function runParityDemo() {
  const vectors = readParityVectors();
  const first = vectors.userHashes[0];
  const userHash = hashUser(first.username, first.salt);
  const sourceHostHash = hashSourceHost(first.username, "local", first.salt);
  const rows = aggregateEvents(
    [
      {
        tool: "codex",
        model: "gpt-5",
        eventTime: "2026-03-08T00:10:00Z",
        inputTokens: 10,
        cacheTokens: 1,
        outputTokens: 2,
        sourceHostHash,
      },
      {
        tool: "codex",
        model: "gpt-5",
        eventTime: "2026-03-08T01:10:00Z",
        inputTokens: 5,
        cacheTokens: 2,
        outputTokens: 3,
        sourceHostHash,
      },
    ],
    { userHash, timeZone: "UTC", now: new Date("2026-03-08T02:00:00Z") },
  );
  const output = {
    userHash,
    sourceHostHash,
    rowKey: buildRowKey({
      userHash,
      sourceHostHash,
      dateLocal: rows[0].date_local,
      tool: rows[0].tool,
      model: rows[0].model,
    }),
    feishuFields: toFeishuFields(rows[0]),
  };
  console.log(JSON.stringify(output, null, 2));
  return 0;
}

export async function main(argv) {
  const [command] = argv;
  switch (command) {
    case "doctor":
      process.exitCode = runDoctor();
      return;
    case "parity-demo":
      process.exitCode = runParityDemo();
      return;
    case "-h":
    case "--help":
    case undefined:
      printHelp();
      process.exitCode = 0;
      return;
    default:
      throw new Error(`unknown command: ${command}`);
  }
}
