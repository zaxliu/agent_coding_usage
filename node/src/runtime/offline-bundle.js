import fs from "node:fs";
import path from "node:path";

import { strFromU8, unzipSync, zipSync } from "fflate";

export const BUNDLE_SCHEMA_VERSION = 1;
export const BUNDLE_KIND_AGGREGATE_ROWS = "aggregate_rows";
export const MANIFEST_FILENAME = "manifest.json";
export const ROWS_FILENAME = "rows.jsonl";
export const CSV_FILENAME = "usage_report.csv";

const ROW_FIELDS = [
  "date_local",
  "user_hash",
  "source_host_hash",
  "tool",
  "model",
  "input_tokens_sum",
  "cache_tokens_sum",
  "output_tokens_sum",
  "row_key",
  "updated_at",
];

export class OfflineBundleError extends Error {}

function requireNonNegativeInt(value, label) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 0) {
    throw new OfflineBundleError(`${label} must be a non-negative integer`);
  }
  return parsed;
}

function parseIsoDatetime(value, label) {
  const text = String(value || "").trim();
  const parsed = new Date(text);
  if (!text || Number.isNaN(parsed.getTime())) {
    throw new OfflineBundleError(`${label} must be a valid ISO datetime`);
  }
  return text;
}

function renderRowsJsonl(rows) {
  return rows.map((row) => `${JSON.stringify(row)}\n`).join("");
}

function renderRowsCsv(rows) {
  const encode = (value) => {
    const text = String(value ?? "").replace(/"/gu, "\"\"");
    return /[",\n]/u.test(text) ? `"${text}"` : text;
  };
  const lines = [ROW_FIELDS.join(",")];
  for (const row of rows) {
    lines.push(ROW_FIELDS.map((field) => encode(row[field])).join(","));
  }
  return `${lines.join("\n")}\n`;
}

function bundleManifest(rows, warnings, timezoneName, lookbackDays, toolVersion) {
  return {
    schema_version: BUNDLE_SCHEMA_VERSION,
    bundle_kind: BUNDLE_KIND_AGGREGATE_ROWS,
    generated_at: new Date().toISOString(),
    tool_version: String(toolVersion),
    timezone: String(timezoneName),
    lookback_days: requireNonNegativeInt(lookbackDays, "lookback_days"),
    row_count: rows.length,
    warning_count: warnings.length,
    warnings: [...warnings],
  };
}

function parseManifest(text) {
  let manifest;
  try {
    manifest = JSON.parse(text);
  } catch (error) {
    throw new OfflineBundleError(`invalid ${MANIFEST_FILENAME}: ${error.message}`);
  }
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) {
    throw new OfflineBundleError(`${MANIFEST_FILENAME} must contain a JSON object`);
  }
  for (const field of [
    "schema_version",
    "bundle_kind",
    "generated_at",
    "tool_version",
    "timezone",
    "lookback_days",
    "row_count",
    "warning_count",
    "warnings",
  ]) {
    if (!(field in manifest)) {
      throw new OfflineBundleError(`${MANIFEST_FILENAME} missing required field: ${field}`);
    }
  }
  if (manifest.schema_version !== BUNDLE_SCHEMA_VERSION) {
    throw new OfflineBundleError(`unsupported schema_version: ${manifest.schema_version}`);
  }
  if (manifest.bundle_kind !== BUNDLE_KIND_AGGREGATE_ROWS) {
    throw new OfflineBundleError(`unsupported bundle_kind: ${manifest.bundle_kind}`);
  }
  parseIsoDatetime(manifest.generated_at, "generated_at");
  manifest.lookback_days = requireNonNegativeInt(manifest.lookback_days, "lookback_days");
  manifest.row_count = requireNonNegativeInt(manifest.row_count, "row_count");
  manifest.warning_count = requireNonNegativeInt(manifest.warning_count, "warning_count");
  if (!Array.isArray(manifest.warnings) || manifest.warnings.some((item) => typeof item !== "string")) {
    throw new OfflineBundleError(`${MANIFEST_FILENAME} warnings must be a string list`);
  }
  return manifest;
}

function parseRows(text) {
  const rows = [];
  const rowKeys = new Set();
  const userHashes = new Set();
  for (const [index, line] of text.split(/\r?\n/u).entries()) {
    if (!line.trim()) {
      continue;
    }
    let payload;
    try {
      payload = JSON.parse(line);
    } catch (error) {
      throw new OfflineBundleError(`${ROWS_FILENAME} line ${index + 1} is not valid JSON: ${error.message}`);
    }
    const extraFields = Object.keys(payload).filter((field) => !ROW_FIELDS.includes(field));
    if (extraFields.length) {
      throw new OfflineBundleError(`${ROWS_FILENAME} line ${index + 1} has unexpected fields: ${extraFields.join(", ")}`);
    }
    const missingFields = ROW_FIELDS.filter((field) => !(field in payload));
    if (missingFields.length) {
      throw new OfflineBundleError(`${ROWS_FILENAME} line ${index + 1} missing fields: ${missingFields.join(", ")}`);
    }
    for (const field of ["date_local", "user_hash", "source_host_hash", "tool", "model", "row_key", "updated_at"]) {
      if (typeof payload[field] !== "string" || !payload[field].trim()) {
        throw new OfflineBundleError(`${ROWS_FILENAME} line ${index + 1} field ${field} must be a non-empty string`);
      }
    }
    for (const field of ["input_tokens_sum", "cache_tokens_sum", "output_tokens_sum"]) {
      payload[field] = requireNonNegativeInt(payload[field], `${ROWS_FILENAME} line ${index + 1} field ${field}`);
    }
    parseIsoDatetime(payload.updated_at, `${ROWS_FILENAME} line ${index + 1} updated_at`);
    if (rowKeys.has(payload.row_key)) {
      throw new OfflineBundleError(`${ROWS_FILENAME} line ${index + 1} duplicates row_key ${payload.row_key}`);
    }
    rowKeys.add(payload.row_key);
    userHashes.add(payload.user_hash);
    rows.push(payload);
  }
  if (userHashes.size > 1) {
    throw new OfflineBundleError(`${ROWS_FILENAME} must contain exactly one user_hash`);
  }
  return rows;
}

function readZipEntries(filePath) {
  if (!fs.existsSync(filePath)) {
    throw new OfflineBundleError(`bundle path not found: ${filePath}`);
  }
  return Object.fromEntries(
    Object.entries(unzipSync(fs.readFileSync(filePath))).map(([name, bytes]) => [name, strFromU8(bytes)]),
  );
}

export async function writeZipEntries(filePath, entries) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const zipped = zipSync(
    Object.fromEntries(Object.entries(entries).map(([name, content]) => [name, new TextEncoder().encode(content)])),
    { level: 6 },
  );
  fs.writeFileSync(filePath, Buffer.from(zipped));
  return filePath;
}

export async function writeOfflineBundle(rows, outputPath, { warnings, timezoneName, lookbackDays, toolVersion, includeCsv = true }) {
  const manifest = bundleManifest(rows, warnings, timezoneName, lookbackDays, toolVersion);
  const entries = {
    [MANIFEST_FILENAME]: `${JSON.stringify(manifest, null, 2)}\n`,
    [ROWS_FILENAME]: renderRowsJsonl(rows),
  };
  if (includeCsv) {
    entries[CSV_FILENAME] = renderRowsCsv(rows);
  }
  await writeZipEntries(outputPath, entries);
  return outputPath;
}

export async function readOfflineBundle(filePath) {
  const entries = readZipEntries(filePath);
  if (!(MANIFEST_FILENAME in entries)) {
    throw new OfflineBundleError(`bundle missing required file: ${MANIFEST_FILENAME}`);
  }
  if (!(ROWS_FILENAME in entries)) {
    throw new OfflineBundleError(`bundle missing required file: ${ROWS_FILENAME}`);
  }
  const manifest = parseManifest(entries[MANIFEST_FILENAME]);
  const rows = parseRows(entries[ROWS_FILENAME]);
  if (manifest.row_count !== rows.length) {
    throw new OfflineBundleError(`manifest row_count=${manifest.row_count} does not match ${ROWS_FILENAME} lines=${rows.length}`);
  }
  if (manifest.warning_count !== manifest.warnings.length) {
    throw new OfflineBundleError("manifest warning_count does not match warnings length");
  }
  const warnings = [...manifest.warnings];
  const extras = Object.keys(entries).filter((name) => ![MANIFEST_FILENAME, ROWS_FILENAME, CSV_FILENAME].includes(name));
  if (extras.length) {
    warnings.push(`extra bundle files ignored: ${extras.sort().join(", ")}`);
  }
  return { rows, warnings, manifest };
}
