import path from "node:path";
import process from "node:process";
import { execFileSync } from "node:child_process";

import { repoRoot } from "./env.js";

const bridgePath = path.join(repoRoot, "node", "bridge", "collector_bridge.py");

function runBridge(command, lookbackDays) {
  const output = execFileSync(
    "python3",
    [bridgePath, command, "--lookback-days", String(Math.max(1, lookbackDays))],
    {
      cwd: repoRoot,
      env: process.env,
      encoding: "utf8",
      maxBuffer: 50 * 1024 * 1024,
    },
  );
  return JSON.parse(output);
}

export function collectEventsViaPython(lookbackDays) {
  return runBridge("collect", lookbackDays);
}

export function doctorViaPython(lookbackDays) {
  return runBridge("doctor", lookbackDays);
}
