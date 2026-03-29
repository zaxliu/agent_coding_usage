import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { readEventsFromFile } from "./parsing.js";

const NOISE_PARTS = new Set(["extensions", "node_modules", ".git", ".cache", "Cache", "__pycache__"]);

function expandUser(input) {
  if (!input.startsWith("~")) {
    return path.resolve(input);
  }
  return path.join(os.homedir(), input.slice(1));
}

function normalizeForGlob(filePath) {
  return filePath.split(path.sep).join("/");
}

function escapeRegex(text) {
  return text.replace(/[|\\{}()[\]^$+?.]/gu, "\\$&");
}

function globToRegExp(pattern) {
  let output = "^";
  for (let index = 0; index < pattern.length; index += 1) {
    const current = pattern[index];
    const next = pattern[index + 1];
    if (current === "*" && next === "*") {
      output += ".*";
      index += 1;
      continue;
    }
    if (current === "*") {
      output += "[^/]*";
      continue;
    }
    if (current === "?") {
      output += "[^/]";
      continue;
    }
    output += escapeRegex(current);
  }
  return new RegExp(output + "$", "u");
}

function rootFromPattern(pattern) {
  const expanded = expandUser(pattern);
  const normalized = normalizeForGlob(expanded);
  const match = normalized.match(/[*?]/u);
  const prefix = match ? normalized.slice(0, match.index) : normalized;
  const trimmed = prefix.replace(/\/+$/u, "");
  if (!trimmed) {
    return path.parse(expanded).root || ".";
  }
  if (fs.existsSync(trimmed) && fs.statSync(trimmed).isFile()) {
    return trimmed;
  }
  return trimmed || path.parse(expanded).root || ".";
}

function isNoisePath(filePath) {
  return filePath.split(path.sep).some((part) => NOISE_PARTS.has(part));
}

function listMatchesForPattern(pattern) {
  const expanded = expandUser(pattern);
  const normalizedPattern = normalizeForGlob(expanded);
  const matcher = globToRegExp(normalizedPattern);
  const root = rootFromPattern(pattern);
  if (!fs.existsSync(root)) {
    return [];
  }
  if (fs.statSync(root).isFile()) {
    return matcher.test(normalizeForGlob(root)) ? [root] : [];
  }

  const out = [];
  const stack = [root];
  while (stack.length) {
    const current = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      const candidate = path.join(current, entry.name);
      if (entry.isDirectory()) {
        stack.push(candidate);
        continue;
      }
      if (!entry.isFile()) {
        continue;
      }
      const lower = candidate.toLowerCase();
      if (!lower.endsWith(".json") && !lower.endsWith(".jsonl")) {
        continue;
      }
      if (isNoisePath(candidate)) {
        continue;
      }
      if (matcher.test(normalizeForGlob(candidate))) {
        out.push(candidate);
      }
    }
  }
  return out;
}

function shortenWarning(warning, limit = 120) {
  const compact = String(warning || "").replace(/\s+/gu, " ").trim();
  return compact.length <= limit ? compact : compact.slice(0, limit - 3) + "...";
}

export class FileCollector {
  constructor(name, patterns, { sourceName = "local", sourceHostHash = "" } = {}) {
    this.name = name;
    this.patterns = patterns;
    this.sourceName = sourceName;
    this.sourceHostHash = sourceHostHash;
  }

  matchedFiles() {
    const files = new Set();
    for (const pattern of this.patterns) {
      for (const filePath of listMatchesForPattern(pattern)) {
        files.add(filePath);
      }
    }
    return [...files].sort((left, right) => left.localeCompare(right));
  }

  probe() {
    const files = this.matchedFiles();
    if (!files.length) {
      return { ok: false, message: `no data files found for ${this.name}` };
    }
    let parsableEvents = 0;
    const parseWarnings = [];
    for (const filePath of files) {
      const [events, warning] = readEventsFromFile(filePath, this.name);
      if (warning) {
        parseWarnings.push(warning);
        continue;
      }
      parsableEvents += events.length;
    }
    let message = `${files.length} files detected, ${parsableEvents} parsable events`;
    if (parseWarnings.length) {
      message += `, ${parseWarnings.length} parse warnings (first: ${shortenWarning(parseWarnings[0])})`;
    }
    return { ok: parsableEvents > 0, message };
  }

  collect(start, end) {
    const events = [];
    const warnings = [];
    for (const filePath of this.matchedFiles()) {
      const [parsed, warning] = readEventsFromFile(filePath, this.name);
      if (warning) {
        warnings.push(warning);
        continue;
      }
      for (const event of parsed) {
        if (event.eventTime >= start && event.eventTime <= end) {
          events.push({
            ...event,
            sourceHostHash: event.sourceHostHash || this.sourceHostHash,
          });
        }
      }
    }
    if (!events.length) {
      warnings.push(`${this.name}: no usage events in selected time range`);
    }
    return { events, warnings };
  }
}
