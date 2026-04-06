async function getJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "content-type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  return response.json();
}

function configPayload() {
  return {
    basic: {
      ORG_USERNAME: document.querySelector("#org-username").value,
      HASH_SALT: document.querySelector("#hash-salt").value,
      TIMEZONE: document.querySelector("#timezone").value,
      LOOKBACK_DAYS: document.querySelector("#lookback-days").value,
    },
    cursor: {},
    feishu_default: {
      FEISHU_APP_TOKEN: document.querySelector("#feishu-app-token").value,
    },
    feishu_targets: [],
    remotes: [],
    raw_env: [],
  };
}

async function refreshRuntime() {
  const runtime = await getJson("/api/runtime");
  document.querySelector("#runtime").textContent = JSON.stringify(runtime, null, 2);
}

async function refreshConfig() {
  const config = await getJson("/api/config");
  document.querySelector("#org-username").value = config.basic.ORG_USERNAME || "";
  document.querySelector("#hash-salt").value = config.basic.HASH_SALT || "";
  document.querySelector("#timezone").value = config.basic.TIMEZONE || "";
  document.querySelector("#lookback-days").value = config.basic.LOOKBACK_DAYS || "";
  document.querySelector("#feishu-app-token").value = config.feishu_default.FEISHU_APP_TOKEN || "";
}

async function refreshResults() {
  const results = await getJson("/api/results/latest");
  document.querySelector("#results").textContent = JSON.stringify(results, null, 2);
}

async function refreshJobs() {
  const jobs = await getJson("/api/jobs");
  document.querySelector("#jobs").textContent = JSON.stringify(jobs, null, 2);
}

async function runAction(action) {
  if (action === "save-config") {
    await getJson("/api/config", {
      method: "PUT",
      body: JSON.stringify(configPayload()),
    });
    await refreshConfig();
    return refreshJobs();
  }

  let url = "/api/doctor";
  let payload = {};
  if (action === "collect") {
    url = "/api/collect";
  } else if (action === "sync-preview") {
    url = "/api/sync/preview";
  } else if (action === "sync") {
    url = "/api/sync";
    payload = { confirm_sync: true };
  }
  await getJson(url, { method: "POST", body: JSON.stringify(payload) });
  await refreshJobs();
  await refreshResults();
}

for (const button of document.querySelectorAll("[data-action]")) {
  button.addEventListener("click", () => runAction(button.dataset.action));
}

await Promise.all([refreshRuntime(), refreshConfig(), refreshResults(), refreshJobs()]);
