import Link from "@/components/ui/link";
import { Layers3, Sparkles, ArrowRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { listClusters, getCrystalStats } from "@/lib/queries";
import { formatDate, shortId } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function ClustersIndexPage() {
  const clusters = listClusters(200);
  const stats = getCrystalStats();

  return (
    <div className="flex flex-col gap-6 pb-12">
      <section className="flex flex-col gap-2 rounded-2xl border border-border/60 bg-white/85 px-5 py-4">
        <div className="flex items-center gap-2 text-sm">
          <Layers3 className="h-4 w-4 text-cyan-700" />
          <span className="font-semibold">经验簇 + 结晶</span>
          <span className="text-muted-foreground">
            · 每条 memory_eligible experience 自动按语义归簇 · 簇 ≥5 成员 → LLM 综合 multi-step workflow skill
          </span>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Tile label="总簇数" value={stats.clusters} />
          <Tile label="已结晶" value={stats.crystallized} />
          <Tile label="平均成员" value={stats.avg_members} />
          <Tile label="结晶技能" value={stats.skills} />
        </div>
      </section>

      {clusters.length === 0 ? (
        <section className="rounded-2xl border border-dashed border-border/60 bg-muted/30 px-6 py-10 text-center">
          <Layers3 className="mx-auto h-8 w-8 text-muted-foreground" />
          <p className="mt-3 text-sm text-muted-foreground">暂无簇</p>
        </section>
      ) : (
        <section className="rounded-2xl border border-border/60 bg-white/85">
          <div className="flex items-center gap-2 border-b border-border/60 px-5 py-3 text-sm">
            <span className="font-semibold">{clusters.length}</span>
            <span className="text-muted-foreground"> 个簇 · 按成员数降序</span>
          </div>
          <ul className="divide-y divide-border/60">
            {clusters.map((c) => {
              const isCrystallized = c.crystallized_skill_id != null;
              return (
                <li key={c.cluster_id} className="px-5 py-3">
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <Link
                      href={`/clusters/${c.cluster_id}`}
                      className="font-mono text-[11px] text-cyan-700 hover:underline"
                    >
                      {shortId(c.cluster_id)}
                    </Link>
                    <Badge
                      className={
                        c.member_count >= 5
                          ? "bg-emerald-100 text-emerald-900 font-mono"
                          : "bg-cyan-100 text-cyan-900 font-mono"
                      }
                    >
                      {c.member_count} 成员
                    </Badge>
                    {isCrystallized ? (
                      <Badge className="border-amber-500/30 bg-amber-50 text-amber-900 font-mono">
                        <Sparkles className="mr-0.5 inline h-3 w-3" />
                        已结晶 score={c.last_structure_score?.toFixed(2)}
                      </Badge>
                    ) : c.member_count >= 5 ? (
                      <Badge className="bg-amber-100 text-amber-900 font-mono">
                        待结晶
                      </Badge>
                    ) : null}
                    {c.new_since_crystallize > 0 && isCrystallized ? (
                      <Badge className="bg-cyan-100 text-cyan-900 font-mono">
                        +{c.new_since_crystallize} 自上次
                      </Badge>
                    ) : null}
                    <span className="ml-auto text-muted-foreground">
                      {formatDate(c.updated_at)}
                    </span>
                  </div>
                  <Link
                    href={`/clusters/${c.cluster_id}`}
                    className="mt-1 flex items-center gap-2 text-sm font-medium hover:text-cyan-800 hover:underline"
                  >
                    {c.label || "(unlabeled cluster)"}
                    <ArrowRight className="h-3.5 w-3.5 opacity-50" />
                  </Link>
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}

function Tile({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-lg border border-border/60 bg-muted/30 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="font-mono text-xl font-semibold">{value}</div>
    </div>
  );
}
