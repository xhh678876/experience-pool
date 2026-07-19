import { notFound } from "next/navigation";
import Link from "@/components/ui/link";
import { ArrowLeft, Layers3, Sparkles, ListOrdered, Boxes } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  getCluster,
  getClusterMembers,
  getCrystallizedSkillByCluster,
} from "@/lib/queries";
import { formatDate, shortId } from "@/lib/utils";

export const dynamic = "force-dynamic";

type SkillContent = {
  name?: string;
  trigger?: string;
  steps?: { step: string; why: string; how: string }[];
  edge_cases?: string[];
  branches?: string[];
  structure_score?: number;
  quality?: string;
};

export default async function ClusterDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const cluster = await getCluster(id);
  if (!cluster) notFound();
  const members = await getClusterMembers(id);
  const skill = await getCrystallizedSkillByCluster(id);

  let skillContent: SkillContent | null = null;
  if (skill) {
    try {
      skillContent = JSON.parse(skill.content);
    } catch {}
  }

  return (
    <div className="flex flex-col gap-6 pb-12">
      <section className="rounded-2xl border border-border/60 bg-gradient-to-br from-cyan-50 via-white to-amber-50/40 px-5 py-4">
        <div className="flex items-center gap-2 text-xs">
          <Link href="/clusters" className="text-cyan-700 hover:underline">
            <ArrowLeft className="inline h-3.5 w-3.5" /> 返回
          </Link>
          <span className="text-muted-foreground">/</span>
          <Layers3 className="h-3.5 w-3.5" />
          <span className="font-mono text-[11px] text-muted-foreground">
            {shortId(id)}
          </span>
        </div>
        <h1 className="mt-1 text-xl font-semibold">
          {cluster.label || "(unlabeled cluster)"}
        </h1>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
          <Badge className="bg-cyan-100 text-cyan-900 font-mono">
            {cluster.member_count} 成员
          </Badge>
          {cluster.crystallized_skill_id ? (
            <Badge className="border-amber-500/30 bg-amber-50 text-amber-900">
              <Sparkles className="mr-0.5 inline h-3 w-3" />
              已结晶 v{skill?.version || 1} · score {cluster.last_structure_score?.toFixed(2)}
            </Badge>
          ) : cluster.member_count >= 5 ? (
            <Badge className="bg-amber-100 text-amber-900">
              待结晶
            </Badge>
          ) : (
            <Badge variant="outline">
              还需 {5 - cluster.member_count} 个成员才能结晶
            </Badge>
          )}
          {cluster.new_since_crystallize > 0 && cluster.crystallized_skill_id ? (
            <Badge className="bg-cyan-100 text-cyan-900">
              +{cluster.new_since_crystallize} 自上次结晶
            </Badge>
          ) : null}
          <span className="text-muted-foreground">
            创建 {formatDate(cluster.created_at)}
          </span>
          <span className="text-muted-foreground">·</span>
          <span className="text-muted-foreground">
            最近更新 {formatDate(cluster.updated_at)}
          </span>
        </div>
      </section>

      {/* Crystallized skill */}
      {skillContent ? (
        <section className="rounded-2xl border border-amber-500/30 bg-white/85">
          <div className="flex items-center gap-2 border-b border-border/60 px-5 py-3">
            <Sparkles className="h-4 w-4 text-amber-700" />
            <span className="font-semibold">结晶技能 · {skill?.name}</span>
            <Badge variant="outline" className="font-mono">
              v{skill?.version}
            </Badge>
            <Badge
              className={
                (skill?.structure_score || 0) >= 0.7
                  ? "bg-emerald-100 text-emerald-900"
                  : "bg-cyan-100 text-cyan-900"
              }
            >
              score {(skill?.structure_score || 0).toFixed(2)}
            </Badge>
            <span className="ml-auto text-xs text-muted-foreground">
              {skill?.member_count} 个经验综合 · {skill ? formatDate(skill.created_at) : "—"}
            </span>
          </div>
          {skillContent.trigger ? (
            <div className="border-b border-border/60 px-5 py-3">
              <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                何时使用
              </div>
              <p className="text-sm">{skillContent.trigger}</p>
            </div>
          ) : null}
          {skillContent.steps && skillContent.steps.length > 0 ? (
            <div className="border-b border-border/60 px-5 py-3">
              <div className="mb-2 flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                <ListOrdered className="h-3 w-3" />
                {skillContent.steps.length} 步 workflow
              </div>
              <ol className="flex flex-col gap-2">
                {skillContent.steps.map((s, i) => (
                  <li key={i} className="rounded-md border border-border/60 bg-muted/20 px-3 py-2 text-sm">
                    <div className="font-medium">
                      <span className="mr-2 inline-flex h-5 w-5 items-center justify-center rounded bg-cyan-100 font-mono text-[11px] text-cyan-900">
                        {i + 1}
                      </span>
                      {s.step}
                    </div>
                    {s.why ? (
                      <div className="mt-1 ml-7 text-xs text-muted-foreground">
                        <span className="font-mono text-[10px] uppercase">why:</span> {s.why}
                      </div>
                    ) : null}
                    {s.how ? (
                      <div className="mt-0.5 ml-7 text-xs">
                        <span className="font-mono text-[10px] uppercase text-muted-foreground">how:</span> {s.how}
                      </div>
                    ) : null}
                  </li>
                ))}
              </ol>
            </div>
          ) : null}
          {skillContent.edge_cases && skillContent.edge_cases.length > 0 ? (
            <div className="border-b border-border/60 px-5 py-3">
              <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                Edge cases
              </div>
              <ul className="ml-4 list-disc text-sm space-y-0.5">
                {skillContent.edge_cases.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {skillContent.branches && skillContent.branches.length > 0 ? (
            <div className="px-5 py-3">
              <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                Branches
              </div>
              <ul className="ml-4 list-disc text-sm space-y-0.5">
                {skillContent.branches.map((b, i) => (
                  <li key={i}>{b}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </section>
      ) : null}

      {/* Members */}
      <section className="rounded-2xl border border-border/60 bg-white/85">
        <div className="flex items-center gap-2 border-b border-border/60 px-5 py-3">
          <Boxes className="h-4 w-4 text-cyan-700" />
          <span className="font-semibold">成员经验 ({members.length})</span>
          <span className="text-muted-foreground text-xs">
            · 按余弦相似度降序
          </span>
        </div>
        {members.length === 0 ? (
          <p className="px-5 py-6 text-sm text-muted-foreground">
            (no members — 数据可能在迁移中)
          </p>
        ) : (
          <ul className="divide-y divide-border/60">
            {members.map((m) => (
              <li key={m.experience_id} className="px-5 py-2.5">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <Link
                    href={`/experiences/${m.experience_id}`}
                    className="font-mono text-[11px] text-cyan-700 hover:underline"
                  >
                    {shortId(m.experience_id)}
                  </Link>
                  <Badge variant="outline" className="font-mono">
                    {m.task_type}
                  </Badge>
                  <Badge
                    className={
                      m.similarity >= 0.9
                        ? "bg-emerald-100 text-emerald-900 font-mono"
                        : m.similarity >= 0.75
                        ? "bg-cyan-100 text-cyan-900 font-mono"
                        : "bg-amber-100 text-amber-900 font-mono"
                    }
                  >
                    sim {(m.similarity * 100).toFixed(0)}%
                  </Badge>
                  {m.trajectory_score !== null ? (
                    <Badge
                      className={
                        m.trajectory_score >= 0.4
                          ? "bg-emerald-100 text-emerald-900 font-mono"
                          : m.trajectory_score >= 0
                          ? "bg-cyan-100 text-cyan-900 font-mono"
                          : "bg-rose-100 text-rose-900 font-mono"
                      }
                    >
                      score {m.trajectory_score >= 0 ? "+" : ""}
                      {m.trajectory_score.toFixed(2)}
                    </Badge>
                  ) : null}
                  <span className="ml-auto text-muted-foreground">
                    {formatDate(m.added_at)}
                  </span>
                </div>
                <Link
                  href={`/experiences/${m.experience_id}`}
                  className="mt-0.5 block truncate text-sm hover:text-cyan-800 hover:underline"
                >
                  {m.intent_text || "(no intent)"}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
