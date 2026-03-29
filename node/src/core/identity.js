import { sha256Hex } from "./hash.js";

export function hashUser(username, salt) {
  return sha256Hex(`${username}|${salt}`);
}

export function hashSourceHost(username, sourceLabel, salt) {
  return sha256Hex(`${username}|${sourceLabel}|${salt}`);
}

export function resolveIdentity(model, sessionFingerprint) {
  if (typeof sessionFingerprint === "string" && sessionFingerprint.trim()) {
    return sessionFingerprint.trim();
  }
  return `model:${model}`;
}

export function buildRowKey({
  userHash,
  sourceHostHash,
  dateLocal,
  tool,
  model,
  sessionFingerprint = null,
}) {
  const identity = resolveIdentity(model, sessionFingerprint);
  return sha256Hex(`${userHash}|${sourceHostHash}|${dateLocal}|${tool}|${identity}`);
}
