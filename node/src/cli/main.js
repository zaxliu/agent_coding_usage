import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { aggregateEvents } from "../core/aggregation.js";
import { hashSourceHost, hashUser } from "../core/identity.js";
import { buildCursorCollector } from "../collectors/cursor-dashboard.js";
import { collectLocalUsage, probeLocalUsage } from "../collectors/local.js";
import { toFeishuFields } from "../core/privacy.js";
import { maybeCaptureCursorToken } from "../runtime/cursor-login.js";
import {
  getEnvPath,
  getEnv,
  getReportsDir,
  getRuntimeStatePath,
  intEnv,
  loadDotenv,
  prepareRuntimePaths,
  requiredEnv,
  repoRoot,
} from "../runtime/env.js";
import { REQUIRED_FEISHU_FIELDS, feishuSchemaWarnings } from "../runtime/feishu-schema.js";
import { resolveFeishuTargetsFromEnv, selectFeishuTargets } from "../runtime/feishu-targets.js";
import {
  createMissingFeishuFields,
  FeishuBitableClient,
  fetchBitableFieldTypeMap,
  fetchFirstTableId,
  fetchTenantAccessToken,
} from "../runtime/feishu.js";
import { readOfflineBundle, writeOfflineBundle } from "../runtime/offline-bundle.js";
import { parseRemoteConfigsFromEnv } from "../runtime/remotes.js";
import { printDoctorReport, printSyncSummary, printTerminalReport, writeCsvReport } from "../runtime/reporting.js";
import { info, warn } from "../runtime/ui.js";

function printHelp() {
  console.log(`Usage: llm-usage-node <command> [options]

Commands:
  init          Initialize the active runtime .env and reports directory
  doctor        Probe local data sources in Node
  whoami        Show ORG_USERNAME, user_hash, and per-host source hashes
  collect       Collect local usage in Node and write usage_report.csv to the user data dir
  sync          Collect local usage in Node and upsert rows to Feishu
  import-config Import legacy .env and runtime_state.json into active runtime paths
  export-bundle Collect usage and write an offline bundle for later upload

Options:
  --lookback-days N
  --ui auto|cli|none
  --feishu
  --feishu-target NAME
  --all-feishu-targets
  --feishu-bitable-schema
  --from PATH
  --from-bundle PATH
  --output PATH
  --dry-run
  --force
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
    sourceRoot: "",
    output: "",
    dryRun: false,
    force: false,
    fromBundle: "",
    feishu: false,
    feishuTargets: [],
    allFeishuTargets: false,
    feishuBitableSchema: false,
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
    if (value === "--from") {
      options.sourceRoot = argv[index + 1] || "";
      index += 1;
      continue;
    }
    if (value === "--output") {
      options.output = argv[index + 1] || "";
      index += 1;
      continue;
    }
    if (value === "--from-bundle") {
      options.fromBundle = argv[index + 1] || "";
      index += 1;
      continue;
    }
    if (value === "--feishu") {
      options.feishu = true;
      continue;
    }
    if (value === "--feishu-target") {
      options.feishuTargets.push(argv[index + 1] || "");
      index += 1;
      continue;
    }
    if (value === "--all-feishu-targets") {
      options.allFeishuTargets = true;
      continue;
    }
    if (value === "--feishu-bitable-schema") {
      options.feishuBitableSchema = true;
      continue;
    }
    if (value === "--dry-run") {
      options.dryRun = true;
      continue;
    }
    if (value === "--force") {
      options.force = true;
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

function requiredOrgUsername() {
  const username = getEnv("ORG_USERNAME");
  if (!username) {
    throw new Error("missing env var: ORG_USERNAME");
  }
  return username;
}

function packageVersion() {
  const packageJsonPath = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../package.json");
  return JSON.parse(fs.readFileSync(packageJsonPath, "utf8")).version || "0.0.0";
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

function buildTerminalHostLabels() {
  const username = getEnv("ORG_USERNAME");
  const salt = getEnv("HASH_SALT");
  if (!username || !salt) {
    return {};
  }
  const labels = {
    [hashSourceHost(username, "local", salt)]: "local",
  };
  for (const config of parseRemoteConfigsFromEnv()) {
    labels[hashSourceHost(username, config.source_label, salt)] = config.source_label;
  }
  return labels;
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

function resolveFeishuSelection(options, { defaultOnly = true } = {}) {
  return selectFeishuTargets(resolveFeishuTargetsFromEnv(process.env), {
    names: options.feishuTargets,
    all: options.allFeishuTargets,
    defaultOnly,
  });
}

async function resolveFeishuBotToken(target) {
  if (target.botToken) {
    return target.botToken;
  }
  if (!target.appId || !target.appSecret) {
    throw new Error(`target ${target.name}: missing Feishu app credentials`);
  }
  return fetchTenantAccessToken({ appId: target.appId, appSecret: target.appSecret });
}

async function resolveFeishuTableId(target, botToken) {
  if (!target.appToken) {
    throw new Error(`target ${target.name}: missing Feishu app token`);
  }
  if (target.tableId) {
    return target.tableId;
  }
  const tableId = await fetchFirstTableId({ appToken: target.appToken, botToken });
  console.log(info(`FEISHU_TABLE_ID empty for target ${target.name}, auto-selected first table: ${tableId}`));
  return tableId;
}

async function runFeishuDoctor(options) {
  const targets = resolveFeishuSelection(options, {
    defaultOnly: !options.feishuTargets.length && !options.allFeishuTargets,
  });
  if (!targets.length) {
    console.log(warn("no Feishu targets configured"));
    return 0;
  }
  let exitCode = 0;
  for (const target of targets) {
    try {
      const botToken = await resolveFeishuBotToken(target);
      const tableId = await resolveFeishuTableId(target, botToken);
      const fieldTypeMap = await fetchBitableFieldTypeMap({
        appToken: target.appToken,
        tableId,
        botToken,
      });
      const warnings = feishuSchemaWarnings(fieldTypeMap, REQUIRED_FEISHU_FIELDS);
      if (warnings.length) {
        console.log(`feishu[${target.name}]: WARN`);
        printWarnings(warnings);
      } else {
        console.log(`feishu[${target.name}]: OK`);
      }
    } catch (error) {
      console.log(warn(`target ${target.name}: ${error.message}`));
      exitCode = 2;
    }
  }
  return exitCode;
}

async function runDoctor(_lookbackDays, _uiMode, options) {
  if ((options.feishuTargets.length || options.allFeishuTargets) && !options.feishu) {
    console.log("error: --feishu-target and --all-feishu-targets require --feishu");
    return 2;
  }
  if (options.feishu) {
    return runFeishuDoctor(options);
  }
  printDoctorReport({
    envPath: getEnvPath(),
    probes: await probeLocalUsage(),
    warnings: remoteCollectionWarnings(),
  });
  return 0;
}

async function ensureFeishuSchemaForTargets(targets, { dryRun = false } = {}) {
  let exitCode = 0;
  for (const target of targets) {
    try {
      const botToken = await resolveFeishuBotToken(target);
      const tableId = await resolveFeishuTableId(target, botToken);
      const client = new FeishuBitableClient({ appToken: target.appToken, tableId, botToken });
      const created = await createMissingFeishuFields(client, { dryRun });
      if (created.length) {
        console.log(info(`${dryRun ? "would create" : "created"} columns for ${target.name}: ${created.join(", ")}`));
      } else {
        console.log(info(`no missing columns for ${target.name}`));
      }
    } catch (error) {
      console.log(warn(`target ${target.name}: ${error.message}`));
      exitCode = 2;
    }
  }
  return exitCode;
}

async function runInit(options) {
  loadDotenv();
  fs.mkdirSync(getReportsDir(), { recursive: true });
  console.log(info(`env: ${getEnvPath()}`));
  console.log(info(`reports: ${getReportsDir()}`));
  if ((options.feishuTargets.length || options.allFeishuTargets) && !options.feishuBitableSchema) {
    console.log("error: --feishu-target and --all-feishu-targets require --feishu-bitable-schema");
    return 2;
  }
  if (!options.feishuBitableSchema) {
    return 0;
  }
  const targets = resolveFeishuSelection(options, {
    defaultOnly: !options.feishuTargets.length && !options.allFeishuTargets,
  });
  if (!targets.length) {
    console.log(warn("no Feishu targets configured"));
    return 0;
  }
  return ensureFeishuSchemaForTargets(targets, { dryRun: options.dryRun });
}

async function runWhoami() {
  const username = requiredOrgUsername();
  const salt = requiredEnv("HASH_SALT");
  console.log(`ORG_USERNAME: ${username}`);
  console.log(`user_hash: ${hashUser(username, salt)}`);
  console.log(`source_host_hash(local): ${hashSourceHost(username, "local", salt)}`);
  for (const config of parseRemoteConfigsFromEnv()) {
    console.log(`source_host_hash(${config.alias.toLowerCase()}): ${hashSourceHost(username, config.source_label, salt)}`);
  }
  return 0;
}

function buildImportPlan(sourceRoot, force) {
  const plan = [];
  const messages = [];
  const targets = [
    { sourcePath: path.join(sourceRoot, ".env"), targetPath: getEnvPath(), label: ".env" },
    {
      sourcePath: path.join(sourceRoot, "reports", "runtime_state.json"),
      targetPath: getRuntimeStatePath(),
      label: "runtime state",
    },
  ];

  for (const target of targets) {
    if (!fs.existsSync(target.sourcePath)) {
      messages.push(`missing: ${target.label} source not found at ${target.sourcePath}`);
      continue;
    }
    if (
      fs.existsSync(target.targetPath) &&
      fs.realpathSync(target.sourcePath) === fs.realpathSync(target.targetPath)
    ) {
      messages.push(`skip: ${target.label} source and target are the same file at ${target.targetPath}`);
      continue;
    }
    if (fs.existsSync(target.targetPath) && !force) {
      messages.push(`skip: ${target.label} target already exists at ${target.targetPath}`);
      continue;
    }
    plan.push({
      ...target,
      action: fs.existsSync(target.targetPath) ? "overwrite" : "copy",
    });
  }

  return { plan, messages };
}

async function runImportConfig(options) {
  const sourceRoot = path.resolve(options.sourceRoot || process.cwd());
  const { plan, messages } = buildImportPlan(sourceRoot, options.force);
  const sourceExists = [path.join(sourceRoot, ".env"), path.join(sourceRoot, "reports", "runtime_state.json")].some((item) =>
    fs.existsSync(item),
  );

  for (const message of messages) {
    console.log(message);
  }

  if (!plan.length) {
    if (sourceExists) {
      console.log("info: nothing imported");
      return 0;
    }
    console.log("error: no importable legacy config files found");
    return 1;
  }

  for (const item of plan) {
    console.log(`plan: ${item.action} ${item.sourcePath} -> ${item.targetPath}`);
  }

  if (options.dryRun) {
    console.log("dry-run: no files were written");
    return 0;
  }

  for (const item of plan) {
    fs.mkdirSync(path.dirname(item.targetPath), { recursive: true });
    fs.copyFileSync(item.sourcePath, item.targetPath);
    console.log(`imported: ${item.targetPath}`);
  }
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
  const { rows, warnings } = await buildAggregates(lookbackDays);
  printWarnings(warnings);
  printTerminalReport(rows, { hostLabels: buildTerminalHostLabels() });
  const csvPath = writeCsvReport(rows, getReportsDir());
  console.log(info(`csv: ${csvPath}`));
  return 0;
}

async function syncRowsToFeishu(rows, options) {
  if (options.allFeishuTargets && options.feishuTargets.length) {
    throw new Error("cannot combine --all-feishu-targets with --feishu-target");
  }
  if (options.dryRun) {
    return 0;
  }
  const targets = resolveFeishuSelection(options, {
    defaultOnly: !options.feishuTargets.length && !options.allFeishuTargets,
  });
  if (!targets.length) {
    throw new Error("no Feishu targets configured");
  }
  let exitCode = 0;
  for (const target of targets) {
    const code = await syncRowsToSingleFeishuTarget(rows, target);
    if (exitCode === 0 && code !== 0) {
      exitCode = code;
    }
  }
  return exitCode;
}

async function syncRowsToSingleFeishuTarget(rows, target) {
  const botToken = await resolveFeishuBotToken(target);
  const tableId = await resolveFeishuTableId(target, botToken);
  const client = new FeishuBitableClient({ appToken: target.appToken, tableId, botToken });
  const result = await client.upsert(rows, toFeishuFields);
  console.log(info(`feishu target: ${target.name}`));
  printSyncSummary(result);
  printWarnings(result.warning_samples);
  printWarnings(result.error_samples);
  return result.failed === 0 ? 0 : 2;
}

async function runExportBundle(lookbackDays, uiMode, options) {
  await maybeCaptureCursorToken({
    timeoutSec: options.cursorLoginTimeoutSec,
    browser: options.cursorLoginBrowser,
    userDataDir: options.cursorLoginUserDataDir,
    loginMode: options.cursorLoginMode,
    lookbackDays,
    envPath: getEnvPath(),
    buildCursorCollector: () => buildCollectorsForCursor(),
  });
  const { rows, warnings } = await buildAggregates(lookbackDays);
  const timeZone = getEnv("TIMEZONE", "Asia/Shanghai");
  const outputPath =
    options.output ||
    path.join(getReportsDir(), `llm-usage-bundle-${new Date().toISOString().replaceAll(":", "-")}.zip`);
  await writeOfflineBundle(rows, outputPath, {
    warnings,
    timezoneName: timeZone,
    lookbackDays,
    toolVersion: packageVersion(),
    includeCsv: true,
  });
  console.log(info(`bundle: ${outputPath}`));
  return 0;
}

async function runSync(lookbackDays, uiMode, options) {
  if (options.fromBundle) {
    const { rows, warnings } = await readOfflineBundle(options.fromBundle);
    printWarnings(warnings);
    printTerminalReport(rows, { hostLabels: buildTerminalHostLabels() });
    return syncRowsToFeishu(rows, options);
  }

  await maybeCaptureCursorToken({
    timeoutSec: options.cursorLoginTimeoutSec,
    browser: options.cursorLoginBrowser,
    userDataDir: options.cursorLoginUserDataDir,
    loginMode: options.cursorLoginMode,
    lookbackDays,
    envPath: getEnvPath(),
    buildCursorCollector: () => buildCollectorsForCursor(),
  });
  const { rows, warnings } = await buildAggregates(lookbackDays);
  printWarnings(warnings);
  printTerminalReport(rows, { hostLabels: buildTerminalHostLabels() });
  const csvPath = writeCsvReport(rows, getReportsDir());
  console.log(info(`csv: ${csvPath}`));
  return syncRowsToFeishu(rows, options);
}

export async function main(argv) {
  const { positional, options } = parseArgs(argv);
  const [command] = positional;
  if (
    options.help &&
    (command === "collect" ||
      command === "sync" ||
      command === "doctor" ||
      command === "init" ||
      command === "whoami" ||
      command === "import-config" ||
      command === "export-bundle" ||
      command === undefined)
  ) {
    printHelp();
    process.exitCode = 0;
    return;
  }
  await prepareRuntimePaths(repoRoot);
  if (!["init", "import-config"].includes(command || "")) {
    loadDotenv();
  }
  const lookbackDays = resolveLookbackDays(options.lookbackDays);
  const uiMode = ["auto", "cli", "none"].includes(options.ui) ? options.ui : "auto";

  switch (command) {
    case "init":
      process.exitCode = await runInit(options);
      return;
    case "doctor":
      process.exitCode = await runDoctor(lookbackDays, uiMode, options);
      return;
    case "whoami":
      process.exitCode = await runWhoami();
      return;
    case "collect":
      process.exitCode = await runCollect(lookbackDays, uiMode, options);
      return;
    case "sync":
      process.exitCode = await runSync(lookbackDays, uiMode, options);
      return;
    case "import-config":
      process.exitCode = await runImportConfig(options);
      return;
    case "export-bundle":
      process.exitCode = await runExportBundle(lookbackDays, uiMode, options);
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
