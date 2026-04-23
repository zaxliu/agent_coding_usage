import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const testDir = path.dirname(fileURLToPath(import.meta.url));
const nodeRoot = path.resolve(testDir, "..");
const packageJson = JSON.parse(fs.readFileSync(path.join(nodeRoot, "package.json"), "utf8"));

test("package metadata is ready for npm publish", () => {
  assert.equal(packageJson.private, undefined);
  assert.equal(packageJson.name, "@llm-usage-horizon/llm-usage-node");
  assert.equal(packageJson.version, "0.1.4");
  assert.equal(packageJson.license, "MIT");
  assert.equal(packageJson.author, "Lewis");
  assert.deepEqual(packageJson.keywords, ["llm", "usage", "cli", "feishu", "codex", "cursor"]);
  assert.equal(packageJson.homepage, "https://github.com/zaxliu/agent_coding_usage/tree/main/node");
  assert.deepEqual(packageJson.publishConfig, { access: "public" });
  assert.equal(packageJson.repository.type, "git");
  assert.equal(packageJson.repository.url, "git+https://github.com/zaxliu/agent_coding_usage.git");
  assert.equal(packageJson.repository.directory, "node");
  assert.equal(packageJson.bugs.url, "https://github.com/zaxliu/agent_coding_usage/issues");
  assert.equal(packageJson.bin, "./bin/llm-usage-node.js");
});

test("package includes a pack validation script and a narrow published file list", () => {
  assert.equal(packageJson.scripts["pack:check"], "env NPM_CONFIG_CACHE=/tmp/llm-usage-npm-cache npm pack --dry-run");
  assert.equal("bundle" in packageJson.scripts, false);
  assert.deepEqual(packageJson.files, [
    "bin",
    "resources",
    "src/cli",
    "src/collectors",
    "src/core",
    "src/runtime/cursor-login.js",
    "src/runtime/env.js",
    "src/runtime/feishu.js",
    "src/runtime/offline-bundle.js",
    "src/runtime/remotes.js",
    "src/runtime/reporting.js",
    "src/runtime/ui.js",
    "README.md",
    "LICENSE",
  ]);
  assert.equal(packageJson.files.includes("src/runtime/interaction.js"), false);
  assert.equal(packageJson.files.includes("src/runtime/state.js"), false);
});

test("package readme and license files exist", () => {
  assert.equal(fs.existsSync(path.join(nodeRoot, "README.md")), true);
  assert.equal(fs.existsSync(path.join(nodeRoot, "LICENSE")), true);
});
