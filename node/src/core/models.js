/**
 * @typedef {Object} UsageEvent
 * @property {string} tool
 * @property {string} model
 * @property {Date|string|number} eventTime
 * @property {number} inputTokens
 * @property {number} cacheTokens
 * @property {number} outputTokens
 * @property {string|null|undefined} [sessionFingerprint]
 * @property {string|null|undefined} [sourceRef]
 * @property {string} [sourceHostHash]
 */

/**
 * @typedef {Object} AggregateRecord
 * @property {string} dateLocal
 * @property {string} userHash
 * @property {string} sourceHostHash
 * @property {string} tool
 * @property {string} model
 * @property {number} inputTokensSum
 * @property {number} cacheTokensSum
 * @property {number} outputTokensSum
 * @property {string} rowKey
 * @property {string} updatedAt
 */

export function toDate(value) {
  return value instanceof Date ? value : new Date(value);
}
