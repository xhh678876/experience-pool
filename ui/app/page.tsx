import Link from "@/components/ui/link";
import {
  Activity,
  Award,
  Boxes,
  Database,
  Layers3,
  Search,
  Sparkles,
  Terminal,
  Trophy,
  Users,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CopyButton } from "@/components/ui/copy-button";
import {
  getMvpStats,
  listAgents,
  searchMvpExperiences,
  type MvpExperienceHit,
} from "@/lib/mvp-queries";
import {
  getDashboardStats,
  listRecentSessions,
  topAgentsByContribution,
  type SessionGroup,
} from "@/lib/queries";
import { formatDate, sensitivityColor, shortId } from "@/lib/utils";

export const dynamic = "force-dynamic";

const INSTALL_CMD = "curl -sSL https://expool.clawsii.com/install | bash";

const SAMPLE_QUERIES = [
  "Caddy ACME 证书签发",
  "FastAPI HMAC 验签",
  "SQLite WAL 模式",
  "Canvas API token 上传作业",
  "k8s pod OOM 排查",
];

type SearchParams = {
  q?: string;
  agent?: string;
};

export default async function MarketHome({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp = await searchParams;
  const q = (sp.q ?? "").trim();
  const agents = listAgents();
  const viewer = sp.agent ?? agents[0]?.name ?? "";
  const stats = getMvpStats();
  const dash = getDashboardStats();
  const sessions = listRecentSessions(8);
  const top = topAgentsByContribution(8);

  const hits = q
    ? searchMvpExperiences({ viewerName: viewer, query: q, topK: 5 })
    : ([] as MvpExperienceHit[]);

  return (
    <div className="flex flex-col gap-6 pb-12">
      {/* HERO — centered layout */}
      <section className="overflow-hidden rounded-3xl border border-border/60 bg-gradient-to-br from-cyan-50 via-white to-amber-50/60 px-6 py-10 sm:py-12">
        <div className="mx-auto flex max-w-2xl flex-col items-center text-center">
          <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-cyan-600/30 bg-white/80 px-3 py-1 text-[11px] font-medium text-cyan-800">
            <Sparkles className="h-3 w-3" /> Experience Pool · 多 agent 共享经验池
          </div>
          <h1 className="text-3xl font-semibold leading-tight text-foreground sm:text-4xl">
            任意 agent → 任意机器 → <span className="text-cyan-700">共享池</span>
          </h1>
          <p className="mt-3 text-sm leading-7 text-muted-foreground sm:text-[15px]">
            本地装个 skill，每条 session 自动按子任务切分、脱敏、上传。
            别的 agent 在动手前先搜池里前人怎么解决的，
            省下重复踩坑、还能拿到 5 维奖励标注做训练样本。
          </p>

          <div className="mt-6 w-full">
            <div className="mb-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              一行命令安装
            </div>
            <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center sm:justify-center">
              <code className="flex-1 truncate rounded-lg border border-border/60 bg-white/85 px-3 py-2.5 text-center font-mono text-[12px] text-foreground sm:text-sm">
                {INSTALL_CMD}
              </code>
              <CopyButton text={INSTALL_CMD} label="复制" className="h-10 px-4" />
            </div>
            <p className="mt-2 text-[11px] text-muted-foreground/80">
              装完即跑：Stop hook 秒传 Claude Code session，launchd / systemd timer 每 2 分钟同步 Hermes / Codex / agents-chat。
            </p>
          </div>

          <form className="mt-6 flex w-full flex-col items-center gap-2 sm:flex-row sm:justify-center">
            <div className="w-full max-w-xl flex-1">
              <Input
                name="q"
                defaultValue={q}
                placeholder="搜池子里前人的经验，例如：FastAPI HMAC 验签"
                className="h-11 text-sm"
              />
            </div>
            <input type="hidden" name="agent" value={viewer} />
            <Button type="submit" className="h-11 px-5">
              <Search className="mr-1.5 h-4 w-4" />
              搜索
            </Button>
            <Link href="/search" className="text-xs text-muted-foreground hover:text-cyan-800">
              高级检索 →
            </Link>
          </form>

          {!q ? (
            <div className="mt-3 flex flex-wrap items-center justify-center gap-1.5">
              <span className="text-[11px] text-muted-foreground">试试：</span>
              {SAMPLE_QUERIES.map((s) => (
                <Link
                  key={s}
                  href={`/?q=${encodeURIComponent(s)}`}
                  className="rounded-full border border-border/60 bg-white/70 px-2.5 py-1 text-[11px] text-muted-foreground hover:border-cyan-600/40 hover:text-cyan-800"
                >
                  {s}
                </Link>
              ))}
            </div>
          ) : null}
        </div>

        {q ? (
          <div className="mx-auto mt-6 max-w-3xl text-left">
            <SearchResults hits={hits} q={q} />
          </div>
        ) : null}

        {/* Stats row — horizontal under the hero text */}
        <div className="mx-auto mt-8 grid max-w-3xl grid-cols-2 gap-2 sm:grid-cols-4">
          <HeroTile icon={<Database className="h-3.5 w-3.5" />} label="经验" value={dash.total} />
          <HeroTile icon={<Users className="h-3.5 w-3.5" />} label="agent" value={stats.agents} />
          <HeroTile icon={<Award className="h-3.5 w-3.5" />} label="脱敏命中" value={stats.redactions} />
          <HeroTile icon={<Layers3 className="h-3.5 w-3.5" />} label="任务类型" value={dash.byTaskType.length} />
        </div>
      </section>

      {/* MAIN GRID */}
      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <section className="rounded-2xl border border-border/60 bg-white/85">
          <div className="flex items-center gap-2 border-b border-border/60 px-5 py-3">
            <Boxes className="h-4 w-4 text-cyan-700" />
            <h2 className="text-sm font-semibold">最近 session</h2>
            <span className="text-xs text-muted-foreground">
              · 同 session 的多个任务段聚合显示
            </span>
            <Link
              href="/experiences"
              className="ml-auto text-xs text-muted-foreground hover:text-cyan-800"
            >
              查看全部 →
            </Link>
          </div>
          {sessions.length === 0 ? (
            <EmptyState
              icon={<Boxes className="h-6 w-6" />}
              title="池子还是空的"
              hint="在 agent 机器上跑一行安装命令，下一条 session 结束自动出现在这里。"
            />
          ) : (
            <ul className="divide-y divide-border/60">
              {sessions.map((g) => (
                <SessionRow key={g.session_id + (g.agent_name ?? "")} group={g} />
              ))}
            </ul>
          )}
        </section>

        <aside className="flex flex-col gap-4">
          <section className="rounded-2xl border border-border/60 bg-white/85">
            <div className="flex items-center gap-2 border-b border-border/60 px-4 py-3">
              <Trophy className="h-4 w-4 text-amber-700" />
              <h3 className="text-sm font-semibold">贡献最多</h3>
            </div>
            {top.length === 0 ? (
              <p className="px-4 py-4 text-xs text-muted-foreground">暂无</p>
            ) : (
              <ul className="divide-y divide-border/60">
                {top.map((a, i) => (
                  <li key={a.agent_id} className="flex items-center gap-2 px-4 py-2">
                    <span
                      className={`inline-flex h-5 w-5 items-center justify-center rounded-md text-[10px] font-mono font-semibold ${
                        i === 0
                          ? "bg-amber-100 text-amber-900"
                          : i === 1
                          ? "bg-cyan-100 text-cyan-900"
                          : i === 2
                          ? "bg-emerald-100 text-emerald-900"
                          : "bg-muted text-muted-foreground"
                      }`}
                    >
                      {i + 1}
                    </span>
                    <span className="truncate text-sm">{a.agent_name}</span>
                    <Badge variant="outline" className="ml-1 font-mono text-[10px]">
                      {a.team}
                    </Badge>
                    <span className="ml-auto font-mono text-xs">{a.experiences}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="rounded-2xl border border-border/60 bg-white/85">
            <div className="flex items-center gap-2 border-b border-border/60 px-4 py-3">
              <Activity className="h-4 w-4 text-cyan-700" />
              <h3 className="text-sm font-semibold">7 日趋势</h3>
            </div>
            <div className="px-4 py-3">
              <Sparkbars data={dash.last7Days} />
            </div>
          </section>

          <section className="rounded-2xl border border-border/60 bg-white/85">
            <div className="flex items-center gap-2 border-b border-border/60 px-4 py-3">
              <Terminal className="h-4 w-4 text-muted-foreground" />
              <h3 className="text-sm font-semibold">CLI 速查</h3>
            </div>
            <ul className="divide-y divide-border/60 text-xs">
              <CmdRow cmd="exp daemon-state" desc="后台同步状态" />
              <CmdRow cmd="exp push-latest" desc="手动上传最近 session" />
              <CmdRow cmd="exp list-sessions -v" desc="列本机所有源" />
              <CmdRow cmd="exp export --output dump.jsonl" desc="导出离线 JSONL" />
            </ul>
          </section>
        </aside>
      </div>
    </div>
  );
}

function HeroTile({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: number | string;
}) {
  return (
    <div className="flex flex-col items-center gap-0.5 rounded-xl border border-border/50 bg-white/85 px-3 py-3">
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className="font-mono text-2xl font-semibold text-foreground">{value}</div>
    </div>
  );
}

function SessionRow({ group }: { group: SessionGroup }) {
  const segCount = group.segments.length;
  const isMulti = segCount > 1;
  const headTask = group.segments[0]?.intent_text || "(no intent)";

  return (
    <li className="px-5 py-3">
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <Badge variant="outline" className="font-mono uppercase">
          {group.agent_type}
        </Badge>
        {isMulti ? (
          <Badge className="bg-cyan-100 text-cyan-900 font-mono">
            {segCount} 段 · 同一 session
          </Badge>
        ) : null}
        <span className="text-muted-foreground">{group.agent_name || "unknown"}</span>
        <span className="text-muted-foreground">·</span>
        <span className="text-muted-foreground">{formatDate(group.ended_at)}</span>
        <span className="ml-auto font-mono text-[10px] text-muted-foreground">
          {group.session_id.length > 24
            ? group.session_id.slice(0, 8) + "…"
            : group.session_id}
        </span>
      </div>
      <ul className="mt-2 ml-3 border-l border-dashed border-border/60 pl-3">
        {group.segments.slice(0, 5).map((s, i) => (
          <li key={s.experience_id} className="flex items-center gap-2 py-0.5 text-sm">
            {isMulti ? (
              <span className="font-mono text-[10px] text-muted-foreground">
                #{(s.seg_index ?? i) + 1}
              </span>
            ) : null}
            <Link
              href={`/experiences/${s.experience_id}`}
              className="truncate text-foreground hover:text-cyan-800 hover:underline"
            >
              {s.intent_text || "(no intent)"}
            </Link>
            <Badge variant="outline" className="ml-auto shrink-0 font-mono text-[9px]">
              {s.task_type}
            </Badge>
          </li>
        ))}
        {group.segments.length > 5 ? (
          <li className="py-0.5 text-[11px] text-muted-foreground">
            … 还有 {group.segments.length - 5} 段
          </li>
        ) : null}
      </ul>
      {!isMulti ? null : (
        <div className="mt-1.5 text-[10px] text-muted-foreground/70">
          首段: <span className="font-medium">{headTask.slice(0, 80)}</span>
        </div>
      )}
    </li>
  );
}

function SearchResults({ hits, q }: { hits: MvpExperienceHit[]; q: string }) {
  if (hits.length === 0) {
    return (
      <div className="mt-4 rounded-xl border border-dashed border-border/60 bg-white/60 px-5 py-6 text-center text-sm text-muted-foreground">
        “{q}” 暂无命中。试试缩短查询、换个关键词，或去
        <Link href="/search" className="mx-1 text-cyan-700 hover:underline">
          高级检索
        </Link>
        加 ACL/任务类型筛选。
      </div>
    );
  }
  return (
    <div className="mt-4 rounded-xl border border-border/60 bg-white/85">
      <div className="border-b border-border/60 px-4 py-2 text-xs">
        <span className="font-semibold">{hits.length}</span>
        <span className="text-muted-foreground"> 条命中 · </span>
        <Link href={`/search?q=${encodeURIComponent(q)}`} className="text-cyan-700 hover:underline">
          完整结果 →
        </Link>
      </div>
      <ul className="divide-y divide-border/60">
        {hits.map((h, i) => (
          <li key={h.experience_id} className="px-4 py-2">
            <div className="flex items-center gap-2 text-[11px]">
              <span className="rounded bg-muted/60 px-1.5 py-0.5 font-mono text-muted-foreground">
                #{i + 1}
              </span>
              {h.similarity != null ? (
                <Badge className="bg-cyan-100 text-cyan-900 font-mono">
                  sim {Math.round(h.similarity * 100)}%
                </Badge>
              ) : null}
              <Badge variant="outline" className="font-mono">{h.task_type}</Badge>
              <Badge className={sensitivityColor(h.sensitivity)}>{h.sensitivity}</Badge>
              <span className="text-muted-foreground">{h.agent_name}</span>
              <span className="ml-auto font-mono text-[10px] text-muted-foreground">
                {shortId(h.experience_id)}
              </span>
            </div>
            <Link
              href={`/experiences/${h.experience_id}`}
              className="block py-0.5 text-sm hover:text-cyan-800 hover:underline"
            >
              {h.intent || h.query}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Sparkbars({ data }: { data: { day: string; count: number }[] }) {
  const max = Math.max(1, ...data.map((d) => d.count));
  return (
    <div className="flex items-end gap-1">
      {data.map((d) => {
        const h = Math.round((d.count / max) * 56) + 4;
        return (
          <div key={d.day} className="flex flex-1 flex-col items-center gap-0.5">
            <div
              className="w-full rounded-t bg-cyan-600/70"
              style={{ height: `${h}px` }}
              title={`${d.day}: ${d.count}`}
            />
            <div className="font-mono text-[9px] text-muted-foreground">
              {d.day.slice(8)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function CmdRow({ cmd, desc }: { cmd: string; desc: string }) {
  return (
    <li className="flex items-center gap-2 px-4 py-2">
      <code className="flex-1 truncate font-mono text-[11px] text-foreground">{cmd}</code>
      <span className="shrink-0 text-[10px] text-muted-foreground">{desc}</span>
      <CopyButton text={cmd} label="" className="h-5 w-5 px-0 justify-center" />
    </li>
  );
}

function EmptyState({
  icon,
  title,
  hint,
}: {
  icon: React.ReactNode;
  title: string;
  hint: string;
}) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-10 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-muted/50 text-muted-foreground">
        {icon}
      </div>
      <p className="text-sm font-medium">{title}</p>
      <p className="max-w-md text-xs leading-5 text-muted-foreground">{hint}</p>
      <CopyButton text={INSTALL_CMD} label="复制安装命令" className="mt-2 h-7" />
    </div>
  );
}
