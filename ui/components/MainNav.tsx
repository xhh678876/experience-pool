import Link from "@/components/ui/link";
import {
  Award,
  Database,
  FolderKanban,
  Globe2,
  KeyRound,
  Plug,
  Radar,
  Search,
  ShieldCheck,
  Sparkles,
} from "lucide-react";

interface NavItem {
  href: string;
  label: string;
  /** Brief one-liner shown on hover. */
  title: string;
  icon: React.ReactNode;
}

const fleetEnabled = ["1", "true", "yes"].includes(
  (process.env.EXP_FLEET_ENABLED ?? "").toLowerCase(),
);

// 极简一级导航：默认只留最常用入口；可选功能用环境变量显式打开。
const primaryNav: NavItem[] = [
  { href: "/plugins",     label: "插件",   title: "安装和配置 Experience Pool 插件",      icon: <Plug className="h-4 w-4" /> },
  { href: "/me/api-keys", label: "API Key", title: "管理经验池 API Key 和本机绑定凭据",  icon: <KeyRound className="h-4 w-4" /> },
  { href: "/rewards",     label: "Q值排序", title: "按复用率和奖励查看经验 Q 值排序",    icon: <Award className="h-4 w-4" /> },
  { href: "/search",      label: "检索",   title: "向量检索池子里的经验",                icon: <Search className="h-4 w-4" /> },
  { href: "/recall",      label: "召回",   title: "查看自动召回命中和注入上下文",        icon: <Sparkles className="h-4 w-4" /> },
  { href: "/projects",    label: "项目池", title: "创建项目并共享成员个人经验库",        icon: <FolderKanban className="h-4 w-4" /> },
  { href: "/experiences", label: "经验库", title: "全部经验列表 + 详情",                 icon: <Database className="h-4 w-4" /> },
  { href: "/me",          label: "我的",   title: "我上传的经验 / 撤回 / 一键发布",       icon: <ShieldCheck className="h-4 w-4" /> },
  { href: "/community",   label: "社区池", title: "已发布到社区的经验（需先发布 3 条解锁）", icon: <Globe2 className="h-4 w-4" /> },
];
if (fleetEnabled) {
  primaryNav.push({
    href: "/fleet",
    label: "舰队",
    title: "claude-fleet：监控本机 claude-code/codex 会话",
    icon: <Radar className="h-4 w-4" />,
  });
}

function NavLink({ item }: { item: NavItem }) {
  return (
    <Link
      href={item.href}
      title={item.title}
      className="inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-sm text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground"
    >
      {item.icon}
      {item.label}
    </Link>
  );
}

export default function MainNav() {
  return (
    <nav className="flex min-w-0 items-center gap-1">
      <Link
        href="/"
        title="经验池"
        className="mr-3 inline-flex min-w-0 items-center gap-2 rounded-md px-1 py-1 text-sm font-semibold hover:bg-muted/50"
      >
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-cyan-600/25 bg-cyan-50 text-cyan-700">
          池
        </span>
        <span className="truncate">经验池</span>
      </Link>
      {primaryNav.map((it) => (
        <NavLink key={it.href} item={it} />
      ))}
    </nav>
  );
}
