import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { loadConfig } from "./config.js";
import { SubprocessRunner } from "./runner.js";
import { isCredConfigured } from "./creds.js";
import { registerReadTools } from "./tools/read.js";
import { registerCredsTools } from "./tools/creds.js";
import { registerWriteTools } from "./tools/write.js";
import { registerBulkTools } from "./tools/bulk.js";
import { registerPrompts } from "./prompts.js";

async function main(): Promise<void> {
  const cfg = loadConfig();
  const runner = new SubprocessRunner(cfg);
  // 注入凭据门控（打破 runner ↔ creds 的循环依赖）。
  runner.credCheck = () => isCredConfigured(runner);

  const server = new McpServer({ name: "expool", version: "0.3.0" });
  registerCredsTools(server, runner, cfg);
  registerReadTools(server, runner);
  registerWriteTools(server, runner);
  registerBulkTools(server, runner);
  registerPrompts(server, cfg);

  const transport = new StdioServerTransport();
  await server.connect(transport);
  // 重要：stdout 被 JSON-RPC 占用，所有日志必须走 stderr，否则污染协议。
  console.error(`[expool-mcp] ready · base=${cfg.base} · vendor=${cfg.vendoredCli}`);
}

main().catch((e) => {
  console.error("[expool-mcp] fatal:", e);
  process.exit(1);
});
