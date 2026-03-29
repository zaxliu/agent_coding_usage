import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const thisDir = path.dirname(fileURLToPath(import.meta.url));
export const repoRoot = path.resolve(thisDir, "../../..");
export const envPath = path.join(repoRoot, ".env");
export const reportsDir = path.join(repoRoot, "reports");

export function loadDotenv(filePath = envPath) {
  if (!fs.existsSync(filePath)) {
    return;
  }
  const text = fs.readFileSync(filePath, "utf8");
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) {
      continue;
    }
    const [rawKey, ...rest] = line.split("=");
    const key = rawKey.trim();
    const value = rest.join("=").trim().replace(/^['"]|['"]$/g, "");
    if (!(key in process.env)) {
      process.env[key] = value;
    }
  }
}

export function getEnv(name, fallback = "") {
  const value = process.env[name];
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

export function requiredEnv(name) {
  const value = getEnv(name);
  if (!value) {
    throw new Error(`missing env var: ${name}`);
  }
  return value;
}

export function intEnv(name, fallback) {
  const raw = getEnv(name, String(fallback));
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}
