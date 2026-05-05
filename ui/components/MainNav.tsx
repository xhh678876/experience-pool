import Link from "@/components/ui/link";
import {
  Award,
  Boxes,
  Database,
  Gauge,
  Globe2,
  Layers3,
  Search,
  Settings,
  ShieldCheck,
  Wrench,
} from "lucide-react";

interface NavItem {
  href: string;
  label: string;
  // Brief one-liner shown on hover.
  title: string;
  icon: React.ReactNode;
  /** Render in muted color for advanced/admin features. */
  muted?: boolean;
}

const browseNav: NavItem[] = [
  { href: "/search",      label: "检索",     title: "向量检索池子里的经验",           icon: <Search className="h-4 w-4" /> },
  { href: "/experiences", label: "经验库",   title: "全部经验列表 + 详情",            icon: <Database className="h-4 w-4" /> },
  { href: "/sessions",    label: "Session",  title: "按 session 聚合的最近上传",      icon: <Boxes className="h-4 w-4" /> },
];

const personalNav: NavItem[] = [
  { href: "/me",        label: "我的",   title: "我上传的经验 / 撤回 / 一键发布", icon: <ShieldCheck className="h-4 w-4" /> },
  { href: "/community", label: "社区池", title: "已发布到社区的经验(需先发布 3 条解锁)", icon: <Globe2 className="h-4 w-4" /> },
];

const advancedNav: NavItem[] = [
  { href: "/clusters", label: "经验簇", title: "经验聚类 + 结晶为 skill",   icon: <Layers3 className="h-4 w-4" />, muted: true },
  { href: "/rewards",  label: "奖励",   title: "Judge 评分 / 5 维奖励标注", icon: <Award className="h-4 w-4" />, muted: true },
  { href: "/skills",   label: "技能",   title: "可复用 skill bundle 库",     icon: <Wrench className="h-4 w-4" />, muted: true },
];

const adminNav: NavItem[] = [
  { href: "/consent", label: "Consent", title: "本机 consent.json 编辑器", icon: <Settings className="h-4 w-4" />, muted: true },
  { href: "/admin",   label: "Admin",   title: "池统计 / 健康检查",         icon: <Gauge className="h-4 w-4" />, muted: true },
];

function NavLink({ item }: { item: NavItem }) {
  return (
    <Link
      href={item.href}
      title={item.title}
      className={
        "inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-sm transition-colors hover:bg-muted/70 hover:text-foreground " +
        (item.muted ? "text-muted-foreground/70" : "text-muted-foreground")
      }
    >
      {item.icon}
      {item.label}
    </Link>
  );
}

function Group({ items }: { items: NavItem[] }) {
  return (
    <div className="flex items-center gap-0.5">
      {items.map((it) => (
        <NavLink key={it.href} item={it} />
      ))}
    </div>
  );
}

function Separator() {
  return <span aria-hidden className="mx-1 hidden h-5 w-px bg-border/60 lg:inline-block" />;
}

export default function MainNav() {
  return (
    <nav className="flex min-w-0 flex-wrap items-center gap-1">
      <Link
        href="/"
        title="创智经验池"
        className="mr-2 inline-flex min-w-0 items-center gap-2 rounded-md px-1 py-1 text-sm font-semibold hover:bg-muted/50"
      >
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-cyan-600/25 bg-cyan-50 text-cyan-700">
          智
        </span>
        <span className="truncate">创智经验池</span>
      </Link>
      <Group items={browseNav} />
      <Separator />
      <Group items={personalNav} />
      <Separator />
      <Group items={advancedNav} />
      <Separator />
      <Group items={adminNav} />
    </nav>
  );
}
