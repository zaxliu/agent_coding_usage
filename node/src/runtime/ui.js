import readline from "node:readline/promises";
import process from "node:process";

const color = {
  reset: "\x1b[0m",
  dim: "\x1b[2m",
  bold: "\x1b[1m",
  cyan: "\x1b[36m",
  blue: "\x1b[34m",
  green: "\x1b[32m",
  yellow: "\x1b[33m",
  red: "\x1b[31m",
  gray: "\x1b[90m",
};

function enabled(stream = process.stdout) {
  return Boolean(stream?.isTTY);
}

function paint(value, tone) {
  if (!enabled()) {
    return value;
  }
  return `${color[tone] || ""}${value}${color.reset}`;
}

export function banner(title, subtitle = "") {
  const head = paint(title, "bold");
  const sub = subtitle ? ` ${paint(subtitle, "gray")}` : "";
  return `${head}${sub}`;
}

export function section(title) {
  return `\n${paint(`◆ ${title}`, "cyan")}`;
}

export function info(message) {
  return `${paint("info", "blue")}: ${message}`;
}

export function warn(message) {
  return `${paint("warn", "yellow")}: ${message}`;
}

export function ok(message) {
  return `${paint("ok", "green")}: ${message}`;
}

export function fail(message) {
  return `${paint("fail", "red")}: ${message}`;
}

export function statusPill(okValue) {
  return okValue ? paint("OK", "green") : paint("WARN", "yellow");
}

export function formatRemoteLine(config, { selected = false, index = 0 } = {}) {
  const mark = selected ? paint("●", "green") : paint("○", "gray");
  const alias = paint(config.alias.toLowerCase(), selected ? "bold" : "reset");
  const target = paint(`${config.ssh_user}@${config.ssh_host}:${config.ssh_port}`, "gray");
  return `  ${mark} ${String(index).padStart(2, " ")}  ${alias}  ${target}`;
}

export async function promptLine(promptText, { stdin = process.stdin, stdout = process.stdout } = {}) {
  const rl = readline.createInterface({ input: stdin, output: stdout });
  try {
    const answer = await rl.question(promptText);
    return answer.trimEnd();
  } finally {
    rl.close();
  }
}

export function isInteractive(stdin = process.stdin, stdout = process.stdout) {
  return Boolean(stdin?.isTTY && stdout?.isTTY);
}
