import process from "node:process";

const LEGACY_KEYS = [
  "FEISHU_APP_TOKEN",
  "FEISHU_TABLE_ID",
  "FEISHU_APP_ID",
  "FEISHU_APP_SECRET",
  "FEISHU_BOT_TOKEN",
];

export function normalizeFeishuTargetName(raw) {
  const value = String(raw || "").trim().toLowerCase();
  if (!/^[a-z0-9_]+$/u.test(value)) {
    throw new Error(`invalid feishu target name: ${JSON.stringify(raw)}`);
  }
  if (value === "default") {
    throw new Error("feishu target name 'default' is reserved");
  }
  return value;
}

function legacyAnyNonEmpty(env) {
  return LEGACY_KEYS.some((key) => String(env[key] || "").trim());
}

function parseFeishuTargetsList(raw) {
  const seen = new Set();
  const ordered = [];
  for (const part of String(raw || "").split(",")) {
    if (!part.trim()) {
      continue;
    }
    const name = normalizeFeishuTargetName(part);
    if (seen.has(name)) {
      throw new Error(`duplicate feishu target name: ${name}`);
    }
    seen.add(name);
    ordered.push(name);
  }
  return ordered;
}

function targetPrefix(name) {
  return `FEISHU_${name.toUpperCase()}_`;
}

function prefixedOrLegacy(env, prefix, suffix, legacyKey) {
  const prefixed = String(env[`${prefix}${suffix}`] || "").trim();
  if (prefixed) {
    return { value: prefixed, inherited: false };
  }
  const legacy = String(env[legacyKey] || "").trim();
  return { value: legacy, inherited: Boolean(legacy) };
}

function defaultFromLegacy(env) {
  if (!legacyAnyNonEmpty(env)) {
    return null;
  }
  return {
    name: "default",
    appToken: String(env.FEISHU_APP_TOKEN || "").trim(),
    tableId: String(env.FEISHU_TABLE_ID || "").trim(),
    appId: String(env.FEISHU_APP_ID || "").trim(),
    appSecret: String(env.FEISHU_APP_SECRET || "").trim(),
    botToken: String(env.FEISHU_BOT_TOKEN || "").trim(),
    inheritedAuth: false,
  };
}

function namedTargetFromEnv(env, name) {
  const prefix = targetPrefix(name);
  const appId = prefixedOrLegacy(env, prefix, "APP_ID", "FEISHU_APP_ID");
  const appSecret = prefixedOrLegacy(env, prefix, "APP_SECRET", "FEISHU_APP_SECRET");
  const botToken = prefixedOrLegacy(env, prefix, "BOT_TOKEN", "FEISHU_BOT_TOKEN");
  return {
    name,
    appToken: String(env[`${prefix}APP_TOKEN`] || "").trim(),
    tableId: String(env[`${prefix}TABLE_ID`] || "").trim(),
    appId: appId.value,
    appSecret: appSecret.value,
    botToken: botToken.value,
    inheritedAuth: appId.inherited || appSecret.inherited || botToken.inherited,
  };
}

export function resolveFeishuTargetsFromEnv(env = process.env) {
  const out = [];
  const defaultTarget = defaultFromLegacy(env);
  if (defaultTarget) {
    out.push(defaultTarget);
  }
  for (const name of parseFeishuTargetsList(env.FEISHU_TARGETS || "")) {
    out.push(namedTargetFromEnv(env, name));
  }
  return out;
}

export function selectFeishuTargets(targets, { names = [], all = false, defaultOnly = true } = {}) {
  if (all && names.length) {
    throw new Error("cannot combine all feishu targets with explicit names");
  }
  const byName = new Map(targets.map((target) => [target.name, target]));
  if (all) {
    return [...targets];
  }
  if (names.length) {
    return names.map((name) => {
      const normalized = String(name || "").trim().toLowerCase();
      if (!byName.has(normalized)) {
        throw new Error(`unknown feishu target: ${normalized}`);
      }
      return byName.get(normalized);
    });
  }
  if (defaultOnly) {
    return byName.has("default") ? [byName.get("default")] : [];
  }
  return [];
}
