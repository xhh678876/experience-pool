import { publicGatewayBase } from "@/lib/public-url";
import { BookOpen, ExternalLink, Key } from "lucide-react";
import Link from "@/components/ui/link";

export const dynamic = "force-dynamic";

/**
 * Public docs hub. Links go directly at the FastAPI server's swagger /
 * redoc / openapi-json endpoints, which are PUBLIC (no auth needed).
 *
 * NOTE: The href below is computed at request time on the *server* — it
 * points at EXP_BIND_BASE_URL (the externally reachable URL of the API
 * gateway) when set, otherwise the same host as the request. The browser
 * follows the link, so the URL must be browser-reachable, not the
 * internal 127.0.0.1:8080.
 */
function publicApiBase(): string {
  return publicGatewayBase();
}

export default function ApiDocsPage() {
  const base = publicApiBase();
  const swagger = `${base}/docs`;
  const redoc = `${base}/redoc`;
  const openapi = `${base}/openapi.json`;
  const protocol = `${base}/api-protocol`;
  const pairCommand = `expool-plugin pair expair_... --base ${base}`;

  return (
    <div className="flex flex-col gap-6 pb-12">
      <section className="rounded-2xl border border-border/60 bg-white/85 px-5 py-4">
        <div className="flex items-center gap-2 text-sm">
          <BookOpen className="h-4 w-4 text-cyan-700" />
          <span className="font-semibold">API 文档入口</span>
        </div>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          本服务提供 OpenAPI 3.1 规范 + 自动生成的 Swagger / ReDoc 文档。
          下面所有链接都是公开访问（无需 key）。
        </p>
        <ul className="mt-3 grid gap-2 sm:grid-cols-2">
          <DocLink
            href={swagger}
            title="Swagger UI"
            hint="可交互的接口探索面板（可直接试调）"
          />
          <DocLink
            href={redoc}
            title="ReDoc"
            hint="只读、结构化的接口手册"
          />
          <DocLink
            href={openapi}
            title="openapi.json"
            hint="规范源文件（用于自动生成 SDK / 客户端）"
          />
          <DocLink
            href={protocol}
            title="API 协议说明（markdown）"
            hint="鉴权方式 / 错误码 / 限流 / 例子"
          />
        </ul>
      </section>

      <section className="rounded-2xl border border-border/60 bg-white/85 px-5 py-4">
        <div className="flex items-center gap-2 text-sm">
          <Key className="h-4 w-4 text-amber-700" />
          <span className="font-semibold">怎么调用</span>
        </div>
        <ol className="mt-3 list-decimal space-y-1.5 pl-5 text-sm leading-6">
          <li>
            在 <Link href="/me/api-keys" className="text-cyan-700 hover:underline">API Keys 页</Link> 生成一次性绑定码或 key（要先登录）。
          </li>
          <li>
            插件用户优先运行 <code className="font-mono">/expool:pair expair_...</code>
            或 <code className="font-mono">{pairCommand}</code>。
          </li>
          <li>直接调 HTTP 时，头加 <code className="font-mono">Authorization: Bearer expk_...</code>。</li>
          <li>
            发请求到任何 <code className="font-mono">/v1/*</code> 接口。
            HMAC 也支持（旧客户端），三种鉴权任选其一。
          </li>
        </ol>
        <pre className="mt-3 overflow-x-auto rounded-md border border-border/60 bg-muted/30 px-3 py-2 text-xs leading-5">
{`# example
curl -sS -X POST "${base}/v1/lite/push" \\
  -H "Authorization: Bearer expk_..." \\
  -H "Content-Type: application/json" \\
  -d '{
    "session_id": "demo-001",
    "agent_type": "claude-code",
    "started_at": "2026-05-12T10:00:00Z",
    "ended_at":   "2026-05-12T10:05:00Z",
    "query":   "hi",
    "intent":  "say hi",
    "outcome": "said hi",
    "steps":   ["say hi"],
    "trajectory": [
      {"role":"user","content":"hi"},
      {"role":"assistant","content":"hello"}
    ]
  }'`}
        </pre>
      </section>
    </div>
  );
}

function DocLink({ href, title, hint }: { href: string; title: string; hint: string }) {
  return (
    <li>
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className="flex items-start gap-2 rounded-lg border border-border/60 bg-white/70 px-3 py-2 hover:border-cyan-600/40 hover:bg-cyan-50/50"
      >
        <ExternalLink className="mt-0.5 h-3.5 w-3.5 flex-none text-cyan-700" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold">{title}</div>
          <div className="text-xs text-muted-foreground">{hint}</div>
        </div>
      </a>
    </li>
  );
}
