import { notFound } from "next/navigation";
import Link from "next/link";
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

type SearchParams = { tab?: string };

const TABS = [
  { key: "card", label: "Card" },
  { key: "trajectory", label: "Trajectory" },
  { key: "lineage", label: "Lineage" },
  { key: "skills", label: "Skills" },
  { key: "audit", label: "Audit" },
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
  const tab = (sp.tab ?? "card") as (typeof TABS)[number]["key"];

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

  return (
    <div className="space-y-6 pb-32">
      <div className="space-y-3">
        <div className="text-xs text-muted-foreground">
          <Link href="/experiences" className="hover:underline">
            Experiences
          </Link>
          {" / "}
          <span className="font-mono">{id}</span>
        </div>
        <h1 className="text-2xl font-semibold tracking-tight">
          {exp.intent_text ?? "(no intent extracted)"}
        </h1>
        <div className="flex flex-wrap gap-2 items-center">
          <Badge className={statusColor(exp.review_status)}>{exp.review_status}</Badge>
          <Badge className={sensitivityColor(exp.sensitivity)}>
            sensitivity: {exp.sensitivity}
          </Badge>
          <Badge>task: {exp.task_type}</Badge>
          <Badge>model: {exp.source_model}</Badge>
          <Badge>q: {exp.q_scalar.toFixed(2)}</Badge>
          <Badge>reuse: {exp.reuse_count}</Badge>
          <Badge>visits: {exp.visit_count}</Badge>
          <Badge>created: {formatDate(exp.created_at)}</Badge>
          {exp.extraction_status !== "ok" ? (
            <Badge className="border-amber-300 bg-amber-50 text-amber-900">
              extraction: {exp.extraction_status}
            </Badge>
          ) : null}
          {exp.sanitization_status !== "ok" ? (
            <Badge className="border-amber-300 bg-amber-50 text-amber-900">
              sanitize: {exp.sanitization_status}
            </Badge>
          ) : null}
          {(() => {
            const tags = tryParseJson<string[]>(exp.tags) ?? [];
            return tags.map((t) => (
              <Badge key={t} className="border-slate-300 bg-slate-50 text-slate-900">
                #{t}
              </Badge>
            ));
          })()}
        </div>
      </div>

      <div className="border-b">
        <nav className="flex gap-1">
          {TABS.map((t) => {
            const params = new URLSearchParams();
            if (t.key !== "card") params.set("tab", t.key);
            const href = `/experiences/${id}${params.toString() ? `?${params.toString()}` : ""}`;
            const active = tab === t.key;
            return (
              <Link
                key={t.key}
                href={href}
                className={`px-4 py-2 text-sm rounded-t-md border border-b-0 ${
                  active
                    ? "bg-card border-border"
                    : "border-transparent text-muted-foreground hover:text-foreground hover:bg-muted"
                }`}
              >
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

      <ActionBar id={id} experience={exp} />
    </div>
  );
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return { title: `Experience ${id.slice(0, 8)}` };
}
