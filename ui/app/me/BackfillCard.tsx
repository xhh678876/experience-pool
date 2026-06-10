import { Boxes, Lock, ExternalLink } from "lucide-react";
import Link from "@/components/ui/link";
import { CopyButton } from "@/components/ui/copy-button";
import { apiBindScript } from "@/lib/users-api";

/** Server-rendered card on /me showing the standalone session-extractor
 * one-liner. Renders nothing if the user isn't logged in (the real /me
 * itself is auth-gated, but apiBindScript returns null on no session). */
export default async function BackfillCard() {
  const bind = await apiBindScript();
  if (!bind?.extract_command) return null;

  const cmd = bind.extract_command;
  const docsUrl = `${bind.base_url}/session-extractor/README.md`;

  return (
    <section className="flex flex-col gap-3 rounded-2xl border border-cyan-600/30 bg-cyan-50/30 px-5 py-4">
      <div className="flex items-center gap-2 text-sm">
        <Boxes className="h-4 w-4 text-cyan-700" />
        <span className="font-semibold">📦 历史会话回填</span>
        <span className="ml-2 inline-flex items-center gap-1 rounded-md border border-emerald-500/30 bg-emerald-50 px-2 py-0.5 text-[10px] text-emerald-800">
          <Lock className="h-3 w-3" />
          仅私有,绝不公开
        </span>
      </div>

      <p className="text-xs leading-5 text-muted-foreground">
        把你机器上已有的 Claude Code / Codex / hermes / openclaw 历史 session
        全量上传到你的私库。脚本里 <code className="font-mono">acl</code> 写死
        <code className="font-mono">private</code>,**这条通道永远不会发到经验池公共/团队池**。
        服务端会按内容指纹去重,可重复跑。
      </p>

      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
          <span>复制 → 在有 session 数据的那台机器粘到 shell</span>
          <Link
            href={docsUrl}
            className="inline-flex items-center gap-1 text-cyan-700 hover:underline"
          >
            查看说明
            <ExternalLink className="h-3 w-3" />
          </Link>
        </div>
        <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-start">
          <code className="flex-1 break-all rounded-lg border border-cyan-600/30 bg-white/85 px-3 py-2.5 font-mono text-[11px] leading-5 text-foreground">
            {cmd}
          </code>
          <CopyButton text={cmd} label="复制" className="h-10 px-4 sm:self-start" />
        </div>
      </div>

      <details className="text-[11px] text-muted-foreground">
        <summary className="cursor-pointer hover:text-foreground">
          运行后会做什么?
        </summary>
        <ul className="ml-4 mt-1.5 list-disc space-y-1">
          <li>从 ~/.claude/projects/ ~/.codex/sessions/ 等扫历史 session</li>
          <li>每条 session 用你的 HMAC secret 签名 POST 到 /v1/lite/push</li>
          <li>服务端 Layer 1 脱敏 → 落到你的私库 (acl=private)</li>
          <li>同内容指纹重复 push 不会创建新行</li>
          <li>跑完打印 uploaded / duplicate / failed 计数</li>
          <li>可加 --dry-run 先预览不真传</li>
        </ul>
      </details>
    </section>
  );
}
