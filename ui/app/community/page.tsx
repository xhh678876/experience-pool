import Link from "@/components/ui/link";
import {
  Globe2,
  Lock,
  Sparkles,
  Users,
  TrendingUp,
  ArrowRight,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { getReviewerName } from "@/lib/auth";
import { getOwnerQuota } from "@/lib/me-queries";
import {
  listCommunityExperiences,
  communityStats,
} from "@/lib/community-queries";
import { formatDate, shortId } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function CommunityPage() {
  const viewer = await getReviewerName();
  const quota = await getOwnerQuota(viewer);
  const stats = await communityStats();

  // Always fetch (cheap) so we can show counts even when locked.
  const rows = quota.community_unlocked
    ? await listCommunityExperiences(200)
    : [];

  return (
    <div className="flex flex-col gap-6 pb-12">
      <section className="rounded-2xl border border-border/60 bg-gradient-to-br from-cyan-50 via-white to-amber-50/40 px-5 py-4">
        <div className="flex items-center gap-2 text-sm">
          <Globe2 className="h-4 w-4 text-cyan-700" />
          <span className="font-semibold">社区池</span>
          <span className="text-muted-foreground">
            · 由 {stats.contributors} 位贡献者 · {stats.total_published} 条已发布经验
          </span>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Tile label="已发布总数" value={stats.total_published} />
          <Tile label="贡献者" value={stats.contributors} />
          <Tile
            label="近 7 天"
            value={`+${stats.recent_7d}`}
            highlight={stats.recent_7d > 0}
          />
        </div>
      </section>

      {!quota.community_unlocked ? (
        <section className="rounded-2xl border border-amber-500/30 bg-amber-50/40 px-6 py-8 text-center">
          <Lock className="mx-auto h-10 w-10 text-amber-700" />
          <h2 className="mt-3 text-base font-semibold">社区池未解锁</h2>
          <p className="mt-2 text-sm text-amber-900">
            想看其他贡献者的经验，请先发布
            <span className="mx-1 font-semibold text-amber-700">
              {quota.threshold - quota.publish_count}
            </span>
            条自己的经验到社区池。
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            发布前会跑严格脱敏（屏蔽 file:// / 本地路径 / localhost / UUID 等）
          </p>
          <div className="mx-auto mt-4 max-w-sm">
            <div className="mb-1 flex items-center justify-between text-xs">
              <span className="text-muted-foreground">进度</span>
              <span className="font-mono">
                {quota.publish_count} / {quota.threshold}
              </span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-muted/60">
              <div
                className="h-full bg-amber-500 transition-all"
                style={{
                  width: `${Math.min(100, (quota.publish_count / quota.threshold) * 100)}%`,
                }}
              />
            </div>
          </div>
          <Link
            href="/me"
            className="mt-5 inline-flex items-center gap-1 rounded-md border border-cyan-500/40 bg-cyan-50 px-3 py-1.5 text-xs font-medium text-cyan-900 hover:bg-cyan-100"
          >
            前往我的经验池发布 <ArrowRight className="h-3 w-3" />
          </Link>
        </section>
      ) : (
        <>
          <section className="rounded-md border border-emerald-500/30 bg-emerald-50/60 px-4 py-2.5 text-xs text-emerald-900">
            <span className="inline-flex items-center gap-1.5 font-medium">
              <Sparkles className="h-3.5 w-3.5" />
              社区池已解锁
            </span>
            <span className="ml-2">
              · 你已贡献 {quota.publish_count} 条 · 现在可以浏览其他人发布的经验
            </span>
          </section>
          {rows.length === 0 ? (
            <section className="rounded-2xl border border-dashed border-border/60 bg-muted/30 px-6 py-10 text-center">
              <Globe2 className="mx-auto h-8 w-8 text-muted-foreground" />
              <p className="mt-3 text-sm text-muted-foreground">
                还没有人发布过经验
              </p>
            </section>
          ) : (
            <section className="rounded-2xl border border-border/60 bg-white/85">
              <div className="border-b border-border/60 px-4 py-3 text-sm">
                <span className="font-semibold">{rows.length}</span>
                <span className="text-muted-foreground"> 条 · 按发布时间倒序</span>
              </div>
              <ul className="divide-y divide-border/60">
                {rows.map((r) => (
                  <li key={r.experience_id} className="px-4 py-3">
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <Link
                        href={`/experiences/${r.experience_id}`}
                        className="font-mono text-[11px] text-cyan-700 hover:underline"
                      >
                        {shortId(r.experience_id)}
                      </Link>
                      <Badge variant="outline" className="font-mono">
                        {r.task_type}
                      </Badge>
                      <Badge className="bg-emerald-100 text-emerald-900 font-mono text-[10px]">
                        <Globe2 className="mr-0.5 inline h-3 w-3" />
                        public
                      </Badge>
                      <span className="inline-flex items-center gap-1 text-muted-foreground">
                        <Users className="h-3 w-3" />
                        <code className="font-mono">{r.agent_owner}</code>
                      </span>
                      {r.is_memory_eligible ? (
                        <Badge className="bg-cyan-100 text-cyan-900 font-mono text-[10px]">
                          memory
                        </Badge>
                      ) : null}
                      {r.trajectory_score !== null ? (
                        <Badge
                          className={
                            r.trajectory_score >= 0.4
                              ? "bg-emerald-100 text-emerald-900 font-mono text-[10px]"
                              : "bg-cyan-100 text-cyan-900 font-mono text-[10px]"
                          }
                        >
                          score {r.trajectory_score >= 0 ? "+" : ""}
                          {r.trajectory_score.toFixed(2)}
                        </Badge>
                      ) : null}
                      <span className="ml-auto text-muted-foreground">
                        {r.published_at ? formatDate(r.published_at) : ""}
                      </span>
                    </div>
                    <Link
                      href={`/experiences/${r.experience_id}`}
                      className="mt-1 line-clamp-2 block text-sm hover:text-cyan-800 hover:underline"
                    >
                      {r.intent_text || r.query || "(no intent)"}
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}

function Tile({
  label,
  value,
  highlight,
}: {
  label: string;
  value: number | string;
  highlight?: boolean;
}) {
  return (
    <div
      className={
        highlight
          ? "rounded-lg border border-emerald-500/30 bg-emerald-50/70 px-3 py-2"
          : "rounded-lg border border-border/60 bg-muted/30 px-3 py-2"
      }
    >
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="font-mono text-xl font-semibold">{value}</div>
    </div>
  );
}
