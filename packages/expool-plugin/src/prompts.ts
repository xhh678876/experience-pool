import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { Config } from "./config.js";

// 与 Python 版 _split_frontmatter 行为一致的简版 YAML frontmatter 解析：
// 只取顶层 `key: value` 行，value 去首尾空白并剥掉成对引号。
function splitFrontmatter(text: string): { fm: Record<string, string>; body: string } {
  if (!text.startsWith("---\n")) {
    return { fm: {}, body: text };
  }
  // 对齐 Python 的 text.split("---\n", 2)：最多切出 [前, fm块, 剩余body]。
  const first = text.indexOf("---\n");
  const second = text.indexOf("---\n", first + 4);
  if (second < 0) {
    return { fm: {}, body: text };
  }
  const fmBlock = text.slice(first + 4, second);
  const rest = text.slice(second + 4);

  const fm: Record<string, string> = {};
  for (const line of fmBlock.split("\n")) {
    if (line.includes(":") && !line.trimStart().startsWith("#")) {
      const idx = line.indexOf(":");
      const key = line.slice(0, idx).trim();
      let value = line.slice(idx + 1).trim();
      // 去掉成对的首尾引号（双引号或单引号）。
      if (value.length >= 2) {
        const q = value[0];
        if ((q === '"' || q === "'") && value[value.length - 1] === q) {
          value = value.slice(1, -1);
        }
      }
      fm[key] = value;
    }
  }
  // body 去掉开头的换行（对齐 Python 的 body.lstrip("\n")）。
  return { fm, body: rest.replace(/^\n+/, "") };
}

// 把 commands/*.md 动态注册成 MCP prompts（slash 命令），对照 Python 版
// _register_command_prompts。无参 prompt 不传 argsSchema，避免把闭包变量当用户参数。
export function registerPrompts(server: McpServer, cfg: Config): void {
  const commandsDir = join(cfg.pluginRoot, "commands");
  if (!existsSync(commandsDir)) {
    return;
  }

  const files = readdirSync(commandsDir)
    .filter((f) => f.endsWith(".md"))
    .sort();

  for (const file of files) {
    const text = readFileSync(join(commandsDir, file), "utf-8");
    const { fm, body } = splitFrontmatter(text);
    const name = file.slice(0, -".md".length); // stem，去掉 .md 后缀
    const description = fm.description || `/expool:${name}`;

    server.registerPrompt(
      `expool:${name}`,
      { description },
      () => ({
        messages: [
          { role: "user" as const, content: { type: "text" as const, text: body } },
        ],
      }),
    );
  }
}
