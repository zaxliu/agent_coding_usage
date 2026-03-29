import process from "node:process";

import { aggregateEvents } from "../core/aggregation.js";
import { hashUser } from "../core/identity.js";
import { collectLocalUsage, localCollectorNames, probeLocalUsage } from "../collectors/local.js";
import { toFeishuFields } from "../core/privacy.js";
import {
  getEnv,
  getEnvPath,
  getReportsDir,
  getRuntimeStatePath,
  intEnv,
  loadDotenv,
  prepareRuntimePaths,
  requiredEnv,
  repoRoot,
} from "../runtime/env.js";
import { FeishuBitableClient, fetchFirstTableId, fetchTenantAccessToken } from "../runtime/feishu.js";
import { confirmSaveTemporaryRemote, persistTemporaryRemote, selectRemotes } from "../runtime/interaction.js";
import {
  buildEnvWithTemporaryRemotes,
  buildTemporaryRemote,
  parseRemoteConfigsFromEnv,
  probeRemoteSsh,
  uniqueAlias,
} from "../runtime/remotes.js";
import { printDoctorReport, printSyncSummary, printTerminalReport, writeCsvReport } from "../runtime/reporting.js";
import { loadSelectedRemoteAliases, saveSelectedRemoteAliases } from "../runtime/state.js";
import { doctorViaPython, collectEventsViaPython } from "../runtime/python-bridge.js";
import { info, warn } from "../runtime/ui.js";

function printHelp() {
  console.log(`Usage: llm-usage-node <command> [options]

Commands:
  doctor        Probe configured data sources via the Python collector bridge
  collect       Collect usage, aggregate in Node, and write usage_report.csv to the user data dir
  sync          Collect usage, aggregate in Node, and upsert rows to Feishu

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

function pythonWarningBelongsToReplacedTool(message) {
  const text = String(message || "");
  return localCollectorNames().some((tool) => {
    if (!shouldReplacePythonTool(tool)) {
      return false;
    }
    const pythonName = tool === "claude_code" ? "claude_code" : tool;
    return text.startsWith(`${pythonName}:`) || text.includes(`for ${pythonName}`);
  });
}

function shouldReplacePythonTool(tool) {
  if (tool === "cursor" && getEnv("CURSOR_WEB_SESSION_TOKEN")) {
    return false;
  }
  return localCollectorNames().includes(tool);
}

function printWarnings(warnings) {
  for (const message of warnings || []) {
    console.log(warn(message));
  }
}

async function prepareRemoteSelection(uiMode) {
  const configuredRemotes = parseRemoteConfigsFromEnv();
  const runtimeStatePath = getRuntimeStatePath();
  const envPath = getEnvPath();
  const stateAliases = loadSelectedRemoteAliases(runtimeStatePath);
  const configuredAliases = configuredRemotes.map((item) => item.alias);
  const defaultAliases = stateAliases.length
    ? stateAliases.filter((alias) => configuredAliases.includes(alias))
    : [...configuredAliases];

  const selection = await selectRemotes(configuredRemotes, defaultAliases, {
    uiMode,
    remoteValidator: probeRemoteSsh,
    buildTemporaryRemote,
  });
  saveSelectedRemoteAliases(runtimeStatePath, selection.selectedAliases);
  let selectedAliases = [...selection.selectedAliases];

  if (selection.temporaryRemotes.length) {
    for (const remote of selection.temporaryRemotes) {
      remote.alias = uniqueAlias(remote.alias, configuredAliases);
      if (await confirmSaveTemporaryRemote({ uiMode })) {
        const alias = persistTemporaryRemote(remote, configuredAliases, envPath);
        configuredAliases.push(alias);
        selectedAliases.push(alias);
        console.log(info(`已将临时远端保存到 .env: ${alias}`));
      } else {
        selectedAliases.push(remote.alias);
      }
    }
  }

  saveSelectedRemoteAliases(runtimeStatePath, selectedAliases);
  const temporaryBundle = buildEnvWithTemporaryRemotes(process.env, selection.temporaryRemotes);
  return {
    selectedAliases,
    envOverrides: temporaryBundle.env,
  };
}

async function buildAggregates(lookbackDays, uiMode) {
  const remoteSelection = await prepareRemoteSelection(uiMode);
  const envOverrides = {
    ...remoteSelection.envOverrides,
    LLM_USAGE_SELECTED_REMOTE_ALIASES: remoteSelection.selectedAliases.join(","),
  };
  const localPayload = collectLocalUsage(lookbackDays);
  const bridgePayload = collectEventsViaPython(lookbackDays, envOverrides);
  const bridgeEvents = deserializeEvents(bridgePayload).filter((event) => !shouldReplacePythonTool(event.tool));
  const username = requiredEnv("ORG_USERNAME");
  const salt = requiredEnv("HASH_SALT");
  const userHash = hashUser(username, salt);
  const timeZone = getEnv("TIMEZONE", "Asia/Shanghai");
  const rows = aggregateEvents([...serializeLocalEvents(localPayload.events), ...bridgeEvents], {
    userHash,
    timeZone,
  });
  return {
    rows,
    warnings: mergeWarnings(
      localPayload.warnings,
      (bridgePayload.warnings || []).filter((warning) => !pythonWarningBelongsToReplacedTool(warning)),
    ),
  };
}

async function runDoctor(lookbackDays, uiMode) {
  const remoteSelection = await prepareRemoteSelection(uiMode);
  const envOverrides = {
    ...remoteSelection.envOverrides,
    LLM_USAGE_SELECTED_REMOTE_ALIASES: remoteSelection.selectedAliases.join(","),
  };
  const payload = doctorViaPython(lookbackDays, envOverrides);
  const probes = [
    ...probeLocalUsage(),
    ...(payload.probes || []).filter((probe) => !shouldReplacePythonTool(probe.name)),
  ];
  printDoctorReport({
    envPath: getEnvPath(),
    probes,
    warnings: (payload.warnings || []).filter((warning) => !pythonWarningBelongsToReplacedTool(warning)),
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
