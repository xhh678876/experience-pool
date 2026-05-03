import Link from "@/components/ui/link";
import { Award, ArrowRight, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { listExperiencesWithRewards } from "@/lib/queries";
import { formatDate, shortId } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function RewardsIndexPage() {
  const items = listExperiencesWithRewards(60);

  return (
    <div className="flex flex-col gap-6 pb-12">
      <section className="flex flex-col gap-3 rounded-2xl border border-border/60 bg-white/85 px-5 py-4">
        <div className="flex items-center gap-2 text-sm">
          <Award className="h-4 w-4 text-amber-700" />
          <span className="font-semibold">奖励标注</span>
          <span className="text-muted-foreground">
            · synergy 5 维 × {`{-1, 0, +1}`} + confidence + reason · 按 (experience, judge_model) 复合主键存储
          </span>
        </div>
        <p className="text-xs leading-5 text-muted-foreground">
          已标注的 experience 列表。点进单条看每个 turn 的 5 维分布、judge 模型、reason、置信度。
          权重：outcome 0.35 / intent 0.20 / execution 0.20 / orchestration 0.10 / expression 0.15。
        </p>
      </section>

      {items.length === 0 ? (
        <section className="rounded-2xl border border-dashed border-border/60 bg-muted/30 px-6 py-10 text-center">
          <Sparkles className="mx-auto mb-3 h-6 w-6 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            暂无奖励标注记录。运行下面命令对一条 experience 标分：
          </p>
          <pre className="mx-auto mt-3 inline-block rounded-md border border-border/60 bg-white/70 px-3 py-2 text-left font-mono text-xs">
{`exp annotate-existing --experience-id <eid> \\
    --session <local-id> --source claude-code \\
    --annotate-backend claude --annotate-model claude-haiku-4-5`}
          </pre>
        </section>
      ) : (
        <section className="rounded-2xl border border-border/60 bg-white/85">
          <div className="border-b border-border/60 px-5 py-3 text-sm">
            <span className="font-semibold">{items.length}</span>
            <span className="text-muted-foreground"> 条已标注</span>
          </div>
          <ul className="divide-y divide-border/60">
            {items.map((item) => (
              <li key={item.experience_id} className="px-5 py-3">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <Link
                    href={`/rewards/${item.experience_id}`}
                    className="font-mono text-[11px] text-cyan-700 hover:underline"
                  >
                    {shortId(item.experience_id)}
                  </Link>
                  <Badge variant="outline" className="font-mono">{item.task_type}</Badge>
                  <Badge className={scoreBadgeColor(item.trajectory_score)}>
                    score {item.trajectory_score?.toFixed(2) ?? "—"}
                  </Badge>
                  <span className="text-muted-foreground">
                    {item.reward_count} turn 标注
                  </span>
                  <span className="text-muted-foreground">·</span>
                  <span className="text-muted-foreground font-mono text-[11px]">
                    {(item.judge_models || "").split(",").join(" · ")}
                  </span>
                  <span className="text-muted-foreground">·</span>
                  <span className="text-muted-foreground">{formatDate(item.created_at)}</span>
                </div>
                <Link
                  href={`/rewards/${item.experience_id}`}
                  className="mt-1 flex items-center gap-2 text-sm font-medium hover:text-cyan-800 hover:underline"
                >
                  {item.intent_text || "(no intent)"}
                  <ArrowRight className="h-3.5 w-3.5 opacity-50" />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function scoreBadgeColor(score: number | null): string {
  if (score === null || score === undefined) return "bg-muted text-muted-foreground";
  if (score >= 0.5) return "bg-emerald-100 text-emerald-900";
  if (score >= 0) return "bg-cyan-100 text-cyan-900";
  if (score >= -0.5) return "bg-amber-100 text-amber-900";
  return "bg-rose-100 text-rose-900";
}
