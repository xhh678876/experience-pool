/**
 * Credential and endpoint config.
 *
 * Lookup order for credentials:
 *   1. EXP_AGENT_NAME + EXP_AGENT_SECRET environment variables (CI / non-interactive)
 *   2. ~/.experience-pool/credentials/<agent>.json (file dropped by `exp register`)
 *
 * The agent file is shaped like the Python `Credential` dataclass produces:
 *   { agent_id, agent_name, team, secret }
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export interface Credential {
  agent_id: string;
  agent_name: string;
  team: string;
  secret: string;
}

export interface Endpoint {
  baseUrl: string;
}

export const DEFAULT_BASE_URL =
  process.env.EXP_BASE_URL ?? "http://localhost:8080";

export function credentialsDir(): string {
  return (
    process.env.EXP_CREDENTIALS_DIR ??
    path.join(os.homedir(), ".experience-pool", "credentials")
  );
}

export function loadCredential(agentName?: string): Credential | null {
  if (process.env.EXP_AGENT_NAME && process.env.EXP_AGENT_SECRET) {
    return {
      agent_id: process.env.EXP_AGENT_ID ?? "",
      agent_name: process.env.EXP_AGENT_NAME,
      team: process.env.EXP_AGENT_TEAM ?? "",
      secret: process.env.EXP_AGENT_SECRET,
    };
  }
  const dir = credentialsDir();
  if (!fs.existsSync(dir)) return null;
  if (agentName) {
    const file = path.join(dir, `${agentName}.json`);
    if (!fs.existsSync(file)) return null;
    return JSON.parse(fs.readFileSync(file, "utf-8")) as Credential;
  }
  const files = fs.readdirSync(dir).filter((f) => f.endsWith(".json"));
  if (files.length !== 1) return null;
  return JSON.parse(fs.readFileSync(path.join(dir, files[0]), "utf-8")) as Credential;
}

export function saveCredential(cred: Credential): string {
  const dir = credentialsDir();
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `${cred.agent_name}.json`);
  fs.writeFileSync(file, JSON.stringify(cred, null, 2), { mode: 0o600 });
  return file;
}
