import test from "node:test";
import assert from "node:assert/strict";

import { normalizeResultsPayload, credentialSubmissionMode, nextCredentialPromptJob } from "../../web/app-state.js";

test("normalizeResultsPayload reads dashboard summary totals and names from current backend payload", () => {
  const normalized = normalizeResultsPayload({
    summary: {
      totals: {
        rows: 2,
        input_tokens_sum: 15,
        cache_tokens_sum: 3,
        output_tokens_sum: 7,
        total_tokens: 25,
      },
      active_days: 2,
      top_tool: { name: "codex", total_tokens: 15 },
      top_model: { name: "gpt-5", total_tokens: 15 },
      generated_at: "2026-04-07T12:00:00Z",
    },
    timeseries: [
      {
        date_local: "2026-04-06",
        input_tokens_sum: 10,
        cache_tokens_sum: 2,
        output_tokens_sum: 3,
      },
    ],
    breakdowns: {
      tools: [{ name: "codex", total_tokens: 15 }],
      models: [{ name: "gpt-5", total_tokens: 15 }],
    },
    table_rows: [
      {
        date_local: "2026-04-06",
        tool: "codex",
        model: "gpt-5",
        input_tokens_sum: 10,
        cache_tokens_sum: 2,
        output_tokens_sum: 3,
      },
    ],
  });

  assert.equal(normalized.summary.total_tokens, 25);
  assert.equal(normalized.summary.active_days, 2);
  assert.equal(normalized.summary.top_tool, "codex");
  assert.equal(normalized.summary.top_model, "gpt-5");
  assert.equal(normalized.summary.generated_at, "2026-04-07T12:00:00Z");
});

test("credentialSubmissionMode treats cancel as dismiss instead of submit", () => {
  assert.equal(credentialSubmissionMode({ submitterValue: "submit" }), "submit");
  assert.equal(credentialSubmissionMode({ submitterValue: "cancel" }), "cancel");
  assert.equal(credentialSubmissionMode({ submitterValue: "" }), "submit");
});

test("nextCredentialPromptJob does not reopen a dismissed job until the pending job changes", () => {
  const firstJob = {
    id: "job-1",
    status: "needs_input",
    input_request: {
      kind: "ssh_password",
      remote_alias: "SERVER_A",
      message: "Provide password",
    },
  };
  const secondJob = {
    id: "job-2",
    status: "needs_input",
    input_request: {
      kind: "ssh_password",
      remote_alias: "SERVER_B",
      message: "Provide password",
    },
  };

  assert.equal(nextCredentialPromptJob([firstJob], "")?.id, "job-1");
  assert.equal(nextCredentialPromptJob([firstJob], "job-1"), null);
  assert.equal(nextCredentialPromptJob([firstJob, secondJob], "job-1")?.id, "job-2");
  assert.equal(nextCredentialPromptJob([], "job-1"), null);
});
