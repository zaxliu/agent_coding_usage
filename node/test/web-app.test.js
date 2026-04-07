import test from "node:test";
import assert from "node:assert/strict";

import {
  canDismissInputRequest,
  describeInputRequest,
  inputRequestSubmissionValue,
  normalizeResultsPayload,
  credentialSubmissionMode,
  nextCredentialPromptJob,
} from "../../web/app-state.js";

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

test("describeInputRequest distinguishes confirm, ssh_password, and generic text input requests", () => {
  const confirmUi = describeInputRequest({
    kind: "confirm",
    message: "Save this temporary remote to .env?",
    choices: ["Save", "Skip"],
  });
  const passwordUi = describeInputRequest({
    kind: "ssh_password",
    message: "Enter the SSH password",
  });
  const textUi = describeInputRequest({
    kind: "ssh_host",
    message: "SSH host",
  });

  assert.equal(confirmUi.inputType, "confirm");
  assert.deepEqual(confirmUi.choices, ["Save", "Skip"]);
  assert.equal(confirmUi.submitValue, "Save");
  assert.equal(confirmUi.cancelValue, "Skip");
  assert.equal(passwordUi.inputType, "password");
  assert.deepEqual(passwordUi.choices, []);
  assert.equal(textUi.inputType, "text");
  assert.deepEqual(textUi.choices, []);
});

test("inputRequestSubmissionValue preserves confirm button values and text input values", () => {
  const confirmUi = describeInputRequest({
    kind: "confirm",
    message: "Save this temporary remote to .env?",
    choices: ["Save", "Skip"],
  });
  const textUi = describeInputRequest({
    kind: "ssh_host",
    message: "SSH host",
  });

  assert.equal(inputRequestSubmissionValue({ descriptor: confirmUi, submitterValue: "Save" }), "Save");
  assert.equal(inputRequestSubmissionValue({ descriptor: confirmUi, submitterValue: "Skip" }), "Skip");
  assert.equal(inputRequestSubmissionValue({ descriptor: textUi, fieldValue: "host-a" }), "host-a");
});

test("canDismissInputRequest only allows session password prompts to stay hidden", () => {
  assert.equal(canDismissInputRequest({ kind: "ssh_password" }), true);
  assert.equal(canDismissInputRequest({ kind: "ssh_host" }), false);
  assert.equal(canDismissInputRequest({ kind: "confirm" }), false);
});

test("nextCredentialPromptJob keeps a dismissed job hidden until another pending job appears", () => {
  const dismissedJob = {
    id: "job-1",
    status: "needs_input",
    input_request: {
      kind: "ssh_password",
      remote_alias: "SERVER_A",
      message: "Provide password",
    },
  };
  const completedJob = {
    id: "job-2",
    status: "succeeded",
    input_request: null,
  };
  const nextPendingJob = {
    id: "job-3",
    status: "needs_input",
    input_request: {
      kind: "ssh_password",
      remote_alias: "SERVER_B",
      message: "Provide password",
    },
  };

  assert.equal(nextCredentialPromptJob([dismissedJob], "")?.id, "job-1");
  assert.equal(nextCredentialPromptJob([dismissedJob], "job-1"), null);
  assert.equal(nextCredentialPromptJob([dismissedJob, completedJob], "job-1"), null);
  assert.equal(nextCredentialPromptJob([dismissedJob, completedJob, nextPendingJob], "job-1")?.id, "job-3");
  assert.equal(nextCredentialPromptJob([], "job-1"), null);
});
