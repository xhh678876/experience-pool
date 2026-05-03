import { notFound } from "next/navigation";
import Link from "@/components/ui/link";
import {
  getAuditsForTarget,
  getChildren,
  getExperience,
  getExperiencesByIds,
  getLatestReward,
  getParents,
  getQUpdates,
} from "@/lib/queries";
import { Badge } from "@/components/ui/badge";
import { formatDate, sensitivityColor, statusColor, tryParseJson } from "@/lib/utils";
import { CardTab } from "./_tabs/CardTab";
import { TrajectoryTab } from "./_tabs/TrajectoryTab";
import { LineageTab } from "./_tabs/LineageTab";
import { AuditTab } from "./_tabs/AuditTab";
import { SkillsTab } from "./_tabs/SkillsTab";
import { ActionBar } from "./_tabs/ActionBar";
import { readTrajectory } from "./_tabs/readTrajectory";

export const dynamic = "force-dynamic";

type SearchParams = {
  tab?: string;
  notice?: string;
  noticeKind?: "success" | "warn" | "danger";
};

import {
  FileText,
  GitBranch,
  History,
  MessageSquare,
  Wrench,
  Award,
  ArrowRight,
} from "lucide-react";

const TABS = [
  { key: "card", label: "概要", icon: FileText },
  { key: "trajectory", label: "对话", icon: MessageSquare },
  { key: "lineage", label: "关系", icon: GitBranch },
  { key: "skills", label: "技能", icon: Wrench },
  { key: "audit", label: "日志", icon: History },
] as const;

export default async function ExperienceDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { id } = await params;
  const sp = await searchParams;
  const requestedTab = sp.tab ?? "card";
  const tab = TABS.some((t) => t.key === requestedTab)
    ? (requestedTab as (typeof TABS)[number]["key"])
    : "card";

  const exp = getExperience(id);
  if (!exp) notFound();

  const reward = getLatestReward(id);
  const updates = getQUpdates(id);
  const parents = getParents(id);
  const children = getChildren(id);
  const audits = getAuditsForTarget(id);
  const lineageIds = [...parents.map((e) => e.parent_id), ...children.map((e) => e.child_id)];
  const lineageNodes = getExperiencesByIds(lineageIds);
  const trajectory = await readTrajectory(exp.trajectory_path);
  const currentPath = `/experiences/${id}${tab === "card" ? "" : `?tab=${tab}`}`;

  return (
    <div className="space-y-6 pb-32">
      {sp.notice ? (
        <div
          className={`rounded-lg border px-3 py-2 text-sm ${
            sp.noticeKind === "danger"
              ? "border-rose-500/30 bg-rose-50 text-rose-800"
              : sp.noticeKind === "warn"
                ? "border-amber-500/35 bg-amber-50 text-amber-800"
                : "border-emerald-500/30 bg-emerald-50 text-emerald-800"
          }`}
        >
          {sp.notice}
        </div>
      ) : null}

      {/* TL;DR header */}
      <div className="rounded-2xl border border-border/60 bg-gradient-to-br from-white via-white to-cyan-50/40 px-5 py-4">
        <div className="text-xs text-muted-foreground">
          <Link href="/experiences" className="hover:underline">
            经验库
          </Link>
          {" / "}
          <span className="font-mono">{id.slice(0, 8)}…</span>
        </div>
        <h1 className="mt-1 text-2xl font-semibold leading-snug">
          {exp.intent_text ?? "未提取意图"}
        </h1>
        {exp.outcome ? (
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground line-clamp-2">
            <span className="font-medium text-foreground">结果：</span>
            {exp.outcome}
          </p>
        ) : null}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Badge className={statusColor(exp.review_status)}>{exp.review_status}</Badge>
          <Badge className={sensitivityColor(exp.sensitivity)}>
            敏感 · {exp.sensitivity}
          </Badge>
          <Badge variant="outline" className="font-mono">{exp.task_type}</Badge>
          <Badge variant="outline" className="font-mono">{exp.source_model}</Badge>
          <Badge variant="outline" className="font-mono">q={exp.q_scalar.toFixed(2)}</Badge>
          <Badge variant="outline">复用 {exp.reuse_count}</Badge>
          <Badge variant="outline">访问 {exp.visit_count}</Badge>
          <span className="text-xs text-muted-foreground">{formatDate(exp.created_at)}</span>
          {exp.extraction_status !== "ok" ? (
            <Badge className="border-amber-500/25 bg-amber-50 text-amber-700">
              extraction: {exp.extraction_status}
            </Badge>
          ) : null}
          {exp.sanitization_status !== "ok" ? (
            <Badge className="border-amber-500/25 bg-amber-50 text-amber-700">
              sanitize: {exp.sanitization_status}
            </Badge>
          ) : null}
          {(() => {
            const tags = tryParseJson<string[]>(exp.tags) ?? [];
            return tags.map((t) => (
              <Badge key={t} className="border-border/70 bg-background/35 text-muted-foreground">
                #{t}
              </Badge>
            ));
          })()}
          <Link
            href={`/rewards/${id}`}
            className="ml-auto inline-flex items-center gap-1 rounded-md border border-amber-500/30 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-900 hover:bg-amber-100"
          >
            <Award className="h-3.5 w-3.5" />
            奖励标注
            <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </div>
      </div>

      <div className="border-b">
        <nav className="flex gap-1 overflow-x-auto">
          {TABS.map((t) => {
            const params = new URLSearchParams();
            if (t.key !== "card") params.set("tab", t.key);
            const href = `/experiences/${id}${params.toString() ? `?${params.toString()}` : ""}`;
            const active = tab === t.key;
            const Icon = t.icon;
            return (
              <Link
                key={t.key}
                href={href}
                className={`inline-flex items-center gap-1.5 rounded-t-md border border-b-0 px-3 py-2 text-sm whitespace-nowrap transition ${
                  active
                    ? "bg-card border-border text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground hover:bg-muted"
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                {t.label}
              </Link>
            );
          })}
        </nav>
      </div>

      {tab === "card" ? (
        <CardTab experience={exp} reward={reward} updates={updates} />
      ) : null}

      {tab === "trajectory" ? (
        <TrajectoryTab trajectory={trajectory} path={exp.trajectory_path} />
      ) : null}

      {tab === "lineage" ? (
        <LineageTab
          experience={exp}
          parents={parents}
          children={children}
          nodes={lineageNodes}
        />
      ) : null}

      {tab === "skills" ? <SkillsTab experienceId={id} /> : null}

      {tab === "audit" ? <AuditTab audits={audits} /> : null}

      <ActionBar id={id} experience={exp} returnTo={currentPath} />
    </div>
  );
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return { title: `经验 ${id.slice(0, 8)}` };
}
