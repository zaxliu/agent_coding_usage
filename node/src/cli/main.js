import process from "node:process";

import { aggregateEvents } from "../core/aggregation.js";
import { hashUser } from "../core/identity.js";
import { toFeishuFields } from "../core/privacy.js";
import { envPath, getEnv, intEnv, loadDotenv, reportsDir, requiredEnv } from "../runtime/env.js";
import { FeishuBitableClient, fetchFirstTableId, fetchTenantAccessToken } from "../runtime/feishu.js";
import { collectEventsViaPython, doctorViaPython } from "../runtime/python-bridge.js";
import { printTerminalReport, writeCsvReport } from "../runtime/reporting.js";

function printHelp() {
  console.log(`Usage: llm-usage-node <command> [options]

Commands:
  doctor        Probe configured data sources via the Python collector bridge
  collect       Collect usage, aggregate in Node, and write reports/usage_report.csv
  sync          Collect usage, aggregate in Node, and upsert rows to Feishu
`);
}

function parseArgs(argv) {
  const options = { lookbackDays: undefined };
  const positional = [];

  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--lookback-days") {
      options.lookbackDays = Number.parseInt(argv[index + 1] || "", 10);
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

function deserializeEvents(payload) {
  return (payload.events || []).map((event) => ({
    tool: event.tool,
    model: event.model,
    eventTime: event.event_time,
    inputTokens: event.input_tokens,
    cacheTokens: event.cache_tokens,
    outputTokens: event.output_tokens,
    sessionFingerprint: event.session_fingerprint,
    sourceRef: event.source_ref,
    sourceHostHash: event.source_host_hash || "",
  }));
}

function printWarnings(warnings) {
  for (const warning of warnings || []) {
    console.log(`warn: ${warning}`);
  }
}

async function buildAggregates(lookbackDays) {
  const bridgePayload = collectEventsViaPython(lookbackDays);
  const username = requiredEnv("ORG_USERNAME");
  const salt = requiredEnv("HASH_SALT");
  const userHash = hashUser(username, salt);
  const timeZone = getEnv("TIMEZONE", "Asia/Shanghai");
  const rows = aggregateEvents(deserializeEvents(bridgePayload), {
    userHash,
    timeZone,
  });
  return { rows, warnings: bridgePayload.warnings || [] };
}

async function runDoctor(lookbackDays) {
  const payload = doctorViaPython(lookbackDays);
  console.log(`env: ${envPath}`);
  for (const probe of payload.probes || []) {
    console.log(
      `collector ${probe.name}[${probe.source_name}]: ${probe.ok ? "OK" : "WARN"} - ${probe.message}`,
    );
  }
  printWarnings(payload.warnings);
  return 0;
}

async function runCollect(lookbackDays) {
  const { rows, warnings } = await buildAggregates(lookbackDays);
  printWarnings(warnings);
  printTerminalReport(rows);
  const csvPath = writeCsvReport(rows, reportsDir);
  console.log(`csv: ${csvPath}`);
  return 0;
}

async function runSync(lookbackDays) {
  const { rows, warnings } = await buildAggregates(lookbackDays);
  printWarnings(warnings);
  printTerminalReport(rows);
  const csvPath = writeCsvReport(rows, reportsDir);
  console.log(`csv: ${csvPath}`);

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
    console.log(`info: FEISHU_TABLE_ID empty, auto-selected first table: ${tableId}`);
  }

  const client = new FeishuBitableClient({ appToken, tableId, botToken });
  const result = await client.upsert(rows, toFeishuFields);
  console.log(`飞书同步完成：新增=${result.created} 更新=${result.updated} 失败=${result.failed}`);
  printWarnings(result.warning_samples);
  printWarnings(result.error_samples);
  return result.failed === 0 ? 0 : 2;
}

export async function main(argv) {
  loadDotenv();
  const { positional, options } = parseArgs(argv);
  const [command] = positional;
  const lookbackDays = resolveLookbackDays(options.lookbackDays);

  switch (command) {
    case "doctor":
      process.exitCode = await runDoctor(lookbackDays);
      return;
    case "collect":
      process.exitCode = await runCollect(lookbackDays);
      return;
    case "sync":
      process.exitCode = await runSync(lookbackDays);
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
