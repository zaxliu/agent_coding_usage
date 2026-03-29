import { buildRowKey, resolveIdentity } from "./identity.js";
import { toDate } from "./models.js";
import { formatDateLocal, formatIsoInTimeZone } from "./time.js";

function clampTokens(value) {
  return Math.max(0, Number(value || 0));
}

export function aggregateEvents(events, { userHash, timeZone, now = new Date() }) {
  const buckets = new Map();

  for (const rawEvent of events) {
    const eventTime = toDate(rawEvent.eventTime);
    const sourceHostHash = rawEvent.sourceHostHash || "";
    const localDate = formatDateLocal(eventTime, timeZone);
    const identity = resolveIdentity(rawEvent.model, rawEvent.sessionFingerprint);
    const bucketKey = `${localDate}\u0000${rawEvent.tool}\u0000${sourceHostHash}\u0000${identity}`;
    const existing =
      buckets.get(bucketKey) ||
      {
        date_local: localDate,
        tool: rawEvent.tool,
        source_host_hash: sourceHostHash,
        input_tokens_sum: 0,
        cache_tokens_sum: 0,
        output_tokens_sum: 0,
        model: "unknown",
        model_time: null,
        session_fingerprint: null,
      };

    existing.input_tokens_sum += clampTokens(rawEvent.inputTokens);
    existing.cache_tokens_sum += clampTokens(rawEvent.cacheTokens);
    existing.output_tokens_sum += clampTokens(rawEvent.outputTokens);
    existing.source_host_hash = sourceHostHash;

    if (typeof rawEvent.sessionFingerprint === "string" && rawEvent.sessionFingerprint.trim()) {
      existing.session_fingerprint = rawEvent.sessionFingerprint.trim();
    }

    if (rawEvent.model !== "unknown" && (!existing.model_time || eventTime >= existing.model_time)) {
      existing.model = rawEvent.model;
      existing.model_time = eventTime;
    }

    buckets.set(bucketKey, existing);
  }

  const updatedAt = formatIsoInTimeZone(toDate(now), timeZone);
  return [...buckets.values()]
    .sort((left, right) => {
      return (
        left.date_local.localeCompare(right.date_local) ||
        left.tool.localeCompare(right.tool) ||
        left.source_host_hash.localeCompare(right.source_host_hash) ||
        resolveIdentity(left.model, left.session_fingerprint).localeCompare(
          resolveIdentity(right.model, right.session_fingerprint),
        )
      );
    })
    .map((bucket) => ({
      date_local: bucket.date_local,
      user_hash: userHash,
      source_host_hash: bucket.source_host_hash,
      tool: bucket.tool,
      model: bucket.model,
      input_tokens_sum: bucket.input_tokens_sum,
      cache_tokens_sum: bucket.cache_tokens_sum,
      output_tokens_sum: bucket.output_tokens_sum,
      row_key: buildRowKey({
        userHash,
        sourceHostHash: bucket.source_host_hash,
        dateLocal: bucket.date_local,
        tool: bucket.tool,
        model: bucket.model,
        sessionFingerprint: bucket.session_fingerprint,
      }),
      updated_at: updatedAt,
    }));
}
