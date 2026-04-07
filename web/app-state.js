function toNumber(value) {
  const parsed = Number(value || 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function totalFromParts(input, cache, output) {
  return toNumber(input) + toNumber(cache) + toNumber(output);
}

export function normalizeResultsPayload(results) {
  if (results?.summary && results?.timeseries && results?.breakdowns && results?.table_rows) {
    const summary = results.summary || {};
    const totals = summary.totals || {};
    const breakdowns = results.breakdowns || {};
    return {
      summary: {
        total_tokens:
          toNumber(totals.total_tokens) ||
          totalFromParts(totals.input_tokens_sum, totals.cache_tokens_sum, totals.output_tokens_sum),
        active_days: toNumber(summary.active_days),
        top_tool: summary.top_tool?.name || (breakdowns.tools || breakdowns.tool || [])[0]?.name || "-",
        top_model: summary.top_model?.name || (breakdowns.models || breakdowns.model || [])[0]?.name || "-",
        generated_at: summary.generated_at || results.generated_at || null,
      },
      timeseries: (results.timeseries || []).map((item) => ({
        date: item.date || item.date_local,
        input: toNumber(item.input ?? item.input_tokens_sum),
        cache: toNumber(item.cache ?? item.cache_tokens_sum),
        output: toNumber(item.output ?? item.output_tokens_sum),
      })),
      breakdowns: {
        tools: (breakdowns.tools || breakdowns.tool || []).map((item) => ({
          label: item.label || item.name,
          total: toNumber(item.total ?? item.total_tokens) || totalFromParts(item.input_tokens_sum, item.cache_tokens_sum, item.output_tokens_sum),
        })),
        models: (breakdowns.models || breakdowns.model || []).map((item) => ({
          label: item.label || item.name,
          total: toNumber(item.total ?? item.total_tokens) || totalFromParts(item.input_tokens_sum, item.cache_tokens_sum, item.output_tokens_sum),
        })),
      },
      table_rows: (results.table_rows || []).map((row) => ({
        date: row.date || row.date_local,
        tool: row.tool,
        model: row.model,
        input: toNumber(row.input ?? row.input_tokens_sum),
        cache: toNumber(row.cache ?? row.cache_tokens_sum),
        output: toNumber(row.output ?? row.output_tokens_sum),
      })),
      warnings: results.warnings || [],
    };
  }

  const rows = results?.rows || [];
  const summary = {
    total_tokens: rows.reduce(
      (sum, row) => sum + totalFromParts(row.input_tokens_sum || row.input, row.cache_tokens_sum || row.cache, row.output_tokens_sum || row.output),
      0,
    ),
    active_days: new Set(rows.map((row) => row.date_local)).size,
    top_tool: topBy(rows, "tool"),
    top_model: topBy(rows, "model"),
    generated_at: results?.generated_at || null,
  };
  return {
    summary,
    timeseries: rows.map((row) => ({
      date: row.date_local,
      input: toNumber(row.input_tokens_sum || row.input),
      cache: toNumber(row.cache_tokens_sum || row.cache),
      output: toNumber(row.output_tokens_sum || row.output),
    })),
    breakdowns: {
      tools: groupBy(rows, "tool"),
      models: groupBy(rows, "model"),
    },
    table_rows: rows.map((row) => ({
      date: row.date_local,
      tool: row.tool,
      model: row.model,
      input: toNumber(row.input_tokens_sum || row.input),
      cache: toNumber(row.cache_tokens_sum || row.cache),
      output: toNumber(row.output_tokens_sum || row.output),
    })),
    warnings: results?.warnings || [],
  };
}

function topBy(rows, key) {
  const grouped = new Map();
  for (const row of rows) {
    const label = row[key] || "Unknown";
    grouped.set(
      label,
      (grouped.get(label) || 0) + totalFromParts(row.input_tokens_sum || row.input, row.cache_tokens_sum || row.cache, row.output_tokens_sum || row.output),
    );
  }
  return [...grouped.entries()].sort((left, right) => right[1] - left[1])[0]?.[0] || "-";
}

function groupBy(rows, key) {
  const grouped = new Map();
  for (const row of rows) {
    const label = row[key] || "Unknown";
    grouped.set(
      label,
      (grouped.get(label) || 0) + totalFromParts(row.input_tokens_sum || row.input, row.cache_tokens_sum || row.cache, row.output_tokens_sum || row.output),
    );
  }
  return [...grouped.entries()]
    .sort((left, right) => right[1] - left[1])
    .map(([label, total]) => ({ label, total }));
}

export function credentialSubmissionMode({ submitterValue = "" } = {}) {
  return submitterValue === "cancel" ? "cancel" : "submit";
}

function normalizeChoices(choices = []) {
  return Array.isArray(choices) ? choices.map((choice) => String(choice || "").trim()).filter(Boolean) : [];
}

function titleForInputKind(kind) {
  if (kind === "confirm") {
    return "Confirmation Required";
  }
  if (kind === "ssh_password") {
    return "SSH Password Required";
  }
  if (kind === "ssh_host") {
    return "SSH Host Required";
  }
  if (kind === "ssh_user") {
    return "SSH User Required";
  }
  if (kind === "ssh_port") {
    return "SSH Port Required";
  }
  if (kind === "use_sshpass") {
    return "Use sshpass?";
  }
  return "Input Required";
}

function fieldLabelForInputKind(kind) {
  if (kind === "ssh_password") {
    return "Password";
  }
  if (kind === "ssh_port") {
    return "Port";
  }
  if (kind === "confirm") {
    return "";
  }
  return "Value";
}

function placeholderForInputKind(kind) {
  if (kind === "ssh_password") {
    return "Enter password";
  }
  if (kind === "ssh_port") {
    return "22";
  }
  if (kind === "confirm") {
    return "";
  }
  return "Enter value";
}

export function describeInputRequest(request = {}) {
  const kind = String(request.kind || "").trim();
  const choices = normalizeChoices(request.choices);
  if (kind === "confirm") {
    const submitChoice = choices[0] || "yes";
    const cancelChoice = choices[1] || "no";
    return {
      kind,
      inputType: "confirm",
      title: titleForInputKind(kind),
      message: String(request.message || "").trim(),
      choices: choices.length ? choices : ["yes", "no"],
      fieldLabel: "",
      placeholder: "",
      submitLabel: submitChoice,
      submitValue: submitChoice,
      cancelLabel: cancelChoice,
      cancelValue: cancelChoice,
    };
  }
  if (kind === "ssh_password") {
    return {
      kind,
      inputType: "password",
      title: titleForInputKind(kind),
      message: String(request.message || "").trim(),
      choices: [],
      fieldLabel: fieldLabelForInputKind(kind),
      placeholder: placeholderForInputKind(kind),
      submitLabel: "Continue",
      submitValue: "submit",
      cancelLabel: "Cancel",
      cancelValue: "cancel",
    };
  }
  return {
    kind,
    inputType: "text",
    title: titleForInputKind(kind),
    message: String(request.message || "").trim(),
    choices: [],
    fieldLabel: fieldLabelForInputKind(kind),
    placeholder: placeholderForInputKind(kind),
    submitLabel: "Continue",
    submitValue: "submit",
    cancelLabel: "Cancel",
    cancelValue: "cancel",
  };
}

export function inputRequestSubmissionValue({ descriptor, submitterValue = "", fieldValue = "" } = {}) {
  if ((descriptor || {}).inputType === "confirm") {
    return String(submitterValue || descriptor?.submitValue || "");
  }
  return String(fieldValue || "");
}

export function canDismissInputRequest(request = {}) {
  return String(request.kind || "").trim() === "ssh_password";
}

export function nextCredentialPromptJob(jobs = [], dismissedJobId = "") {
  const pending = jobs.find(
    (job) => job.status === "needs_input" && job.input_request && (!dismissedJobId || job.id !== dismissedJobId),
  );
  if (!pending) {
    return null;
  }
  return pending;
}
