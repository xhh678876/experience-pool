// 里程碑1 冒烟：用 MCP 客户端通过 stdio 拉起 server，验证 initialize / tools/list / tools/call。
// 启动时由命令行 env 注入 EXPOOL_BASE / EXPOOL_CRED_DIR / EXP_AGENT_NAME（透传给 server 子进程）。
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const transport = new StdioClientTransport({
  command: "npx",
  args: ["tsx", "src/index.ts"],
  env: { ...process.env } as Record<string, string>,
});

const client = new Client({ name: "expool-smoke", version: "1.0.0" });
await client.connect(transport);

const tools = await client.listTools();
console.log("[smoke] tools:", tools.tools.map((t) => t.name).join(", "));

const search = await client.callTool({
  name: "exp_search",
  arguments: { q: "expool 插件改进", top_k: 2 },
});
console.log("[smoke] exp_search ->", JSON.stringify(search).slice(0, 700));

const rag = await client.callTool({
  name: "exp_rag_context",
  arguments: { q: "expool 插件改进", top_k: 2, scope: "personal" },
});
console.log("[smoke] exp_rag_context ->", JSON.stringify(rag).slice(0, 700));

await client.close();
console.log("[smoke] OK");
