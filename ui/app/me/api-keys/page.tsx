import Link from "@/components/ui/link";
import { Key, ShieldCheck, BookOpen } from "lucide-react";
import { redirect } from "next/navigation";
import { withPublicBase } from "@/lib/base-path";
import { apiListApiKeys, apiMe } from "@/lib/users-api";
import { formatDate } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { publicGatewayBase } from "@/lib/public-url";
import CreateKeyForm from "./CreateKeyForm";
import CreatePairingCodeForm from "./CreatePairingCodeForm";
import RevokeKeyButton from "./RevokeKeyButton";

export const dynamic = "force-dynamic";

export default async function ApiKeysPage() {
  const me = await apiMe();
  if (!me) redirect(withPublicBase("/login?next=/me/api-keys"));

  const list = await apiListApiKeys();
  const keys = list?.keys ?? [];
  const active = keys.filter((k) => !k.revoked);
  const revoked = keys.filter((k) => k.revoked);
  const base = publicGatewayBase();

  return (
    <div className="flex flex-col gap-6 pb-12">
      <section className="rounded-2xl border border-border/60 bg-white/85 px-5 py-4">
        <div className="flex items-center gap-2 text-sm">
          <Key className="h-4 w-4 text-cyan-700" />
          <span className="font-semibold">API Keys</span>
          <span className="text-muted-foreground">
            · 绑定到 agent <code className="font-mono">{me.default_agent_name}</code>
          </span>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          推荐先生成一次性绑定码，在 agent 里运行{" "}
          <code className="font-mono">/expool:pair expair_...</code>。
          插件会换取并保存 <code className="font-mono">expk_...</code> API key；
          你也可以手动复制 key 用 <code className="font-mono">/expool:bind-api expk_...</code>
          或 <code className="font-mono">/expool:bind+api expk_...</code>。
        </p>
        <div className="mt-2 flex flex-wrap gap-3 text-xs">
          <Link
            href="/api-docs"
            className="inline-flex items-center gap-1 text-cyan-700 hover:underline"
          >
            <BookOpen className="h-3.5 w-3.5" /> API 文档（swagger）
          </Link>
        </div>
      </section>

      <section className="rounded-2xl border border-border/60 bg-white/85 px-5 py-4">
        <div className="mb-2 flex items-center gap-2 text-sm">
          <ShieldCheck className="h-4 w-4 text-cyan-700" />
          <span className="font-semibold">一次性插件绑定码</span>
          <span className="text-muted-foreground">· 不需要把完整 API key 粘进聊天</span>
        </div>
        <CreatePairingCodeForm base={base} />
      </section>

      <section className="rounded-2xl border border-border/60 bg-white/85 px-5 py-4">
        <div className="mb-2 flex items-center gap-2 text-sm">
          <ShieldCheck className="h-4 w-4 text-emerald-700" />
          <span className="font-semibold">手动新建 key</span>
          <span className="text-muted-foreground">· 生成后只显示一次</span>
        </div>
        <CreateKeyForm base={base} />
      </section>

      <section className="rounded-2xl border border-border/60 bg-white/85">
        <div className="flex items-center gap-2 border-b border-border/60 px-5 py-3 text-sm">
          <span className="font-semibold">已有 key</span>
          <span className="text-muted-foreground">
            · {active.length} 把 active{revoked.length > 0 ? ` · ${revoked.length} 把已撤销` : ""}
          </span>
        </div>
        {keys.length === 0 ? (
          <div className="px-5 py-6 text-center text-sm text-muted-foreground">
            还没有 key —— 用上面的表单新建一把。
          </div>
        ) : (
          <ul className="divide-y divide-border/60">
            {keys.map((k) => (
              <li
                key={k.key_id}
                className="flex flex-wrap items-center gap-3 px-5 py-3 text-sm"
              >
                <code className="font-mono text-xs text-muted-foreground">
                  {k.key_prefix}…
                </code>
                <span className="font-medium">{k.name}</span>
                {k.revoked ? (
                  <Badge className="bg-rose-100 text-rose-900 font-mono text-[10px]">
                    revoked
                  </Badge>
                ) : (
                  <Badge className="bg-emerald-100 text-emerald-900 font-mono text-[10px]">
                    active
                  </Badge>
                )}
                <span className="text-xs text-muted-foreground">
                  创建：{formatDate(k.created_at)}
                </span>
                {k.last_used_at ? (
                  <span className="text-xs text-muted-foreground">
                    最近使用：{formatDate(k.last_used_at)}
                  </span>
                ) : (
                  <span className="text-xs text-muted-foreground">未使用过</span>
                )}
                {k.revoked && k.revoked_at ? (
                  <span className="text-xs text-rose-700">
                    撤销：{formatDate(k.revoked_at)}
                  </span>
                ) : null}
                <div className="ml-auto">
                  {!k.revoked ? <RevokeKeyButton keyId={k.key_id} keyName={k.name} /> : null}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
