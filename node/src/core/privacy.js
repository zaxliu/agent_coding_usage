import { REQUIRED_FEISHU_FIELDS, fieldNames } from "../runtime/feishu-schema.js";

export const UPLOAD_FIELDS = new Set(fieldNames(REQUIRED_FEISHU_FIELDS));

export function toFeishuFields(row) {
  return Object.fromEntries(Object.entries(row).filter(([key]) => UPLOAD_FIELDS.has(key)));
}
