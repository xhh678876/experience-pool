import Link from "@/components/ui/link";

// 从顶栏精简下来的次要 / 高级 / 管理入口，统一收纳到页脚。
const footerLinks: { href: string; label: string }[] = [
  { href: "/sessions",    label: "Session" },
  { href: "/plugins",     label: "插件" },
  { href: "/me/api-keys", label: "API Key" },
  { href: "/clusters",    label: "经验簇" },
  { href: "/rewards",     label: "奖励" },
  { href: "/skills",      label: "技能" },
  { href: "/api-docs",    label: "API 文档" },
  { href: "/consent",     label: "Consent" },
  { href: "/admin",       label: "Admin" },
];

export default function SiteFooter() {
  return (
    <footer className="mx-auto max-w-[1600px] px-3 pb-8 pt-4">
      <div className="border-t border-border/50 pt-4">
        <div className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1.5 text-xs text-muted-foreground">
          {footerLinks.map((l, i) => (
            <span key={l.href} className="inline-flex items-center gap-3">
              {i > 0 ? <span aria-hidden className="text-border/70">·</span> : null}
              <Link href={l.href} className="hover:text-cyan-800 hover:underline">
                {l.label}
              </Link>
            </span>
          ))}
        </div>
        <p className="mt-3 text-center text-[11px] text-muted-foreground/60">
          经验池 Experience Pool · 多 Agent 共享
        </p>
      </div>
    </footer>
  );
}
