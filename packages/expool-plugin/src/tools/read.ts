import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { CommandRunner } from "../runner.js";

// 工具返回统一包成 MCP text content（结构与 Python 版 _run 字段一致，便于下游解析）。
const wrap = (r: unknown) => ({
  content: [{ type: "text" as const, text: JSON.stringify(r) }],
});

// 里程碑1：先迁 3 个只读工具（search / get / dashboard），验证 MCP 链路 + runner 端到端。
export function registerReadTools(server: McpServer, runner: CommandRunner): void {
  server.registerTool(
    "exp_search",
    {
      description: "经验池语义检索（覆盖个人池 / 社区池）。任务开工前先跑一遍。",
      inputSchema: {
        q: z.string().describe("自由文本查询"),
        top_k: z.number().int().positive().default(5),
        scope: z.enum(["auto", "personal", "community"]).default("auto"),
        task_type: z.string().optional(),
      },
    },
    async ({ q, top_k, scope, task_type }) => {
      const args = ["search", "--q", q, "--top-k", String(top_k), "--scope", scope, "--json"];
      if (task_type) args.push("--task-type", task_type);
      return wrap(await runner.run(args));
    },
  );

  server.registerTool(
    "exp_get",
    {
      description: "按 id 拉取一条经验的完整卡片。",
      inputSchema: { experience_id: z.string() },
    },
    async ({ experience_id }) => wrap(await runner.run(["get", experience_id])),
  );

  server.registerTool(
    "exp_dashboard",
    {
      description: "全局池指标（不依赖个人凭据）。",
      inputSchema: {},
    },
    // dashboard 免 key（与 Python 版上轮改造一致）。
    async () => wrap(await runner.run(["dashboard"], { requireKey: false })),
  );

  server.registerTool(
    "exp_search_skills",
    {
      description: "检索蒸馏后的 skill 库。",
      inputSchema: {
        q: z.string(),
        top_k: z.number().int().positive().default(3),
      },
    },
    async ({ q, top_k }) =>
      wrap(await runner.run(["skills-search", "--q", q, "--top-k", String(top_k)])),
  );

  server.registerTool(
    "exp_list",
    {
      description: "列出当前账号 private 库里的经验。",
      inputSchema: {
        limit: z.number().int().positive().default(50),
      },
    },
    async ({ limit }) => wrap(await runner.run(["list", "--limit", String(limit)])),
  );

  server.registerTool(
    "exp_quota",
    {
      description: "查看发布配额与社区池解锁状态。",
      inputSchema: {},
    },
    async () => wrap(await runner.run(["quota"])),
  );

  server.registerTool(
    "exp_daemon_state",
    {
      description: "查看各来源的守护进程最近状态。",
      inputSchema: {},
    },
    async () => wrap(await runner.run(["daemon-state"])),
  );

  server.registerTool(
    "exp_get_rewards",
    {
      description: "拉取某条经验已存储的 5 维 reward。",
      inputSchema: { experience_id: z.string() },
    },
    async ({ experience_id }) =>
      wrap(await runner.run(["get-rewards", "--experience-id", experience_id])),
  );
}
