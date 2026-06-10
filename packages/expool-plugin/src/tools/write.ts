import { z } from "zod";
import { existsSync } from "node:fs";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { CommandRunner } from "../runner.js";

// 工具返回统一包成 MCP text content（结构与 Python 版 _run 字段一致，便于下游解析）。
const wrap = (r: unknown) => ({
  content: [{ type: "text" as const, text: JSON.stringify(r) }],
});

// 里程碑2：写工具（push / revoke / publish / unpublish）。
// ★ ACL 硬锁：所有 push 类工具强制 --acl private，不向用户暴露 acl 参数；
//   推到社区池是独立的显式步骤，走 exp_publish(confirm=true)。
export function registerWriteTools(server: McpServer, runner: CommandRunner): void {
  server.registerTool(
    "exp_push_latest",
    {
      description:
        "上传最近一次本地 session（永远 private）。要发到社区池请改用 exp_publish。",
      inputSchema: {
        source: z.string().default("auto"),
        task: z.string().default("misc"),
        sensitivity: z.string().default("medium"),
        tag: z.string().optional(),
        no_trace: z.boolean().default(false),
        annotate: z.boolean().default(false),
      },
    },
    async ({ source, task, sensitivity, tag, no_trace, annotate }) => {
      // push 永远 private；推社区池是独立步骤，走 exp_publish(confirm=true)。
      const args = [
        "push-latest",
        "--yes",
        "--source",
        source,
        "--task",
        task,
        "--sensitivity",
        sensitivity,
        "--acl",
        "private",
      ];
      if (tag) args.push("--tag", tag);
      if (no_trace) args.push("--no-trace");
      if (annotate) args.push("--annotate");
      return wrap(await runner.run(args, { timeoutMs: 300_000 }));
    },
  );

  server.registerTool(
    "exp_push_file",
    {
      description: "上传指定的一个 trajectory 文件（永远 private）。",
      inputSchema: {
        file: z.string(),
        task: z.string().default("misc"),
        sensitivity: z.string().default("medium"),
        tag: z.string().optional(),
        no_trace: z.boolean().default(false),
      },
    },
    async ({ file, task, sensitivity, tag, no_trace }) => {
      if (!existsSync(file)) {
        return wrap({ ok: false, error: `file not found: ${file}` });
      }
      // push 永远 private；推社区池是独立步骤，走 exp_publish(confirm=true)。
      const args = [
        "push-file",
        "--yes",
        "--file",
        file,
        "--task",
        task,
        "--sensitivity",
        sensitivity,
        "--acl",
        "private",
      ];
      if (tag) args.push("--tag", tag);
      if (no_trace) args.push("--no-trace");
      return wrap(await runner.run(args, { timeoutMs: 300_000 }));
    },
  );

  server.registerTool(
    "exp_revoke",
    {
      description: "撤回（软删除）调用者自己的一条经验。",
      inputSchema: { experience_id: z.string() },
    },
    async ({ experience_id }) =>
      wrap(await runner.run(["revoke", "--eid", experience_id])),
  );

  server.registerTool(
    "exp_publish",
    {
      description: "把一条 private 经验推到社区池（不可逆）。必须 confirm=true 才执行。",
      inputSchema: {
        experience_id: z.string(),
        confirm: z.boolean().default(false),
      },
    },
    async ({ experience_id, confirm }) => {
      // 门控：confirm 不为 true 时拒绝，避免误发到全社区可见。
      if (confirm !== true) {
        return wrap({
          ok: false,
          error:
            "publish requires confirm=true. This makes the experience visible to the whole community pool. Ask the user before retrying.",
        });
      }
      return wrap(await runner.run(["publish", "--eid", experience_id]));
    },
  );

  server.registerTool(
    "exp_unpublish",
    {
      description: "把一条已发布的经验从社区池撤回到 private。",
      inputSchema: { experience_id: z.string() },
    },
    async ({ experience_id }) =>
      wrap(await runner.run(["unpublish", "--eid", experience_id])),
  );
}
