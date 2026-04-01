export class FeishuFieldSpec {
  constructor(name, fieldType, { warnOnlyTypeMismatch = true } = {}) {
    this.name = name;
    this.fieldType = fieldType;
    this.warnOnlyTypeMismatch = warnOnlyTypeMismatch;
  }

  feishuType() {
    const mapping = {
      text: 1,
      number: 2,
      datetime: 5,
    };
    if (!(this.fieldType in mapping)) {
      throw new Error(`unknown feishu fieldType: ${this.fieldType}`);
    }
    return mapping[this.fieldType];
  }
}

export const REQUIRED_FEISHU_FIELDS = [
  new FeishuFieldSpec("date_local", "datetime"),
  new FeishuFieldSpec("user_hash", "text"),
  new FeishuFieldSpec("source_host_hash", "text"),
  new FeishuFieldSpec("tool", "text"),
  new FeishuFieldSpec("model", "text"),
  new FeishuFieldSpec("input_tokens_sum", "number"),
  new FeishuFieldSpec("cache_tokens_sum", "number"),
  new FeishuFieldSpec("output_tokens_sum", "number"),
  new FeishuFieldSpec("row_key", "text"),
  new FeishuFieldSpec("updated_at", "datetime"),
];

export function fieldNames(fields) {
  return fields.map((field) => field.name);
}

export function feishuSchemaWarnings(fieldTypeMap, specs = REQUIRED_FEISHU_FIELDS) {
  const warnings = [];
  for (const spec of specs) {
    if (!fieldTypeMap.has(spec.name)) {
      warnings.push(`飞书表缺少字段：${spec.name}`);
      continue;
    }
    if (fieldTypeMap.get(spec.name) !== spec.feishuType()) {
      warnings.push(`飞书字段类型不匹配：${spec.name}`);
    }
  }
  return warnings;
}
