import Link from "@/components/ui/link";
import {
  ArrowRight,
  Award,
  BarChart3,
  CheckCircle2,
  Clock3,
  Gauge,
  MessageSquare,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  TrendingUp,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  getReuseRewardDashboard,
  listExperiencesWithRewards,
  type ReuseRewardDashboard,
  type ReuseRewardEventRow,
  type ReuseRewardExperienceRow,
  type ReuseRewardItemRow,
  type ReuseRewardTimelinePoint,
} from "@/lib/queries";
import { formatDate, shortId } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function RewardsIndexPage() {
  const [dashboard, labeledItems] = await Promise.all([
    getReuseRewardDashboard(24),
    listExperiencesWithRewards(16),
  ]);
  const stats = dashboard.stats;
  const feedbackRate = stats.injected_items
    ? Math.round((stats.feedback_items / stats.injected_items) * 100)
    : 0;
  const usedRate = stats.feedback_items
    ? Math.round((stats.used_items / stats.feedback_items) * 100)
    : 0;

  return (
    <div className="flex flex-col gap-4 pb-12">
      <section className="rounded-lg border border-border/70 bg-white px-5 py-4 shadow-sm">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="inline-flex items-center gap-1.5 font-semibold text-foreground">
                <Award className="h-4 w-4 text-amber-700" />
                Q值排序
              </span>
              <Badge className="bg-cyan-50 text-cyan-800">reuse feedback</Badge>
              <Badge className="bg-emerald-50 text-emerald-800">Q EMA</Badge>
            </div>
            <p className="mt-2 max-w-4xl text-sm leading-6 text-muted-foreground">
              按在线反馈里的复用率和平均奖励排序经验，同时展示 Q 值更新、正负反馈和最近命中的片段。下方保留离线 turn_rewards 标注入口。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Clock3 className="h-3.5 w-3.5" />
            最近反馈 {stats.last_feedback_at ? formatDate(stats.last_feedback_at) : "暂无"}
          </div>
        </div>
      </section>

      <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <MetricCard
          icon={<Sparkles className="h-4 w-4" />}
          label="召回事件"
          value={stats.recall_events}
          detail={`${stats.injected_items} 个注入片段`}
        />
        <MetricCard
          icon={<MessageSquare className="h-4 w-4" />}
          label="反馈覆盖"
          value={`${feedbackRate}%`}
          detail={`${stats.feedback_items}/${stats.injected_items}`}
        />
        <MetricCard
          icon={<CheckCircle2 className="h-4 w-4" />}
          label="实际使用"
          value={`${usedRate}%`}
          detail={`${stats.used_items} used / ${stats.not_used_items} unused`}
        />
        <MetricCard
          icon={<TrendingUp className="h-4 w-4" />}
          label="Q 更新"
          value={stats.q_updates}
          detail={`${stats.experiences_touched} 条经验被触达`}
        />
        <MetricCard
          icon={<Gauge className="h-4 w-4" />}
          label="平均奖励"
          value={formatSigned(stats.avg_reward)}
          detail={`conf ${formatFixed(stats.avg_confidence)}`}
        />
      </section>

      <TopExperiencesPanel rows={dashboard.topExperiences} />

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
        <div className="flex flex-col gap-4">
          <div className="rounded-lg border border-border/70 bg-white shadow-sm">
            <div className="flex items-center gap-2 border-b border-border/60 px-4 py-3 text-sm">
              <BarChart3 className="h-4 w-4 text-cyan-700" />
              <span className="font-semibold">反馈走势</span>
              <span className="text-muted-foreground">· 最近 14 天</span>
            </div>
            <Timeline rows={dashboard.timeline} />
          </div>

          <div className="rounded-lg border border-border/70 bg-white shadow-sm">
            <div className="flex items-center gap-2 border-b border-border/60 px-4 py-3 text-sm">
              <ThumbsUp className="h-4 w-4 text-cyan-700" />
              <span className="font-semibold">最近反馈事件</span>
            </div>
            {dashboard.recentEvents.length === 0 ? (
              <EmptyState text="还没有在线召回反馈。插件调用 /expool:feedback 或自动反馈后会出现在这里。" />
            ) : (
              <ul className="divide-y divide-border/60">
                {dashboard.recentEvents.map((event) => (
                  <FeedbackEventRow key={event.event_id} event={event} />
                ))}
              </ul>
            )}
          </div>

          <div className="rounded-lg border border-border/70 bg-white shadow-sm">
            <div className="flex items-center gap-2 border-b border-border/60 px-4 py-3 text-sm">
              <Sparkles className="h-4 w-4 text-cyan-700" />
              <span className="font-semibold">最近被打分的片段</span>
            </div>
            {dashboard.recentItems.length === 0 ? (
              <EmptyState text="暂无片段级奖励。反馈会保留 rank、chunk_type、query 和 reason，方便回看召回为什么好或差。" />
            ) : (
              <ul className="divide-y divide-border/60">
                {dashboard.recentItems.map((item) => (
                  <FeedbackItemRow
                    key={`${item.event_id}:${item.experience_id}:${item.chunk_id}`}
                    item={item}
                  />
                ))}
              </ul>
            )}
          </div>
        </div>

        <aside className="flex flex-col gap-4">
          <DistributionPanel dashboard={dashboard} />
          <TurnRewardsPanel items={labeledItems} />
        </aside>
      </section>
    </div>
  );
}

function MetricCard({
  icon,
  label,
  value,
  detail,
}: {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
  detail: string;
}) {
  return (
    <div className="rounded-lg border border-border/70 bg-white px-4 py-3 shadow-sm">
      <div className="mb-2 flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          {icon}
          {label}
        </span>
      </div>
      <div className="font-mono text-2xl font-semibold text-foreground">{value}</div>
      <div className="mt-1 truncate text-xs text-muted-foreground" title={detail}>
        {detail}
      </div>
    </div>
  );
}

function DistributionPanel({ dashboard }: { dashboard: ReuseRewardDashboard }) {
  const rewardRows = [
    { label: "positive", value: dashboard.stats.positive_items, color: "bg-emerald-600" },
    { label: "neutral", value: dashboard.stats.neutral_items, color: "bg-cyan-600" },
    { label: "negative", value: dashboard.stats.negative_items, color: "bg-rose-600" },
  ];
  const qRows = dashboard.qDistribution.map((row) => ({
    label: row.bucket,
    value: row.count,
    color:
      row.bucket === ">=0.5"
        ? "bg-emerald-600"
        : row.bucket === "0.25..0.5"
          ? "bg-cyan-600"
          : row.bucket === "0..0.25"
            ? "bg-slate-500"
            : "bg-rose-500",
  }));

  return (
    <div className="rounded-lg border border-border/70 bg-white shadow-sm">
      <div className="flex items-center gap-2 border-b border-border/60 px-4 py-3 text-sm">
        <BarChart3 className="h-4 w-4 text-cyan-700" />
        <span className="font-semibold">分布</span>
      </div>
      <div className="space-y-5 px-4 py-4">
        <BarList title="奖励方向" rows={rewardRows} total={Math.max(1, dashboard.stats.feedback_items)} />
        <BarList
          title="可见经验 Q 值"
          rows={qRows}
          total={Math.max(1, dashboard.qDistribution.reduce((s, r) => s + r.count, 0))}
        />
      </div>
    </div>
  );
}

function BarList({
  title,
  rows,
  total,
}: {
  title: string;
  rows: { label: string; value: number; color: string }[];
  total: number;
}) {
  return (
    <div>
      <div className="mb-2 text-xs font-medium text-muted-foreground">{title}</div>
      <div className="space-y-2">
        {rows.map((row) => {
          const pct = Math.round((row.value / total) * 100);
          return (
            <div
              key={row.label}
              className="grid grid-cols-[86px_minmax(0,1fr)_44px] items-center gap-2 text-xs"
            >
              <span className="font-mono text-muted-foreground">{row.label}</span>
              <div className="h-2 overflow-hidden rounded-full bg-muted">
                <div className={`h-full ${row.color}`} style={{ width: `${pct}%` }} />
              </div>
              <span className="text-right font-mono text-muted-foreground">{row.value}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Timeline({ rows }: { rows: ReuseRewardTimelinePoint[] }) {
  const max = Math.max(1, ...rows.map((r) => Math.max(r.feedback_count, r.q_updates)));
  return (
    <div className="grid grid-cols-7 gap-2 px-4 py-4 md:grid-cols-[repeat(14,minmax(0,1fr))]">
      {rows.map((row) => {
        const feedbackPct = Math.max(6, Math.round((row.feedback_count / max) * 100));
        const qPct = Math.max(6, Math.round((row.q_updates / max) * 100));
        return (
          <div key={row.day} className="flex min-h-28 flex-col justify-end gap-1">
            <div className="flex h-20 items-end gap-1">
              <div
                className="w-1/2 rounded-t bg-cyan-600"
                style={{ height: row.feedback_count ? `${feedbackPct}%` : "2px" }}
                title={`feedback ${row.feedback_count}`}
              />
              <div
                className="w-1/2 rounded-t bg-emerald-600"
                style={{ height: row.q_updates ? `${qPct}%` : "2px" }}
                title={`q updates ${row.q_updates}`}
              />
            </div>
            <div className="truncate text-center font-mono text-[10px] text-muted-foreground">
              {row.day.slice(5)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function FeedbackEventRow({ event }: { event: ReuseRewardEventRow }) {
  return (
    <li className="px-4 py-3">
      <div className="mb-1 flex flex-wrap items-center gap-2 text-xs">
        <span className="font-mono text-[11px] text-muted-foreground">{shortId(event.event_id)}</span>
        <Badge variant="outline" className="font-mono">
          {event.scope}
        </Badge>
        <Badge className={statusBadge(event.final_status)}>{event.final_status}</Badge>
        <span className="text-muted-foreground">{event.viewer_name}</span>
        <span className="text-muted-foreground">· {formatDate(event.feedback_at ?? event.created_at)}</span>
      </div>
      <p className="line-clamp-2 text-sm font-medium leading-5">{event.query_text || "(empty query)"}</p>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>{event.feedback_count}/{event.item_count} 已反馈</span>
        <span>used {event.used_count}</span>
        <span>unused {event.not_used_count}</span>
        <span>reward {formatSigned(event.avg_reward)}</span>
        <span>conf {formatFixed(event.avg_confidence)}</span>
      </div>
    </li>
  );
}

function FeedbackItemRow({ item }: { item: ReuseRewardItemRow }) {
  return (
    <li className="px-4 py-3">
      <div className="mb-1 flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded bg-muted/70 px-1.5 py-0.5 font-mono text-muted-foreground">
          #{item.rank}
        </span>
        <Badge className={rewardBadge(item.reward)}>{formatSigned(item.reward)}</Badge>
        <Badge className={item.was_used_by_agent ? "bg-emerald-100 text-emerald-900" : "bg-slate-100 text-slate-800"}>
          {item.was_used_by_agent ? "used" : "not used"}
        </Badge>
        <Badge variant="outline" className="font-mono">
          {item.chunk_type ?? "chunk"}
        </Badge>
        <span className="text-muted-foreground">sim {Math.round((item.similarity ?? 0) * 100)}%</span>
        <span className="ml-auto text-muted-foreground">{item.feedback_at ? formatDate(item.feedback_at) : ""}</span>
      </div>
      <Link
        href={`/experiences/${item.experience_id}`}
        className="line-clamp-2 text-sm font-medium leading-5 hover:text-cyan-800 hover:underline"
      >
        {item.intent_text || shortId(item.experience_id)}
      </Link>
      <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">{item.query_text}</p>
      {item.feedback_reason ? (
        <p className="mt-2 rounded-md border border-border/60 bg-muted/30 px-2 py-1.5 text-xs leading-5 text-muted-foreground">
          {item.feedback_reason}
        </p>
      ) : null}
    </li>
  );
}

function TopExperiencesPanel({ rows }: { rows: ReuseRewardExperienceRow[] }) {
  return (
    <div className="rounded-lg border border-border/70 bg-white shadow-sm">
      <div className="flex items-center gap-2 border-b border-border/60 px-4 py-3 text-sm">
        <TrendingUp className="h-4 w-4 text-cyan-700" />
              <span className="font-semibold">Q值排序</span>
              <span className="text-muted-foreground">· 复用率/奖励按反馈证据收缩 + 20%Q值 + 10%reuse_count</span>
      </div>
      {rows.length === 0 ? (
        <EmptyState text="暂无可见经验。未登录只能看 public；登录后会按你的 private 经验一起排序。" />
      ) : (
        <ul className="divide-y divide-border/60">
          {rows.map((row) => (
            <li key={row.experience_id} className="px-4 py-3">
              <div className="mb-1 flex items-center gap-2 text-xs">
                <Link
                  href={`/experiences/${row.experience_id}`}
                  className="font-mono text-[11px] text-cyan-700 hover:underline"
                >
                  {shortId(row.experience_id)}
                </Link>
                <Badge className="bg-emerald-100 text-emerald-900">
                  复用率 {Math.round(row.reuse_rate * 100)}%
                </Badge>
                <Badge className="bg-cyan-100 text-cyan-900">
                  排序分 {row.rank_score.toFixed(2)}
                </Badge>
                <Badge variant="outline" className="font-mono">
                  evidence {Math.round(row.feedback_evidence * 100)}%
                </Badge>
                {row.feedback_count > 0 ? (
                  <Badge className={rewardBadge(row.avg_reward)}>
                    奖励 {formatSigned(row.avg_reward)}
                  </Badge>
                ) : (
                  <Badge variant="outline" className="text-muted-foreground">
                    暂无反馈
                  </Badge>
                )}
                <Badge variant="outline" className="font-mono">
                  q={row.q_scalar.toFixed(2)}
                </Badge>
              </div>
              <Link
                href={`/experiences/${row.experience_id}`}
                className="line-clamp-2 text-sm font-medium leading-5 hover:text-cyan-800 hover:underline"
              >
                {row.intent_text || "(no intent)"}
              </Link>
              <div className="mt-2 flex flex-wrap gap-2 text-xs text-muted-foreground">
                <span>{row.used_count}/{row.feedback_count} used</span>
                <span>reuse_count {row.reuse_count}</span>
                <span>q_updates {row.q_update_count}</span>
                <span>{row.negative_count} negative</span>
                <span>conf {formatFixed(row.avg_confidence)}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TurnRewardsPanel({
  items,
}: {
  items: Awaited<ReturnType<typeof listExperiencesWithRewards>>;
}) {
  return (
    <div className="rounded-lg border border-border/70 bg-white shadow-sm">
      <div className="flex items-center gap-2 border-b border-border/60 px-4 py-3 text-sm">
        <Award className="h-4 w-4 text-amber-700" />
        <span className="font-semibold">离线 turn_rewards</span>
      </div>
      {items.length === 0 ? (
        <div className="px-4 py-5 text-xs leading-5 text-muted-foreground">
          暂无离线奖励标注。可用 <span className="font-mono">exp annotate-existing</span> 生成 per-turn 5 维评分。
        </div>
      ) : (
        <ul className="divide-y divide-border/60">
          {items.slice(0, 8).map((item) => (
            <li key={item.experience_id} className="px-4 py-3">
              <div className="mb-1 flex flex-wrap items-center gap-2 text-xs">
                <Link
                  href={`/rewards/${item.experience_id}`}
                  className="font-mono text-[11px] text-cyan-700 hover:underline"
                >
                  {shortId(item.experience_id)}
                </Link>
                <Badge className={rewardBadge(item.trajectory_score)}>
                  score {formatFixed(item.trajectory_score)}
                </Badge>
                <span className="text-muted-foreground">{item.reward_count} turn</span>
              </div>
              <Link
                href={`/rewards/${item.experience_id}`}
                className="line-clamp-2 text-sm font-medium leading-5 hover:text-cyan-800 hover:underline"
              >
                {item.intent_text || "(no intent)"}
                <ArrowRight className="ml-1 inline h-3.5 w-3.5 opacity-50" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="px-4 py-8 text-center text-sm text-muted-foreground">
      <ThumbsDown className="mx-auto mb-2 h-5 w-5 opacity-50" />
      {text}
    </div>
  );
}

function formatSigned(value: number | null | undefined): string {
  if (value === null || value === undefined) return "0.00";
  const rounded = value.toFixed(2);
  return value > 0 ? `+${rounded}` : rounded;
}

function formatFixed(value: number | null | undefined): string {
  if (value === null || value === undefined) return "0.00";
  return value.toFixed(2);
}

function rewardBadge(value: number | null | undefined): string {
  if (value === null || value === undefined) return "bg-muted text-muted-foreground";
  if (value > 0.15) return "bg-emerald-100 text-emerald-900";
  if (value < -0.15) return "bg-rose-100 text-rose-900";
  return "bg-cyan-100 text-cyan-900";
}

function statusBadge(status: string): string {
  if (status === "success" || status === "completed" || status === "ok") {
    return "bg-emerald-100 text-emerald-900";
  }
  if (status === "failed" || status === "error") {
    return "bg-rose-100 text-rose-900";
  }
  return "bg-cyan-100 text-cyan-900";
}
