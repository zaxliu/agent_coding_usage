import { REQUIRED_FEISHU_FIELDS } from "./feishu-schema.js";

const DEFAULT_TIMEOUT_MS = 20_000;

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function maybeJson(text) {
  try {
    const payload = JSON.parse(text);
    return payload && typeof payload === "object" ? payload : null;
  } catch {
    return null;
  }
}

function formatFeishuApiError(payload, context) {
  const parts = [context, `code=${payload.code}`];
  for (const key of ["msg", "message"]) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) {
      parts.push(`${key}=${value.trim()}`);
    }
  }
  if (payload.error && typeof payload.error === "object") {
    for (const key of ["message", "msg"]) {
      const value = payload.error[key];
      if (typeof value === "string" && value.trim()) {
        parts.push(`error.${key}=${value.trim()}`);
        break;
      }
    }
  }

  let text = parts.join(" | ");
  const lowered = text.toLowerCase();
  if (
    ["permission", "forbidden", "无权限", "没有权限", "access denied", "auth scope"].some((token) =>
      lowered.includes(token),
    )
  ) {
    text +=
      " | hint=飞书开放平台的应用接口权限不能替代表格协作权限；请确认该应用或其运行身份对目标多维表格/数据表仍有可编辑权限。";
  }
  return text;
}

async function requestJson(method, url, { token, body, params, timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  const requestUrl = new URL(url);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== "") {
        requestUrl.searchParams.set(key, String(value));
      }
    }
  }

  let backoff = 800;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(requestUrl, {
        method,
        headers: {
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(body ? { "Content-Type": "application/json" } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });
      const text = await response.text();
      const payload = maybeJson(text);

      if ([429, 500, 502, 503, 504].includes(response.status) && attempt < 3) {
        await delay(backoff);
        backoff *= 2;
        continue;
      }
      if (response.status >= 400) {
        if (payload) {
          throw new Error(formatFeishuApiError(payload, `${method} ${requestUrl}`));
        }
        throw new Error(`${method} ${requestUrl} failed with http ${response.status}: ${text}`);
      }
      if (!payload) {
        throw new Error(`feishu api response is not json: ${method} ${requestUrl}`);
      }
      if (payload.code !== 0) {
        throw new Error(formatFeishuApiError(payload, `${method} ${requestUrl}`));
      }
      return payload;
    } finally {
      clearTimeout(timeout);
    }
  }
  throw new Error(`feishu api retry exhausted: ${method} ${url}`);
}

function normalizeDatetimeValue(value) {
  if (typeof value === "number") {
    return value > 10_000_000_000 ? Math.trunc(value) : Math.trunc(value * 1000);
  }
  if (typeof value === "string") {
    const candidate = value.trim();
    if (!candidate) {
      return value;
    }
    const parsed = new Date(candidate);
    if (!Number.isNaN(parsed.getTime())) {
      return parsed.getTime();
    }
  }
  return value;
}

export async function fetchTenantAccessToken({ appId, appSecret, timeoutMs = DEFAULT_TIMEOUT_MS }) {
  const payload = await requestJson("POST", "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal", {
    body: { app_id: appId, app_secret: appSecret },
    timeoutMs,
  });
  const token = payload.tenant_access_token;
  if (typeof token !== "string" || !token.trim()) {
    throw new Error("feishu auth token missing");
  }
  return token;
}

export async function fetchFirstTableId({ appToken, botToken, timeoutMs = DEFAULT_TIMEOUT_MS }) {
  const payload = await requestJson(
    "GET",
    `https://open.feishu.cn/open-apis/bitable/v1/apps/${appToken}/tables`,
    {
      token: botToken,
      params: { page_size: 1 },
      timeoutMs,
    },
  );
  const tableId = payload.data?.items?.[0]?.table_id;
  if (typeof tableId !== "string" || !tableId.trim()) {
    throw new Error("feishu table id missing");
  }
  return tableId;
}

export async function fetchBitableFieldTypeMap({ appToken, tableId, botToken, timeoutMs = DEFAULT_TIMEOUT_MS }) {
  const client = new FeishuBitableClient({ appToken, tableId, botToken, timeoutMs });
  return client.fetchFieldTypeMap();
}

export class FeishuBitableClient {
  constructor({ appToken, tableId, botToken, timeoutMs = DEFAULT_TIMEOUT_MS }) {
    this.appToken = appToken;
    this.tableId = tableId;
    this.botToken = botToken;
    this.timeoutMs = timeoutMs;
    this.baseUrl = `https://open.feishu.cn/open-apis/bitable/v1/apps/${appToken}/tables/${tableId}/records`;
    this.batchSize = 100;
  }

  async fetchExistingRowKeys() {
    const rowKeyToRecordId = new Map();
    let pageToken = null;
    while (true) {
      const payload = await requestJson("GET", this.baseUrl, {
        token: this.botToken,
        params: { page_size: 500, page_token: pageToken },
        timeoutMs: this.timeoutMs,
      });
      const data = payload.data || {};
      for (const item of data.items || []) {
        const rowKey = item.fields?.row_key;
        const recordId = item.record_id;
        if (typeof rowKey === "string" && typeof recordId === "string") {
          rowKeyToRecordId.set(rowKey, recordId);
        }
      }
      if (!data.has_more || !data.page_token) {
        break;
      }
      pageToken = data.page_token;
    }
    return rowKeyToRecordId;
  }

  async fetchFieldTypeMap() {
    const out = new Map();
    let pageToken = null;
    const url = `https://open.feishu.cn/open-apis/bitable/v1/apps/${this.appToken}/tables/${this.tableId}/fields`;
    while (true) {
      const payload = await requestJson("GET", url, {
        token: this.botToken,
        params: { page_size: 500, page_token: pageToken },
        timeoutMs: this.timeoutMs,
      });
      const data = payload.data || {};
      for (const item of data.items || []) {
        if (typeof item.field_name === "string" && typeof item.type === "number") {
          out.set(item.field_name, item.type);
        }
      }
      if (!data.has_more || !data.page_token) {
        break;
      }
      pageToken = data.page_token;
    }
    return out;
  }

  async createField({ fieldName, type }) {
    const url = `https://open.feishu.cn/open-apis/bitable/v1/apps/${this.appToken}/tables/${this.tableId}/fields`;
    await requestJson("POST", url, {
      token: this.botToken,
      body: { field_name: fieldName, type },
      timeoutMs: this.timeoutMs,
    });
  }

  filterFieldsForTable(fields, fieldTypeMap) {
    const filtered = {};
    const missing = [];
    for (const [key, value] of Object.entries(fields)) {
      if (fieldTypeMap.has(key)) {
        filtered[key] = value;
      } else {
        missing.push(key);
      }
    }
    return { filtered, missing };
  }

  normalizeFieldsForTable(fields, fieldTypeMap) {
    const out = {};
    for (const [key, value] of Object.entries(fields)) {
      out[key] = fieldTypeMap.get(key) === 5 ? normalizeDatetimeValue(value) : value;
    }
    return out;
  }

  chunk(items) {
    const out = [];
    for (let index = 0; index < items.length; index += this.batchSize) {
      out.push(items.slice(index, index + this.batchSize));
    }
    return out;
  }

  async upsert(rows, toFeishuFields) {
    const existing = await this.fetchExistingRowKeys();
    const fieldTypeMap = await this.fetchFieldTypeMap();
    let created = 0;
    let updated = 0;
    let failed = 0;
    const error_samples = [];
    const warning_samples = [];
    const warnedMissing = new Set();
    const createRecords = [];
    const updateRecords = [];

    for (const row of rows) {
      const rawFields = toFeishuFields(row);
      const { filtered, missing } = this.filterFieldsForTable(rawFields, fieldTypeMap);
      const fields = this.normalizeFieldsForTable(filtered, fieldTypeMap);
      for (const fieldName of missing) {
        if (!warnedMissing.has(fieldName) && warning_samples.length < 5) {
          warning_samples.push(`飞书表缺少字段，已跳过：${fieldName}`);
        }
        warnedMissing.add(fieldName);
      }
      const recordId = existing.get(row.row_key);
      if (recordId) {
        updateRecords.push({ record_id: recordId, fields });
      } else {
        createRecords.push({ fields });
      }
    }

    for (const batch of this.chunk(createRecords)) {
      try {
        await requestJson("POST", `${this.baseUrl}/batch_create`, {
          token: this.botToken,
          body: { records: batch.map((item) => ({ fields: item.fields })) },
          timeoutMs: this.timeoutMs,
        });
        created += batch.length;
      } catch (error) {
        failed += batch.length;
        if (error_samples.length < 5) {
          error_samples.push(`批量创建失败 batch=${batch.length}: ${error.message}`);
        }
      }
    }

    for (const batch of this.chunk(updateRecords)) {
      try {
        await requestJson("POST", `${this.baseUrl}/batch_update`, {
          token: this.botToken,
          body: { records: batch.map((item) => ({ record_id: item.record_id, fields: item.fields })) },
          timeoutMs: this.timeoutMs,
        });
        updated += batch.length;
      } catch (error) {
        failed += batch.length;
        if (error_samples.length < 5) {
          error_samples.push(`批量更新失败 batch=${batch.length}: ${error.message}`);
        }
      }
    }

    return { created, updated, failed, error_samples, warning_samples };
  }
}

export async function createMissingFeishuFields(
  client,
  { specs = REQUIRED_FEISHU_FIELDS, dryRun = false } = {},
) {
  const fieldTypeMap = await client.fetchFieldTypeMap();
  const created = [];
  for (const spec of specs) {
    if (fieldTypeMap.has(spec.name)) {
      continue;
    }
    if (dryRun) {
      created.push(spec.name);
      continue;
    }
    await client.createField({ fieldName: spec.name, type: spec.feishuType() });
    created.push(spec.name);
  }
  return created;
}
