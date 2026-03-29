import path from "node:path";
import process from "node:process";
import { execFileSync } from "node:child_process";

import { repoRoot } from "./env.js";

const bridgePath = path.join(repoRoot, "node", "bridge", "collector_bridge.py");

function runBridge(command, lookbackDays, envOverrides = {}) {
  const output = execFileSync(
    "python3",
    [bridgePath, command, "--lookback-days", String(Math.max(1, lookbackDays))],
    {
      cwd: repoRoot,
      env: { ...process.env, ...envOverrides },
      encoding: "utf8",
      maxBuffer: 50 * 1024 * 1024,
    },
  );
  return JSON.parse(output);
}

export function collectEventsViaPython(lookbackDays, envOverrides) {
  return runBridge("collect", lookbackDays, envOverrides);
}

export function doctorViaPython(lookbackDays, envOverrides) {
  return runBridge("doctor", lookbackDays, envOverrides);
}
