import Link from "@/components/ui/link";
import { Boxes, Clock, Users } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { listRecentSessions } from "@/lib/queries";
import { getCurrentUser } from "@/lib/auth";
import { formatDate } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function SessionsPage() {
  const me = await getCurrentUser();
  const sessions = await listRecentSessions(
    50,
    me
      ? { scope: "personal", viewerName: me.default_agent_name }
      : { scope: "public" },
  );

  return (
    <div className="flex flex-col gap-6 pb-12">
      <section className="flex flex-col gap-2 rounded-2xl border border-border/60 bg-white/85 px-5 py-4">
        <div className="flex items-center gap-2 text-sm">
          <Boxes className="h-4 w-4 text-cyan-700" />
          <span className="font-semibold">完整 session 视图</span>
          <span className="text-muted-foreground">
            · {me ? "我的私有池" : "公共池预览"} · 同一 session 切出来的多段聚合在一起
          </span>
        </div>
        <p className="text-xs leading-5 text-muted-foreground">
          上传时切分器按 marker / 时间空隙 / 关键词 把 session 切成 N 段。登录后这里默认只看自己的私有经验池，
          能看到一个 session 完整覆盖了哪些任务。
        </p>
      </section>

      {sessions.length === 0 ? (
        <section className="rounded-2xl border border-dashed border-border/60 bg-muted/30 px-6 py-10 text-center">
          <Boxes className="mx-auto h-8 w-8 text-muted-foreground" />
          <p className="mt-3 text-sm text-muted-foreground">暂无 session 上传</p>
        </section>
      ) : (
        <ul className="grid gap-3 lg:grid-cols-2">
          {sessions.map((g) => {
            const isMulti = g.segments.length > 1;
            const totalTurns = g.segments.reduce(
              (sum, segment) => sum + segment.turn_count,
              0,
            );
            const span =
              g.started_at !== g.ended_at
                ? `${formatDate(g.started_at)} → ${formatDate(g.ended_at)}`
                : formatDate(g.started_at);
            return (
              <li
                key={g.session_id + (g.agent_name || "")}
                className="rounded-2xl border border-border/60 bg-white/85"
              >
                <div className="flex flex-wrap items-center gap-2 border-b border-border/60 px-4 py-3 text-xs">
                  <Badge variant="outline" className="font-mono uppercase">
                    {g.agent_type}
                  </Badge>
                  <Badge
                    className={
                      isMulti
                        ? "bg-cyan-100 text-cyan-900 font-mono"
                        : "bg-muted text-muted-foreground"
                    }
                  >
                    {g.segments.length} 段 · {totalTurns} turns
                  </Badge>
                  <span className="flex items-center gap-1 text-muted-foreground">
                    <Users className="h-3 w-3" />
                    {g.agent_name || "unknown"}
                  </span>
                  <span className="ml-auto flex items-center gap-1 text-muted-foreground">
                    <Clock className="h-3 w-3" />
                    {span}
                  </span>
                </div>
                <ul className="px-4 py-2">
                  {g.segments.map((s, i) => (
                    <li
                      key={s.experience_id}
                      className="flex items-start gap-2.5 py-2"
                    >
                      <div className="flex flex-col items-center pt-1">
                        <span
                          className={
                            "flex h-6 w-6 shrink-0 items-center justify-center rounded-full font-mono text-[10px] font-semibold " +
                            (i === 0
                              ? "bg-emerald-100 text-emerald-900"
                              : i === g.segments.length - 1
                              ? "bg-amber-100 text-amber-900"
                              : "bg-cyan-100 text-cyan-900")
                          }
                        >
                          {(s.seg_index ?? i) + 1}
                        </span>
                        {i < g.segments.length - 1 ? (
                          <span className="mt-1 h-full w-px bg-border/60" />
                        ) : null}
                      </div>
                      <div className="min-w-0 flex-1">
                        <Link
                          href={`/experiences/${s.experience_id}`}
                          className="block text-sm font-medium text-foreground hover:text-cyan-800 hover:underline"
                        >
                          {s.intent_text || "(no intent)"}
                        </Link>
                        <div className="mt-0.5 flex flex-wrap gap-1.5 text-[10px] text-muted-foreground">
                          <Badge variant="outline" className="font-mono">
                            {s.task_type}
                          </Badge>
                          <Badge variant="outline" className="font-mono">
                            {s.turn_count} turns
                          </Badge>
                          <Badge
                            className={
                              s.sensitivity === "high"
                                ? "bg-rose-100 text-rose-900"
                                : s.sensitivity === "medium"
                                ? "bg-amber-100 text-amber-900"
                                : "bg-emerald-100 text-emerald-900"
                            }
                          >
                            {s.sensitivity}
                          </Badge>
                          <Badge variant="outline">{s.review_status}</Badge>
                          <span className="font-mono opacity-70">
                            {s.experience_id.slice(0, 8)}
                          </span>
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
