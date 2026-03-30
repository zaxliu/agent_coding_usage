import process from "node:process";

import { aggregateEvents } from "../core/aggregation.js";
import { hashUser } from "../core/identity.js";
import { buildCursorCollector } from "../collectors/cursor-dashboard.js";
import { collectLocalUsage, probeLocalUsage } from "../collectors/local.js";
import { toFeishuFields } from "../core/privacy.js";
import { maybeCaptureCursorToken } from "../runtime/cursor-login.js";
import {
  getEnvPath,
  getEnv,
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
  --cursor-login-mode auto|managed-profile|manual
  --cursor-login-browser default|chrome|edge|safari|firefox|chromium|msedge|webkit
  --cursor-login-user-data-dir PATH
  --cursor-login-timeout-sec N
`);
}

function parseArgs(argv) {
  const options = {
    help: false,
    lookbackDays: undefined,
    ui: "auto",
    cursorLoginMode: "auto",
    cursorLoginBrowser: "default",
    cursorLoginUserDataDir: "",
    cursorLoginTimeoutSec: 600,
  };
  const positional = [];

  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "-h" || value === "--help") {
      options.help = true;
      continue;
    }
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
    if (value === "--cursor-login-mode") {
      options.cursorLoginMode = argv[index + 1] || "auto";
      index += 1;
      continue;
    }
    if (value === "--cursor-login-browser") {
      options.cursorLoginBrowser = argv[index + 1] || "default";
      index += 1;
      continue;
    }
    if (value === "--cursor-login-user-data-dir") {
      options.cursorLoginUserDataDir = argv[index + 1] || "";
      index += 1;
      continue;
    }
    if (value === "--cursor-login-timeout-sec") {
      options.cursorLoginTimeoutSec = Number.parseInt(argv[index + 1] || "", 10);
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
  const localPayload = await collectLocalUsage(lookbackDays);
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
    probes: await probeLocalUsage(),
    warnings: remoteCollectionWarnings(),
  });
  return 0;
}

async function runCollect(lookbackDays, uiMode, options) {
  await maybeCaptureCursorToken({
    timeoutSec: options.cursorLoginTimeoutSec,
    browser: options.cursorLoginBrowser,
    userDataDir: options.cursorLoginUserDataDir,
    loginMode: options.cursorLoginMode,
    lookbackDays,
    envPath: getEnvPath(),
    buildCursorCollector: () => buildCollectorsForCursor(),
  });
  const { rows, warnings } = await buildAggregates(lookbackDays, uiMode);
  printWarnings(warnings);
  printTerminalReport(rows);
  const csvPath = writeCsvReport(rows, getReportsDir());
  console.log(info(`csv: ${csvPath}`));
  return 0;
}

async function runSync(lookbackDays, uiMode, options) {
  await maybeCaptureCursorToken({
    timeoutSec: options.cursorLoginTimeoutSec,
    browser: options.cursorLoginBrowser,
    userDataDir: options.cursorLoginUserDataDir,
    loginMode: options.cursorLoginMode,
    lookbackDays,
    envPath: getEnvPath(),
    buildCursorCollector: () => buildCollectorsForCursor(),
  });
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
  const { positional, options } = parseArgs(argv);
  const [command] = positional;
  if (options.help && (command === "collect" || command === "sync" || command === "doctor" || command === undefined)) {
    printHelp();
    process.exitCode = 0;
    return;
  }
  await prepareRuntimePaths(repoRoot);
  loadDotenv();
  const lookbackDays = resolveLookbackDays(options.lookbackDays);
  const uiMode = ["auto", "cli", "none"].includes(options.ui) ? options.ui : "auto";

  switch (command) {
    case "doctor":
      process.exitCode = await runDoctor(lookbackDays, uiMode);
      return;
    case "collect":
      process.exitCode = await runCollect(lookbackDays, uiMode, options);
      return;
    case "sync":
      process.exitCode = await runSync(lookbackDays, uiMode, options);
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

function buildCollectorsForCursor() {
  return {
    probe() {
      return buildCursorCollector().probe();
    },
    collect(start, end) {
      return buildCursorCollector().collect(start, end);
    },
  };
}
