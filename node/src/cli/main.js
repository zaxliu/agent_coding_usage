import process from "node:process";

import { aggregateEvents } from "../core/aggregation.js";
import { hashUser } from "../core/identity.js";
import { collectLocalUsage, probeLocalUsage } from "../collectors/local.js";
import { toFeishuFields } from "../core/privacy.js";
import {
  getEnv,
  getEnvPath,
  getReportsDir,
  intEnv,
  loadDotenv,
  prepareRuntimePaths,
  requiredEnv,
  repoRoot,
} from "../runtime/env.js";
import { FeishuBitableClient, fetchFirstTableId, fetchTenantAccessToken } from "../runtime/feishu.js";
import { parseRemoteConfigsFromEnv } from "../runtime/remotes.js";
import { printDoctorReport, printSyncSummary, printTerminalReport, writeCsvReport } from "../runtime/reporting.js";
import { info, warn } from "../runtime/ui.js";

function printHelp() {
  console.log(`Usage: llm-usage-node <command> [options]

Commands:
  doctor        Probe local data sources in Node
  collect       Collect local usage in Node and write usage_report.csv to the user data dir
  sync          Collect local usage in Node and upsert rows to Feishu

Options:
  --lookback-days N
  --ui auto|cli|none
`);
}

function parseArgs(argv) {
  const options = { lookbackDays: undefined, ui: "auto" };
  const positional = [];

  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--lookback-days") {
      options.lookbackDays = Number.parseInt(argv[index + 1] || "", 10);
      index += 1;
      continue;
    }
    if (value === "--ui") {
      options.ui = argv[index + 1] || "auto";
      index += 1;
      continue;
    }
    positional.push(value);
  }
  return { positional, options };
}

function resolveLookbackDays(parsedValue) {
  if (Number.isFinite(parsedValue) && parsedValue > 0) {
    return parsedValue;
  }
  return Math.max(1, intEnv("LOOKBACK_DAYS", 7));
}

function serializeLocalEvents(events) {
  return events.map((event) => ({
    tool: event.tool,
    model: event.model,
    eventTime: event.eventTime,
    inputTokens: event.inputTokens,
    cacheTokens: event.cacheTokens,
    outputTokens: event.outputTokens,
    sessionFingerprint: event.sessionFingerprint,
    sourceRef: event.sourceRef,
    sourceHostHash: event.sourceHostHash || "",
  }));
}

function mergeWarnings(...warningLists) {
  return warningLists.flat().filter(Boolean);
}

function printWarnings(warnings) {
  for (const message of warnings || []) {
    console.log(warn(message));
  }
}

function remoteCollectionWarnings() {
  const configuredRemotes = parseRemoteConfigsFromEnv();
  if (!configuredRemotes.length) {
    return [];
  }
  return [
    `remote collection is not supported in llm-usage-node yet; ignoring ${configuredRemotes.length} configured remote(s)`,
  ];
}

async function buildAggregates(lookbackDays) {
  const localPayload = collectLocalUsage(lookbackDays);
  const username = requiredEnv("ORG_USERNAME");
  const salt = requiredEnv("HASH_SALT");
  const userHash = hashUser(username, salt);
  const timeZone = getEnv("TIMEZONE", "Asia/Shanghai");
  const rows = aggregateEvents(serializeLocalEvents(localPayload.events), {
    userHash,
    timeZone,
  });
  return {
    rows,
    warnings: mergeWarnings(remoteCollectionWarnings(), localPayload.warnings),
  };
}

async function runDoctor(_lookbackDays, _uiMode) {
  printDoctorReport({
    envPath: getEnvPath(),
    probes: probeLocalUsage(),
    warnings: remoteCollectionWarnings(),
  });
  return 0;
}

async function runCollect(lookbackDays, uiMode) {
  const { rows, warnings } = await buildAggregates(lookbackDays, uiMode);
  printWarnings(warnings);
  printTerminalReport(rows);
  const csvPath = writeCsvReport(rows, getReportsDir());
  console.log(info(`csv: ${csvPath}`));
  return 0;
}

async function runSync(lookbackDays, uiMode) {
  const { rows, warnings } = await buildAggregates(lookbackDays, uiMode);
  printWarnings(warnings);
  printTerminalReport(rows);
  const csvPath = writeCsvReport(rows, getReportsDir());
  console.log(info(`csv: ${csvPath}`));

  const appToken = requiredEnv("FEISHU_APP_TOKEN");
  let tableId = getEnv("FEISHU_TABLE_ID");
  let botToken = getEnv("FEISHU_BOT_TOKEN");
  if (!botToken) {
    botToken = await fetchTenantAccessToken({
      appId: requiredEnv("FEISHU_APP_ID"),
      appSecret: requiredEnv("FEISHU_APP_SECRET"),
    });
  }
  if (!tableId) {
    tableId = await fetchFirstTableId({ appToken, botToken });
    console.log(info(`FEISHU_TABLE_ID empty, auto-selected first table: ${tableId}`));
  }

  const client = new FeishuBitableClient({ appToken, tableId, botToken });
  const result = await client.upsert(rows, toFeishuFields);
  printSyncSummary(result);
  printWarnings(result.warning_samples);
  printWarnings(result.error_samples);
  return result.failed === 0 ? 0 : 2;
}

export async function main(argv) {
  await prepareRuntimePaths(repoRoot);
  loadDotenv();
  const { positional, options } = parseArgs(argv);
  const [command] = positional;
  const lookbackDays = resolveLookbackDays(options.lookbackDays);
  const uiMode = ["auto", "cli", "none"].includes(options.ui) ? options.ui : "auto";

  switch (command) {
    case "doctor":
      process.exitCode = await runDoctor(lookbackDays, uiMode);
      return;
    case "collect":
      process.exitCode = await runCollect(lookbackDays, uiMode);
      return;
    case "sync":
      process.exitCode = await runSync(lookbackDays, uiMode);
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
