export const UPLOAD_FIELDS = new Set([
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
]);

export function toFeishuFields(row) {
  return Object.fromEntries(Object.entries(row).filter(([key]) => UPLOAD_FIELDS.has(key)));
}
