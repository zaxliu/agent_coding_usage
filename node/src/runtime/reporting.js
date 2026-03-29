import fs from "node:fs";
import path from "node:path";

import { banner, info, section } from "./ui.js";

export function printTerminalReport(rows) {
  console.log(section("Usage Report"));
  console.log(banner("Daily Aggregates", `${rows.length} rows`));
  const headers = ["日期", "来源", "工具", "模型", "输入", "缓存", "输出"];
  const widths = [10, 14, 14, 28, 10, 10, 10];
  console.log(headers.map((value, index) => value.padEnd(widths[index])).join("  "));
  console.log(widths.map((width) => "─".repeat(width)).join("  "));
  for (const row of rows) {
    const data = [
      row.date_local,
      row.source_host_hash.slice(0, 14),
      row.tool,
      row.model.slice(0, 28),
      String(row.input_tokens_sum),
      String(row.cache_tokens_sum),
      String(row.output_tokens_sum),
    ];
    console.log(data.map((value, index) => value.padEnd(widths[index])).join("  "));
  }
}

export function printDoctorReport({ envPath, probes, warnings }) {
  console.log(banner("LLM Usage Node", "doctor"));
  console.log(info(`env: ${envPath}`));
  console.log(section("Collectors"));
  for (const probe of probes || []) {
    const state = probe.ok ? "OK" : "WARN";
    console.log(`  ${state.padEnd(4)} ${probe.name}[${probe.source_name}]  ${probe.message}`);
  }
  if (warnings?.length) {
    console.log(section("Warnings"));
    for (const warning of warnings) {
      console.log(`  - ${warning}`);
    }
  }
}

export function printSyncSummary(result) {
  console.log(section("Sync Result"));
  console.log(banner("Feishu Upsert", `created ${result.created}, updated ${result.updated}, failed ${result.failed}`));
}

export function writeCsvReport(rows, outputDir) {
  fs.mkdirSync(outputDir, { recursive: true });
  const headers = [
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
  const lines = [headers.join(",")];
  for (const row of rows) {
    lines.push(
      headers
        .map((key) => {
          const value = row[key] ?? "";
          const text = String(value).replace(/"/g, "\"\"");
          return /[",\n]/.test(text) ? `"${text}"` : text;
        })
        .join(","),
    );
  }
  const filePath = path.join(outputDir, "usage_report.csv");
  fs.writeFileSync(filePath, `${lines.join("\n")}\n`, "utf8");
  return filePath;
}
