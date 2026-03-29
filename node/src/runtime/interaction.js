import process from "node:process";

import { appendRemoteToEnv } from "./remotes.js";
import { banner, formatRemoteLine, info, isInteractive, promptLine, section, warn } from "./ui.js";

export async function confirmSaveTemporaryRemote({ uiMode = "auto", stdin = process.stdin, stdout = process.stdout }) {
  if (!isInteractive(stdin, stdout) || uiMode === "none") {
    return false;
  }
  const answer = (await promptLine("是否将这个临时远端保存到 .env？[y/N]: ", { stdin, stdout })).trim().toLowerCase();
  return ["y", "yes", "是", "确认"].includes(answer);
}

export async function selectRemotes(
  configs,
  defaultAliases,
  {
    uiMode = "auto",
    stdin = process.stdin,
    stdout = process.stdout,
    remoteValidator,
    buildTemporaryRemote,
  },
) {
  if (uiMode === "none" || !isInteractive(stdin, stdout)) {
    return { selectedAliases: [...defaultAliases], temporaryRemotes: [], modeUsed: "none" };
  }

  if (!configs.length) {
    stdout.write(`${banner("Remote Selection", "local only by default")}\n`);
    stdout.write(`${info("当前 .env 中还没有配置远端。回车仅统计本机，输入 + 新增临时远端。")}\n`);
    const answer = (await promptLine("> ", { stdin, stdout })).trim();
    if (answer !== "+") {
      return { selectedAliases: [], temporaryRemotes: [], modeUsed: "cli" };
    }
    const temp = await promptTemporaryRemote({ stdin, stdout, remoteValidator, buildTemporaryRemote });
    return { selectedAliases: [], temporaryRemotes: temp ? [temp] : [], modeUsed: "cli" };
  }

  while (true) {
    stdout.write(`${banner("Remote Selection", "enter to accept defaults")}\n`);
    stdout.write(`${section("Configured Remotes")}\n`);
    configs.forEach((config, index) => {
      stdout.write(`${formatRemoteLine(config, { selected: defaultAliases.includes(config.alias), index: index + 1 })}\n`);
    });
    stdout.write(`${info("输入说明: 回车=默认, all=全选, none=仅本机, 1,2 或 ALIAS 选择, +=新增临时远端")}\n`);
    const defaultLabel = defaultAliases.length ? defaultAliases.map((item) => item.toLowerCase()).join(", ") : "仅本机";
    const answer = (await promptLine(`本次远端选择 [${defaultLabel}]: `, { stdin, stdout })).trim();
    if (!answer) {
      return { selectedAliases: [...defaultAliases], temporaryRemotes: [], modeUsed: "cli" };
    }
    if (answer.toLowerCase() === "all") {
      return { selectedAliases: configs.map((item) => item.alias), temporaryRemotes: [], modeUsed: "cli" };
    }
    if (answer.toLowerCase() === "none") {
      return { selectedAliases: [], temporaryRemotes: [], modeUsed: "cli" };
    }
    if (answer === "+") {
      const temp = await promptTemporaryRemote({ stdin, stdout, remoteValidator, buildTemporaryRemote });
      return { selectedAliases: [...defaultAliases], temporaryRemotes: temp ? [temp] : [], modeUsed: "cli" };
    }

    const aliasMap = new Map(configs.map((item) => [item.alias, item.alias]));
    const resolved = [];
    let valid = true;
    for (const token of answer.split(",").map((item) => item.trim()).filter(Boolean)) {
      if (/^\d+$/u.test(token)) {
        const index = Number.parseInt(token, 10);
        if (index >= 1 && index <= configs.length) {
          resolved.push(configs[index - 1].alias);
          continue;
        }
      }
      const alias = token.toUpperCase();
      if (aliasMap.has(alias)) {
        resolved.push(alias);
        continue;
      }
      valid = false;
      break;
    }
    if (valid) {
      return { selectedAliases: resolved, temporaryRemotes: [], modeUsed: "cli" };
    }
    stdout.write(`${warn("输入无效，请重试。")}\n`);
  }
}

async function promptTemporaryRemote({ stdin, stdout, remoteValidator, buildTemporaryRemote }) {
  while (true) {
    stdout.write(`${section("Temporary Remote")}\n`);
    const host = (await promptLine("SSH 主机: ", { stdin, stdout })).trim();
    if (!host) {
      return null;
    }
    const user = (await promptLine("SSH 用户: ", { stdin, stdout })).trim();
    if (!user) {
      return null;
    }
    const portRaw = (await promptLine("SSH 端口 [22]: ", { stdin, stdout })).trim() || "22";
    const port = Number.parseInt(portRaw, 10);
    if (!Number.isFinite(port) || port <= 0) {
      stdout.write(`${warn("端口格式不正确，请重新输入。")}\n`);
      continue;
    }

    const config = buildTemporaryRemote(host, user, port);
    stdout.write(`${info("正在检查 SSH 连通性...")}\n`);
    const [okValue, message] = await Promise.resolve(remoteValidator(config));
    if (okValue) {
      stdout.write(`${info(`SSH 检查通过: ${message}`)}\n`);
      return config;
    }
    stdout.write(`${warn(`SSH 检查失败: ${message}`)}\n`);
    const retry = (await promptLine("输入 r 重新填写，其他任意输入取消: ", { stdin, stdout })).trim().toLowerCase();
    if (retry !== "r") {
      return null;
    }
  }
}

export function persistTemporaryRemote(config, existingAliases, filePath) {
  return appendRemoteToEnv(config, existingAliases, filePath);
}
