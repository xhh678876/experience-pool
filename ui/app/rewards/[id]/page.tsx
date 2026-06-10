import { notFound } from "next/navigation";
import Link from "@/components/ui/link";
import { Award, ArrowLeft, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { getExperience, getTurnRewards } from "@/lib/queries";
import { formatDate, shortId } from "@/lib/utils";

export const dynamic = "force-dynamic";

const DIM_LABELS: { key: "r_outcome" | "r_intent" | "r_execution" | "r_orchestration" | "r_expression"; label: string; weight: number }[] = [
  { key: "r_outcome", label: "outcome", weight: 0.35 },
  { key: "r_intent", label: "intent", weight: 0.20 },
  { key: "r_execution", label: "execution", weight: 0.20 },
  { key: "r_orchestration", label: "orchestration", weight: 0.10 },
  { key: "r_expression", label: "expression", weight: 0.15 },
];

export default async function RewardDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const exp = await getExperience(id);
  if (!exp) notFound();
  const rewards = getTurnRewards(id);

  // Group rewards by judge_model
  const byJudge = new Map<string, typeof rewards>();
  for (const r of rewards) {
    const list = byJudge.get(r.judge_model) ?? [];
    list.push(r);
    byJudge.set(r.judge_model, list);
  }

  return (
    <div className="flex flex-col gap-6 pb-12">
      {/* Header */}
      <section className="flex flex-col gap-3 rounded-2xl border border-border/60 bg-white/85 px-5 py-4">
        <div className="flex items-center gap-2 text-xs">
          <Link href="/rewards" className="text-cyan-700 hover:underline">
            <ArrowLeft className="inline h-3.5 w-3.5" /> 返回
          </Link>
          <span className="text-muted-foreground">/</span>
          <Award className="h-3.5 w-3.5 text-amber-700" />
          <span className="font-mono text-[11px] text-muted-foreground">{shortId(id)}</span>
        </div>
        <div>
          <h1 className="text-lg font-semibold">{exp.intent_text || "(no intent)"}</h1>
          <p className="mt-1 text-xs text-muted-foreground">
            <Badge variant="outline" className="mr-2 font-mono">{exp.task_type}</Badge>
            <Badge variant="outline" className="mr-2 font-mono">{exp.acl}</Badge>
            <span>{formatDate(exp.created_at)}</span>
            <span className="mx-2">·</span>
            <Link href={`/experiences/${id}`} className="text-cyan-700 hover:underline">
              查看完整 experience →
            </Link>
          </p>
        </div>
      </section>

      {rewards.length === 0 ? (
        <section className="rounded-2xl border border-dashed border-border/60 bg-muted/30 px-6 py-10 text-center">
          <Sparkles className="mx-auto mb-3 h-6 w-6 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">这条 experience 还没有奖励标注。</p>
          <pre className="mx-auto mt-3 inline-block rounded-md border border-border/60 bg-white/70 px-3 py-2 text-left font-mono text-xs">
{`exp annotate-existing --experience-id ${shortId(id)} \\
    --session <local-id> --source claude-code \\
    --annotate-model claude-haiku-4-5`}
          </pre>
        </section>
      ) : (
        <>
          {/* Per-judge sections */}
          {[...byJudge.entries()].map(([judge, list]) => {
            const summary = computeSummary(list);
            return (
              <section
                key={judge}
                className="rounded-2xl border border-border/60 bg-white/85"
              >
                <div className="flex items-center gap-2 border-b border-border/60 px-5 py-3">
                  <Sparkles className="h-4 w-4 text-cyan-700" />
                  <span className="font-mono text-sm">{judge}</span>
                  <Badge variant="outline" className="font-mono">{list[0]?.judge_backend}</Badge>
                  <span className="ml-auto text-xs text-muted-foreground">
                    {list.length} turn 标注 · 平均 confidence {summary.confidence_mean.toFixed(2)}
                  </span>
                </div>

                {/* Summary bars */}
                <div className="grid gap-2 border-b border-border/60 px-5 py-3 md:grid-cols-5">
                  {DIM_LABELS.map((d) => (
                    <div key={d.key} className="flex flex-col gap-1">
                      <div className="flex items-baseline justify-between text-[11px]">
                        <span className="font-mono text-muted-foreground">
                          {d.label} <span className="text-muted-foreground/60">×{d.weight}</span>
                        </span>
                        <span className={`font-mono ${
                          summary.mean[d.key] >= 0.5 ? "text-emerald-700"
                          : summary.mean[d.key] >= 0 ? "text-cyan-700"
                          : summary.mean[d.key] >= -0.5 ? "text-amber-700"
                          : "text-rose-700"
                        }`}>
                          {summary.mean[d.key].toFixed(2)}
                        </span>
                      </div>
                      <DimBar value={summary.mean[d.key]} />
                    </div>
                  ))}
                </div>

                <div className="border-b border-border/60 bg-muted/30 px-5 py-2 text-xs text-muted-foreground">
                  weighted trajectory_score:{" "}
                  <span className={`font-mono font-semibold ${
                    summary.trajectory_score >= 0.5 ? "text-emerald-700"
                    : summary.trajectory_score >= 0 ? "text-cyan-700"
                    : summary.trajectory_score >= -0.5 ? "text-amber-700"
                    : "text-rose-700"
                  }`}>
                    {summary.trajectory_score.toFixed(3)}
                  </span>
                </div>

                {/* Per-turn rows */}
                <ul className="divide-y divide-border/60">
                  {list.map((r) => (
                    <li key={`${r.turn_index}-${r.judge_model}`} className="px-5 py-3">
                      <div className="flex flex-wrap items-center gap-2 text-xs">
                        <span className="rounded bg-muted/60 px-1.5 py-0.5 font-mono text-muted-foreground">
                          turn {r.turn_index}
                        </span>
                        {r.user_turn_index !== null ? (
                          <span className="text-muted-foreground">
                            ↤ user #{r.user_turn_index}
                          </span>
                        ) : null}
                        <Badge className={confColor(r.confidence)}>
                          conf {r.confidence.toFixed(2)}
                        </Badge>
                        <span className="ml-auto font-mono text-[10px] text-muted-foreground">
                          {formatDate(r.annotated_at)} by {r.annotated_by || "unknown"}
                        </span>
                      </div>
                      <div className="mt-2 grid gap-2 md:grid-cols-5">
                        {DIM_LABELS.map((d) => (
                          <DimChip
                            key={d.key}
                            label={d.label}
                            value={r[d.key] as number}
                          />
                        ))}
                      </div>
                      {r.reason ? (
                        <p className="mt-2 text-xs italic text-muted-foreground">
                          “{r.reason}”
                        </p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </>
      )}
    </div>
  );
}

function computeSummary(rows: ReturnType<typeof getTurnRewards>) {
  const n = rows.length || 1;
  const mean = {
    r_outcome: rows.reduce((s, r) => s + r.r_outcome, 0) / n,
    r_intent: rows.reduce((s, r) => s + r.r_intent, 0) / n,
    r_execution: rows.reduce((s, r) => s + r.r_execution, 0) / n,
    r_orchestration: rows.reduce((s, r) => s + r.r_orchestration, 0) / n,
    r_expression: rows.reduce((s, r) => s + r.r_expression, 0) / n,
  };
  const trajectory_score =
    mean.r_outcome * 0.35 +
    mean.r_intent * 0.20 +
    mean.r_execution * 0.20 +
    mean.r_orchestration * 0.10 +
    mean.r_expression * 0.15;
  const confidence_mean = rows.reduce((s, r) => s + r.confidence, 0) / n;
  return { mean, trajectory_score, confidence_mean };
}

function DimBar({ value }: { value: number }) {
  // value in [-1, 1]; render as a centered bar.
  const pct = Math.abs(value) * 50;
  const pos = value >= 0;
  return (
    <div className="relative h-2 w-full overflow-hidden rounded-full bg-muted/50">
      <div className="absolute left-1/2 top-0 h-full w-px bg-border/80" />
      <div
        className={`absolute top-0 h-full ${pos ? "bg-emerald-500" : "bg-rose-500"}`}
        style={
          pos
            ? { left: "50%", width: `${pct}%` }
            : { right: "50%", width: `${pct}%` }
        }
      />
    </div>
  );
}

function DimChip({ label, value }: { label: string; value: number }) {
  const color =
    value === 1 ? "bg-emerald-100 text-emerald-900 border-emerald-500/30"
    : value === 0 ? "bg-cyan-50 text-cyan-900 border-cyan-500/30"
    : "bg-rose-100 text-rose-900 border-rose-500/30";
  const sign = value > 0 ? "+1" : value < 0 ? "−1" : "0";
  return (
    <div className={`rounded-md border px-2 py-1.5 text-center ${color}`}>
      <div className="text-[10px] uppercase tracking-wider opacity-70">{label}</div>
      <div className="font-mono text-sm font-semibold">{sign}</div>
    </div>
  );
}

function confColor(c: number): string {
  if (c >= 0.7) return "bg-emerald-100 text-emerald-900";
  if (c >= 0.4) return "bg-cyan-100 text-cyan-900";
  return "bg-amber-100 text-amber-900";
}
