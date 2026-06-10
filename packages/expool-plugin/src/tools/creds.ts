import { z } from "zod";
import { existsSync, mkdirSync, chmodSync } from "node:fs";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { CommandRunner } from "../runner.js";
import type { Config } from "../config.js";

// 工具返回统一包成 MCP text content（结构与 Python 版 _run 字段一致，便于下游解析）。
const wrap = (r: unknown) => ({
  content: [{ type: "text" as const, text: JSON.stringify(r) }],
});

// bind / bind-api / pair 三条路径写凭据前都要保证 cred dir 存在且 0700；写完再把
// 凭据文件收紧到 0600。失败一律忽略（与 Python 版 try/except OSError: pass 一致）。
function ensureCredDir(credDir: string): void {
  mkdirSync(credDir, { recursive: true });
  try {
    chmodSync(credDir, 0o700);
  } catch {
    /* best-effort，忽略 chmod 失败 */
  }
}

function tightenCredentialPath(result: unknown): void {
  if (result && typeof result === "object") {
    const raw = (result as Record<string, unknown>).credential_path;
    if (raw) {
      try {
        chmodSync(String(raw), 0o600);
      } catch {
        /* best-effort，忽略 chmod 失败 */
      }
    }
  }
}

// 里程碑2：凭据 / 状态工具（whoami / status / bind / bind-api / pair）。
// 敏感值（secret / api_key / code）一律走 extraEnv 不进 argv，避免出现在 ps aux。
export function registerCredsTools(server: McpServer, runner: CommandRunner, cfg: Config): void {
  server.registerTool(
    "expool_whoami",
    {
      description: "显示当前凭据身份（agent_name / agent_id）。",
      inputSchema: {},
    },
    async () => wrap(await runner.run(["whoami"])),
  );

  server.registerTool(
    "expool_status",
    {
      description: "插件状态总览：是否已配置凭据、存储位置、网关、vendored CLI 是否就位。",
      inputSchema: {},
    },
    async () => {
      // 权威判断仍走 vendored CLI 的 whoami（纯本地、免网络、免 key）。
      const probe = await runner.run(["whoami"], { requireKey: false, timeoutMs: 30_000 });
      const res = probe.result;
      const configured = Boolean(probe.ok) && res !== null && typeof res === "object";
      const dict = configured ? (res as Record<string, unknown>) : {};
      const authType = (dict.auth_type as string | undefined) ?? (configured ? "unknown" : "none");
      const agentName = (dict.agent_name as string | undefined) ?? null;
      return wrap({
        ok: true,
        configured,
        auth_type: authType,
        agent_name: agentName,
        gateway: cfg.base,
        credential_dir: cfg.credDir,
        vendored_cli: cfg.vendoredCli,
        vendored_cli_present: existsSync(cfg.vendoredCli),
      });
    },
  );

  server.registerTool(
    "expool_bind",
    {
      description: "为用户安装 agent_name + secret 凭据，默认写后做一次 /healthz 验证。",
      inputSchema: {
        agent_name: z.string(),
        secret: z.string(),
        agent_id: z.string().optional(),
        team: z.string().optional(),
        verify: z.boolean().default(true),
      },
    },
    async ({ agent_name, secret, agent_id, team, verify }) => {
      const args = ["bind", "--name", agent_name, "--skip-claude-settings"];
      if (agent_id) args.push("--agent-id", agent_id);
      if (team) args.push("--team", team);
      if (verify === false) args.push("--no-verify");

      ensureCredDir(cfg.credDir);
      // secret 走 EXP_BIND_SECRET 环境变量，不进 argv。
      const out = await runner.run(args, {
        requireKey: false,
        extraEnv: { EXP_BIND_SECRET: secret },
      });
      tightenCredentialPath(out.result);
      return wrap(out);
    },
  );

  server.registerTool(
    "expool_bind_api",
    {
      description: "安装门户签发的 Bearer API Key（推荐的 plugin-first 绑定路径）。",
      inputSchema: {
        api_key: z.string(),
        agent_name: z.string().optional(),
        verify: z.boolean().default(true),
      },
    },
    async ({ api_key, agent_name, verify }) => {
      const args = ["bind-api"];
      if (agent_name) args.push("--agent-name", agent_name);
      if (verify === false) args.push("--no-verify");

      ensureCredDir(cfg.credDir);
      // api_key 走 EXP_BIND_API_KEY 环境变量，不进 argv。
      const out = await runner.run(args, {
        requireKey: false,
        extraEnv: { EXP_BIND_API_KEY: api_key },
      });
      tightenCredentialPath(out.result);
      return wrap(out);
    },
  );

  server.registerTool(
    "expool_pair",
    {
      description: "用门户一次性配对码（expair_...）换取本机 API Key。",
      inputSchema: {
        code: z.string(),
        agent_name: z.string().optional(),
        verify: z.boolean().default(true),
      },
    },
    async ({ code, agent_name, verify }) => {
      const args = ["pair"];
      if (agent_name) args.push("--agent-name", agent_name);
      if (verify === false) args.push("--no-verify");

      ensureCredDir(cfg.credDir);
      // code 走 EXP_PAIR_CODE 环境变量，不进 argv。
      const out = await runner.run(args, {
        requireKey: false,
        extraEnv: { EXP_PAIR_CODE: code },
      });
      tightenCredentialPath(out.result);
      return wrap(out);
    },
  );
}
