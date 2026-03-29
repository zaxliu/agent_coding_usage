import fs from "node:fs";
import path from "node:path";

function walkJsonNodes(obj) {
  if (Array.isArray(obj)) {
    const out = [];
    for (const item of obj) {
      out.push(...walkJsonNodes(item));
    }
    return out;
  }
  if (obj && typeof obj === "object") {
    const out = [obj];
    for (const value of Object.values(obj)) {
      out.push(...walkJsonNodes(value));
    }
    return out;
  }
  return [];
}

function coerceInt(value) {
  const parsed = Number.parseInt(String(value ?? "0"), 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

function parseTime(raw) {
  if (raw == null) {
    return null;
  }
  if (typeof raw === "number") {
    const ts = raw > 10_000_000_000 ? raw / 1000 : raw;
    return new Date(ts * 1000);
  }
  if (typeof raw === "string") {
    const text = raw.trim();
    if (!text) {
      return null;
    }
    if (/^\d+$/u.test(text)) {
      const ts = Number(text);
      return new Date((ts > 10_000_000_000 ? ts / 1000 : ts) * 1000);
    }
    const candidate = text.replace(/Z$/u, "+00:00");
    const parsed = new Date(candidate);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }
  return null;
}

function extractUsage(node) {
  const usage = node?.usage && typeof node.usage === "object" ? node.usage : node;
  const inputTokens = coerceInt(usage.input_tokens || usage.prompt_tokens || usage.inputTokenCount);
  const outputTokens = coerceInt(usage.output_tokens || usage.completion_tokens || usage.outputTokenCount);
  let cacheTokens = coerceInt(usage.cache_tokens || usage.cached_tokens || usage.cached_input_tokens);
  if (cacheTokens === 0) {
    cacheTokens =
      coerceInt(usage.cache_read_input_tokens) + coerceInt(usage.cache_creation_input_tokens);
  }
  return { inputTokens, cacheTokens, outputTokens };
}

function extractCodexTokenCountUsage(node) {
  if (node?.type !== "event_msg" || node?.payload?.type !== "token_count") {
    return null;
  }
  const usage = node.payload?.info?.last_token_usage;
  if (!usage || typeof usage !== "object") {
    return null;
  }
  const cacheTokens = coerceInt(usage.cached_input_tokens);
  return {
    inputTokens: Math.max(0, coerceInt(usage.input_tokens) - cacheTokens),
    cacheTokens,
    outputTokens: coerceInt(usage.output_tokens),
  };
}

function extractModel(node) {
  for (const key of ["model", "model_name", "modelName"]) {
    const value = node?.[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return "unknown";
}

function extractCodexTurnModel(node) {
  if (node?.type !== "turn_context" || !node.payload || typeof node.payload !== "object") {
    return null;
  }
  const model = extractModel(node.payload);
  if (model !== "unknown") {
    return model;
  }
  const nestedModel = extractModel(node.payload?.collaboration_mode?.settings);
  return nestedModel !== "unknown" ? nestedModel : null;
}

function buildSessionFingerprint(filePath, tool) {
  const stem = path.parse(filePath).name;
  if (tool === "codex") {
    const matches = stem.match(/[0-9a-fA-F-]{36}/gu);
    if (matches?.length) {
      return `codex:${matches[matches.length - 1].toLowerCase()}`;
    }
    return `codex_file:${stem}`;
  }
  if (tool === "copilot_cli") {
    const sessionId = path.basename(path.dirname(filePath)).trim();
    return sessionId ? `copilot_cli:${sessionId}` : `copilot_cli_file:${stem}`;
  }
  return null;
}

function extractTime(node) {
  for (const key of ["timestamp", "created_at", "createdAt", "time", "date"]) {
    const parsed = parseTime(node?.[key]);
    if (parsed) {
      return parsed;
    }
  }
  return null;
}

function normalizeCopilotModel(value) {
  if (typeof value !== "string" || !value.trim()) {
    return "";
  }
  return value.trim().replace(/^copilot\//u, "");
}

function estimateTokensFromText(text) {
  const content = String(text || "").trim();
  if (!content) {
    return 0;
  }
  let asciiChars = 0;
  for (const char of content) {
    if (char.charCodeAt(0) < 128) {
      asciiChars += 1;
    }
  }
  const nonAsciiChars = content.length - asciiChars;
  return Math.max(1, Math.ceil(asciiChars * 0.25 + nonAsciiChars * 0.6));
}

function collectCopilotTextParts(value) {
  if (typeof value === "string") {
    return value ? [value] : [];
  }
  if (Array.isArray(value)) {
    return value.flatMap((item) => collectCopilotTextParts(item));
  }
  if (!value || typeof value !== "object") {
    return [];
  }
  const out = [];
  if (typeof value.text === "string" && value.text) {
    out.push(value.text);
  }
  if (typeof value.value === "string" && value.value) {
    out.push(value.value);
  }
  if (value.content && typeof value.content === "object" && typeof value.content.value === "string" && value.content.value) {
    out.push(value.content.value);
  }
  if (Array.isArray(value.parts)) {
    out.push(...value.parts.flatMap((item) => collectCopilotTextParts(item)));
  }
  if (Array.isArray(value.response)) {
    out.push(...value.response.flatMap((item) => collectCopilotTextParts(item)));
  }
  return out;
}

function extractCopilotVscodeUsage(result) {
  const usage = result?.usage && typeof result.usage === "object" ? result.usage : null;
  const metadata = result?.metadata && typeof result.metadata === "object" ? result.metadata : null;
  let inputTokens = 0;
  let outputTokens = 0;
  let cacheTokens = 0;

  if (usage) {
    inputTokens = coerceInt(usage.promptTokens || usage.inputTokens || usage.prompt_tokens);
    outputTokens = coerceInt(usage.completionTokens || usage.outputTokens || usage.output_tokens);
    cacheTokens = coerceInt(usage.cachedInputTokens || usage.cacheReadTokens || usage.cached_input_tokens);
  }

  if (inputTokens === 0 && outputTokens === 0) {
    inputTokens = coerceInt(result?.promptTokens);
    outputTokens = coerceInt(result?.outputTokens || result?.completionTokens);
  }

  if (inputTokens === 0 && outputTokens === 0 && metadata) {
    inputTokens = coerceInt(metadata.promptTokens || metadata.inputTokens);
    outputTokens = coerceInt(metadata.outputTokens || metadata.completionTokens);
    if (cacheTokens === 0) {
      cacheTokens = coerceInt(metadata.cachedInputTokens || metadata.cacheReadTokens);
    }
  }

  return { inputTokens, cacheTokens, outputTokens };
}

function extractCopilotVscodeModel(session, request) {
  const candidates = [
    request?.modelId,
    request?.model,
    request?.selectedModel?.identifier,
    request?.agent?.modelId,
    request?.result?.metadata?.modelId,
    request?.result?.metadata?.model,
    request?.result?.metadata?.id,
    session?.inputState?.selectedModel?.metadata?.version,
    session?.inputState?.selectedModel?.metadata?.name,
    session?.inputState?.selectedModel?.metadata?.id,
    session?.inputState?.selectedModel?.identifier,
  ];
  for (const value of candidates) {
    const normalized = normalizeCopilotModel(value);
    if (normalized && normalized !== "auto") {
      return normalized;
    }
  }
  if (typeof request?.result?.details === "string" && request.result.details.trim()) {
    return request.result.details.split("•", 1)[0].trim();
  }
  return "unknown";
}

function buildCopilotVscodeEvent(session, request, fallbackTime, sourceRef) {
  const sessionId = session?.sessionId;
  const requestId = request?.requestId;
  const result = request?.result;
  if (typeof sessionId !== "string" || !sessionId.trim() || typeof requestId !== "string" || !requestId.trim()) {
    return null;
  }
  if (!result || typeof result !== "object") {
    return null;
  }
  let { inputTokens, cacheTokens, outputTokens } = extractCopilotVscodeUsage(result);
  if (inputTokens === 0 && cacheTokens === 0 && outputTokens === 0) {
    const inputText = collectCopilotTextParts(request.message).join("\n");
    let outputText = collectCopilotTextParts(request.response).join("\n");
    if (!outputText) {
      outputText = collectCopilotTextParts(result).join("\n");
    }
    inputTokens = estimateTokensFromText(inputText);
    outputTokens = estimateTokensFromText(outputText);
    if (inputTokens === 0 && outputTokens === 0) {
      return null;
    }
  }
  return {
    tool: "copilot_vscode",
    model: extractCopilotVscodeModel(session, request),
    eventTime: parseTime(request.timestamp) || extractTime(result) || fallbackTime,
    inputTokens,
    cacheTokens,
    outputTokens,
    sessionFingerprint: `copilot_vscode:${sessionId.trim()}:${requestId.trim()}`,
    sourceRef,
  };
}

function extractCopilotVscodeEvents(node, fallbackTime, sourceRef) {
  const session = node?.kind === 0 && node.v && typeof node.v === "object" ? node.v : node;
  if (!session || typeof session !== "object" || typeof session.sessionId !== "string" || !Array.isArray(session.requests)) {
    return [];
  }
  const out = [];
  for (const request of session.requests) {
    if (!request || typeof request !== "object") {
      continue;
    }
    const event = buildCopilotVscodeEvent(session, request, fallbackTime, sourceRef);
    if (event) {
      out.push(event);
    }
  }
  return out;
}

function applyCopilotDelta(state, delta) {
  const kind = delta?.kind;
  const keys = Array.isArray(delta?.k) ? delta.k.map((part) => String(part)) : null;
  const value = delta?.v;
  if (kind === 0) {
    return value && (typeof value === "object" || Array.isArray(value)) ? value : state;
  }
  if (!keys?.length) {
    return state;
  }
  const root = state && (typeof state === "object" || Array.isArray(state)) ? state : {};
  let current = root;

  for (let index = 0; index < keys.length - 1; index += 1) {
    const part = keys[index];
    const nextPart = keys[index + 1];
    const wantsList = /^\d+$/u.test(nextPart);
    if (Array.isArray(current)) {
      if (!/^\d+$/u.test(part)) {
        return root;
      }
      const pos = Number(part);
      while (current.length <= pos) {
        current.push(wantsList ? [] : {});
      }
      if (!current[pos] || typeof current[pos] !== "object") {
        current[pos] = wantsList ? [] : {};
      }
      current = current[pos];
      continue;
    }
    if (!current || typeof current !== "object") {
      return root;
    }
    if (!current[part] || typeof current[part] !== "object") {
      current[part] = wantsList ? [] : {};
    }
    current = current[part];
  }

  const last = keys[keys.length - 1];
  if (kind === 1) {
    if (Array.isArray(current)) {
      if (!/^\d+$/u.test(last)) {
        return root;
      }
      const pos = Number(last);
      while (current.length <= pos) {
        current.push(null);
      }
      current[pos] = value;
      return root;
    }
    if (current && typeof current === "object") {
      current[last] = value;
    }
    return root;
  }

  if (kind === 2) {
    let target;
    if (Array.isArray(current)) {
      if (!/^\d+$/u.test(last)) {
        return root;
      }
      const pos = Number(last);
      while (current.length <= pos) {
        current.push([]);
      }
      if (!Array.isArray(current[pos])) {
        current[pos] = [];
      }
      target = current[pos];
    } else if (current && typeof current === "object") {
      if (!Array.isArray(current[last])) {
        current[last] = [];
      }
      target = current[last];
    } else {
      return root;
    }
    if (Array.isArray(value)) {
      target.push(...value);
    } else {
      target.push(value);
    }
  }
  return root;
}

function extractCopilotVscodeEventsFromJsonlText(text, fallbackTime, sourceRef) {
  let state = {};
  let sawDelta = false;
  const events = [];
  for (const [index, raw] of text.split(/\r?\n/u).entries()) {
    const line = raw.trim();
    if (!line) {
      continue;
    }
    let obj;
    try {
      obj = JSON.parse(line);
    } catch {
      continue;
    }
    if (!obj || typeof obj !== "object") {
      continue;
    }
    if (Number.isInteger(obj.kind)) {
      sawDelta = true;
      state = applyCopilotDelta(state, obj);
      continue;
    }
    events.push(...extractCopilotVscodeEvents(obj, fallbackTime, `${sourceRef}:${index + 1}`));
  }
  if (sawDelta && state && typeof state === "object") {
    return extractCopilotVscodeEvents(state, fallbackTime, sourceRef);
  }
  return events;
}

function extractCopilotCliEvents(node, fallbackTime, sourceRef, sessionFingerprint) {
  if (node?.type !== "session.shutdown" || !node.data || typeof node.data !== "object") {
    return [];
  }
  const modelMetrics = node.data.modelMetrics;
  if (!modelMetrics || typeof modelMetrics !== "object") {
    return [];
  }
  const eventTime = extractTime(node) || parseTime(node.data.sessionStartTime) || fallbackTime;
  const prefix = sessionFingerprint || "copilot_cli";
  const out = [];
  for (const [modelName, metrics] of Object.entries(modelMetrics)) {
    if (!modelName.trim() || !metrics || typeof metrics !== "object" || !metrics.usage || typeof metrics.usage !== "object") {
      continue;
    }
    const inputTokens = coerceInt(metrics.usage.inputTokens);
    const outputTokens = coerceInt(metrics.usage.outputTokens);
    const cacheTokens = coerceInt(metrics.usage.cacheReadTokens) + coerceInt(metrics.usage.cacheWriteTokens);
    if (inputTokens === 0 && cacheTokens === 0 && outputTokens === 0) {
      continue;
    }
    out.push({
      tool: "copilot_cli",
      model: modelName.trim(),
      eventTime,
      inputTokens,
      cacheTokens,
      outputTokens,
      sessionFingerprint: `${prefix}:${modelName.trim()}`,
      sourceRef,
    });
  }
  return out;
}

function extractUsageEventsFromNode(node, tool, fallbackTime, sourceRef, options = {}) {
  const { codexModelHint = null, sessionFingerprint = null } = options;
  if (tool === "copilot_cli") {
    return extractCopilotCliEvents(node, fallbackTime, sourceRef, sessionFingerprint);
  }
  if (tool === "copilot_vscode") {
    return extractCopilotVscodeEvents(node, fallbackTime, sourceRef);
  }
  if (tool === "codex") {
    const usage = extractCodexTokenCountUsage(node);
    if (!usage || (usage.inputTokens === 0 && usage.cacheTokens === 0 && usage.outputTokens === 0)) {
      return [];
    }
    return [
      {
        tool,
        model: extractModel(node) === "unknown" && codexModelHint ? codexModelHint : extractModel(node),
        eventTime: extractTime(node) || fallbackTime,
        inputTokens: usage.inputTokens,
        cacheTokens: usage.cacheTokens,
        outputTokens: usage.outputTokens,
        sessionFingerprint,
        sourceRef,
      },
    ];
  }

  const out = [];
  const seen = new Set();
  for (const candidate of walkJsonNodes(node)) {
    const usage = extractUsage(candidate);
    if (usage.inputTokens === 0 && usage.cacheTokens === 0 && usage.outputTokens === 0) {
      continue;
    }
    const eventTime = extractTime(candidate) || fallbackTime;
    const dedupeKey = [tool, usage.inputTokens, usage.cacheTokens, usage.outputTokens, eventTime.toISOString()].join("|");
    if (seen.has(dedupeKey)) {
      continue;
    }
    seen.add(dedupeKey);
    out.push({
      tool,
      model: extractModel(candidate),
      eventTime,
      inputTokens: usage.inputTokens,
      cacheTokens: usage.cacheTokens,
      outputTokens: usage.outputTokens,
      sessionFingerprint,
      sourceRef,
    });
  }
  return out;
}

export function readEventsFromText(text, tool, sourceRef, fallbackTime, fileSuffix, sessionFingerprintSource = null) {
  const events = [];
  let codexModelHint = null;
  const sessionFingerprint = sessionFingerprintSource ? buildSessionFingerprint(sessionFingerprintSource, tool) : null;
  try {
    if (tool === "copilot_vscode" && fileSuffix.toLowerCase() === ".jsonl") {
      return [extractCopilotVscodeEventsFromJsonlText(text, fallbackTime, sourceRef), null];
    }

    if (fileSuffix.toLowerCase() === ".jsonl") {
      let lineNumber = 0;
      for (const raw of text.split(/\r?\n/u)) {
        lineNumber += 1;
        const line = raw.trim();
        if (!line) {
          continue;
        }
        let obj;
        try {
          obj = JSON.parse(line);
        } catch {
          continue;
        }
        if (tool === "codex") {
          const turnModel = extractCodexTurnModel(obj);
          if (turnModel) {
            codexModelHint = turnModel;
          }
        }
        events.push(
          ...extractUsageEventsFromNode(obj, tool, fallbackTime, `${sourceRef}:${lineNumber}`, {
            codexModelHint,
            sessionFingerprint,
          }),
        );
      }
      return [events, null];
    }

    if (fileSuffix.toLowerCase() === ".json") {
      const obj = JSON.parse(text);
      if (tool === "codex") {
        for (const candidate of walkJsonNodes(obj)) {
          const turnModel = extractCodexTurnModel(candidate);
          if (turnModel) {
            codexModelHint = turnModel;
          }
        }
      }
      events.push(
        ...extractUsageEventsFromNode(obj, tool, fallbackTime, sourceRef, {
          codexModelHint,
          sessionFingerprint,
        }),
      );
      return [events, null];
    }

    return [[], null];
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return [[], `failed decoding ${sourceRef}: ${message}`];
  }
}

export function readEventsFromFile(filePath, tool) {
  const stat = fs.statSync(filePath);
  const fallbackTime = new Date(stat.mtimeMs);
  try {
    const text = fs.readFileSync(filePath, "utf8");
    return readEventsFromText(text, tool, filePath, fallbackTime, path.extname(filePath), filePath);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return [[], `failed reading ${filePath}: ${message}`];
  }
}
