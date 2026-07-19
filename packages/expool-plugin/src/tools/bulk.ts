import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { CommandResult, CommandRunner } from "../runner.js";

// 工具返回统一包成 MCP text content（与 read.ts 保持一致）。
const wrap = (r: unknown) => ({
  content: [{ type: "text" as const, text: JSON.stringify(r) }],
});

// 从 CommandResult.result 安全取出 sessions 数组（r.result 须为对象且 sessions 须为数组）。
function extractSessions(r: CommandResult): Array<Record<string, unknown>> {
  const result = r.result;
  if (typeof result !== "object" || result === null) return [];
  const sessions = (result as Record<string, unknown>).sessions;
  return Array.isArray(sessions) ? (sessions as Array<Record<string, unknown>>) : [];
}

function extractTotal(r: CommandResult, fallback: number): number {
  const result = r.result;
  if (typeof result !== "object" || result === null) return fallback;
  const total = (result as Record<string, unknown>).total;
  return typeof total === "number" && Number.isFinite(total) ? total : fallback;
}

// 里程碑：迁移批量类工具（detect / upload-all / install-skill），逐字对照 Python 版。
export function registerBulkTools(server: McpServer, runner: CommandRunner): void {
  // 1) exp_detect_runtimes：只读探测，免 key，所有 source 并行 list-sessions。
  server.registerTool(
    "exp_detect_runtimes",
    {
      description:
        "检测本机有哪些 agent runtime 有可识别的 session 数据。只读、免凭据，可在 /expool:bind 前用于核对 runtime/model 识别。",
      inputSchema: {},
    },
    async () => {
      // 按需只支持 claude-code 与 codex 两个 runtime。
      const sources = ["claude-code", "codex"];

      // 每个 source 是独立子进程，彼此无依赖 —— 用 Promise.all 真并行。
      const entries = await Promise.all(
        sources.map(async (src) => {
          const r = await runner.run(
            ["list-sessions", "--source", src, "--limit", "5", "--with-model"],
            { timeoutMs: 30_000, requireKey: false },
          );
          const sessions = extractSessions(r);
          const newest = (sessions[0] ?? {}) as Record<string, unknown>;
          const info = {
            available: Boolean(r.ok && sessions.length > 0),
            count: extractTotal(r, sessions.length),
            newest: newest.mtime ?? newest.ended_at ?? newest.updated_at ?? null,
            model: (newest.model as string | undefined) ?? "unknown",
            error: r.error ?? null,
          };
          return [src, info] as const;
        }),
      );

      const detected: Record<string, unknown> = {};
      for (const [src, info] of entries) detected[src] = info;
      return wrap({ ok: true, detected });
    },
  );

  // 2) exp_upload_all：批量上传。full=true 时逐 source push-all；否则跑增量 daemon-tick。
  server.registerTool(
    "exp_upload_all",
    {
      description:
        "一键批量上传本机各 agent runtime 的新 session（强制 acl=private）。full=false 走增量 daemon-tick；full=true 按 source 重新 push-all（须显式给 sources）。",
      inputSchema: {
        sources: z.array(z.string()).optional(),
        full: z.boolean().default(false),
      },
    },
    async ({ sources, full }) => {
      if (full === true) {
        if (!sources || sources.length === 0) {
          return wrap({
            ok: false,
            error:
              "full=True requires an explicit sources=[...] list to avoid blasting every backend.",
          });
        }

        // Full backfills are CPU/IO heavy (especially long Codex rollouts),
        // so run sources sequentially to avoid saturating the SQLite gateway.
        const results: Array<readonly [string, CommandResult]> = [];
        for (const src of sources) {
          try {
            const r = await runner.run(
              [
                "push-all",
                "--source",
                src,
                "--yes",
                "--task",
                "auto-sync",
                "--sensitivity",
                "medium",
                "--acl",
                "private",
              ],
              { timeoutMs: 600_000 },
            );
            results.push([src, r] as const);
          } catch (e) {
            results.push([
              src,
              { ok: false, result: null, error: String(e) } as CommandResult,
            ] as const);
          }
        }

        const by_source: Record<string, unknown> = {};
        for (const [src, r] of results) by_source[src] = r;
        return wrap({ ok: true, mode: "full", by_source });
      }

      return wrap(await runner.run(["daemon-tick"], { timeoutMs: 600_000 }));
    },
  );

  // 3) exp_install_skill：把蒸馏后的 skill 安装到本地目录。
  server.registerTool(
    "exp_install_skill",
    {
      description: "把一个蒸馏后的 skill 安装到本地目录。",
      inputSchema: {
        name: z.string(),
        target: z.string(),
      },
    },
    async ({ name, target }) => {
      const args = ["skills-install", "--name", name, "--target", target];
      return wrap(await runner.run(args));
    },
  );
}
