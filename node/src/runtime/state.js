import fs from "node:fs";
import path from "node:path";

export function loadSelectedRemoteAliases(filePath) {
  if (!fs.existsSync(filePath)) {
    return [];
  }
  try {
    const payload = JSON.parse(fs.readFileSync(filePath, "utf8"));
    if (!Array.isArray(payload.selected_remote_aliases)) {
      return [];
    }
    return payload.selected_remote_aliases.filter((item) => typeof item === "string" && item.trim());
  } catch {
    return [];
  }
}

export function saveSelectedRemoteAliases(filePath, aliases) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const payload = { selected_remote_aliases: aliases };
  fs.writeFileSync(filePath, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
}
