import Link from "@/components/ui/link";
import {
  Activity,
  ArrowRight,
  Award,
  Boxes,
  CircleCheck,
  Database,
  Layers3,
  PlugZap,
  Search,
  ShieldCheck,
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
  searchMvpExperiences,
  type MvpExperienceHit,
} from "@/lib/mvp-queries";
import {
  getDashboardStats,
  listRecentSessions,
  topAgentsByContribution,
  type SessionGroup,
} from "@/lib/queries";
import { getCurrentUser } from "@/lib/auth";
import { formatDate, sensitivityColor, shortId } from "@/lib/utils";

export const dynamic = "force-dynamic";

const PLUGIN_REPO_URL = (
  process.env.EXP_PLUGIN_REPO_URL ?? "https://github.com/xhh678876/expool-mcp-plugin"
).trim().replace(/\.git$/, "");
const NPM_PACKAGE = process.env.EXP_PLUGIN_NPM_PACKAGE ?? "@haohui666/expool-plugin";
const INSTALL_CMD =
  process.env.EXP_PLUGIN_INSTALL_CMD ??
  (PLUGIN_REPO_URL
    ? `claude plugin marketplace add ${PLUGIN_REPO_URL} && claude plugin install expool`
    : `npx ${NPM_PACKAGE} install --agents claude,codex,openclaw,hermes`);

const SAMPLE_QUERIES = [
  "Caddy ACME 证书签发",
  "FastAPI HMAC 验签",
  "SQLite WAL 模式",
  "Canvas API token 上传作业",
  "k8s pod OOM 排查",
];

type SearchParams = {
  q?: string;
};

export default async function MarketHome({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp = await searchParams;
  const q = (sp.q ?? "").trim();
  const me = await getCurrentUser();
  // Never accept an agent identity from the URL. Private ACL is derived only
  // from the authenticated portal session; anonymous visitors see public rows.
  const viewer = me?.default_agent_name ?? "";
  const stats = getMvpStats();
  const dash = getDashboardStats();
  const sessions = await listRecentSessions(
    8,
    me
      ? { scope: "personal", viewerName: me.default_agent_name }
      : { scope: "public" },
  );
  const top = await topAgentsByContribution(8);

  const hits = q
    ? await searchMvpExperiences({ viewerName: viewer, query: q, topK: 5 })
    : ([] as MvpExperienceHit[]);

  return (
    <div className="flex flex-col gap-6 pb-12">
      <section className="overflow-hidden rounded-lg border border-border/70 bg-white/88 shadow-sm">
        <div className="grid gap-0 lg:grid-cols-[1fr_360px]">
          <div className="border-b border-border/60 px-5 py-5 lg:border-b-0 lg:border-r lg:px-7 lg:py-7">
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1.5 rounded-md border border-cyan-700/20 bg-cyan-50 px-2.5 py-1 text-[11px] font-medium text-cyan-800">
                <Sparkles className="h-3.5 w-3.5" />
                Experience Pool
              </span>
              <span className="inline-flex items-center gap-1.5 rounded-md border border-emerald-700/20 bg-emerald-50 px-2.5 py-1 text-[11px] font-medium text-emerald-800">
                <CircleCheck className="h-3.5 w-3.5" />
                private by default
              </span>
              <span className="inline-flex items-center gap-1.5 rounded-md border border-border/70 bg-muted/60 px-2.5 py-1 text-[11px] text-muted-foreground">
                Claude Code · Codex · OpenClaw · Hermes
              </span>
            </div>

            <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_260px]">
              <div>
                <h1 className="max-w-3xl text-2xl font-semibold leading-tight text-foreground sm:text-3xl">
                  开工先查经验，收工自动沉淀。
                </h1>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
                  把多 agent 的任务轨迹脱敏成可检索的经验卡。团队成员下一次遇到相似问题时，直接复用已经跑通的步骤。
                </p>

                <form className="mt-5 flex w-full flex-col gap-2 sm:flex-row">
                  <Input
                    name="q"
                    defaultValue={q}
                    placeholder="搜索经验，例如：FastAPI HMAC 验签"
                    className="h-11 min-w-0 flex-1 bg-white text-sm"
                  />
                  <Button type="submit" className="h-11 px-5">
                    <Search className="mr-1.5 h-4 w-4" />
                    搜索
                  </Button>
                  <Link
                    href="/search"
                    className="inline-flex h-11 items-center justify-center rounded-md border border-border bg-background px-4 text-sm font-medium transition-colors hover:bg-muted hover:text-foreground"
                  >
                    高级检索
                    <ArrowRight className="ml-1.5 h-4 w-4" />
                  </Link>
                </form>

                {!q ? (
                  <div className="mt-3 flex flex-wrap items-center gap-1.5">
                    <span className="text-[11px] text-muted-foreground">试试</span>
                    {SAMPLE_QUERIES.map((s) => (
                      <Link
                        key={s}
                        href={`/?q=${encodeURIComponent(s)}`}
                        className="rounded-md border border-border/60 bg-white px-2.5 py-1 text-[11px] text-muted-foreground hover:border-cyan-600/40 hover:text-cyan-800"
                      >
                        {s}
                      </Link>
                    ))}
                  </div>
                ) : null}
              </div>

              <div className="grid grid-cols-2 gap-2 xl:grid-cols-1">
                <QuickAction
                  href="/plugins"
                  icon={<PlugZap className="h-4 w-4" />}
                  label="接入 agent"
                  detail="安装插件并绑定账号"
                />
                <QuickAction
                  href="/me/api-keys"
                  icon={<ShieldCheck className="h-4 w-4" />}
                  label="API Key"
                  detail="生成配对码或长期 key"
                />
              </div>
            </div>

            {q ? (
              <div className="mt-5 max-w-3xl text-left">
                <SearchResults hits={hits} q={q} />
              </div>
            ) : null}
          </div>

          <div className="flex flex-col gap-4 bg-[linear-gradient(180deg,hsl(174_45%_96%),hsl(45_34%_95%))] px-5 py-5 lg:px-6 lg:py-7">
            <div>
              <div className="mb-2 flex items-center gap-2 text-xs font-medium text-cyan-900">
                <Activity className="h-4 w-4" />
                池子状态
              </div>
              <div className="grid grid-cols-2 gap-2">
                <HeroTile icon={<Database className="h-3.5 w-3.5" />} label="经验" value={dash.total} />
                <HeroTile icon={<Users className="h-3.5 w-3.5" />} label="agent" value={stats.agents} />
                <HeroTile icon={<Award className="h-3.5 w-3.5" />} label="脱敏" value={stats.redactions} />
                <HeroTile icon={<Layers3 className="h-3.5 w-3.5" />} label="类型" value={dash.byTaskType.length} />
              </div>
            </div>

            <div className="rounded-lg border border-border/60 bg-white/80 p-3">
              <div className="mb-2 flex items-center gap-2 text-xs font-medium">
                <Terminal className="h-4 w-4 text-muted-foreground" />
                快速安装
              </div>
              <code className="block truncate rounded-md border border-border/60 bg-muted/60 px-2.5 py-2 font-mono text-[11px] text-foreground">
                {INSTALL_CMD}
              </code>
              <div className="mt-2 flex items-center justify-between gap-2">
                <span className="text-[11px] text-muted-foreground">默认上传到 private</span>
                <CopyButton text={INSTALL_CMD} label="复制" className="h-7 px-3" />
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* MAIN GRID */}
      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <section className="rounded-2xl border border-border/60 bg-white/85">
          <div className="flex items-center gap-2 border-b border-border/60 px-5 py-3">
            <Boxes className="h-4 w-4 text-cyan-700" />
            <h2 className="text-sm font-semibold">最近 session</h2>
            <span className="text-xs text-muted-foreground">
              · {me ? "我的私有池优先" : "公共池预览"} · 同 session 的多个任务段聚合显示
            </span>
            <Link
              href="/sessions"
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
              <CmdRow cmd="expool-plugin detect" desc="识别最近 session/model" />
              <CmdRow cmd="expool-plugin auto on" desc="开启自动上传" />
              <CmdRow cmd="/expool:search &quot;...&quot;" desc="agent 内检索经验池" />
            </ul>
            <div className="border-t border-border/60 px-4 py-2 text-right text-xs">
              <Link href="/plugins" className="text-cyan-700 hover:underline">
                完整插件接入 →
              </Link>
            </div>
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
    <div className="flex min-h-[76px] flex-col justify-between rounded-lg border border-border/50 bg-white/85 px-3 py-3">
      <div className="flex items-center gap-1 text-[10px] uppercase text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className="font-mono text-2xl font-semibold text-foreground">{value}</div>
    </div>
  );
}

function QuickAction({
  href,
  icon,
  label,
  detail,
}: {
  href: string;
  icon: React.ReactNode;
  label: string;
  detail: string;
}) {
  return (
    <Link
      href={href}
      className="group flex min-h-[76px] flex-col justify-between rounded-lg border border-border/60 bg-white/75 p-3 text-left transition hover:border-cyan-600/40 hover:bg-cyan-50/40"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-cyan-800">{icon}</span>
        <ArrowRight className="h-3.5 w-3.5 text-muted-foreground transition group-hover:translate-x-0.5 group-hover:text-cyan-800" />
      </div>
      <div>
        <div className="text-sm font-medium text-foreground">{label}</div>
        <div className="mt-0.5 text-[11px] text-muted-foreground">{detail}</div>
      </div>
    </Link>
  );
}

function SessionRow({ group }: { group: SessionGroup }) {
  const segCount = group.segments.length;
  const totalTurns = group.segments.reduce((sum, segment) => sum + segment.turn_count, 0);
  const isMulti = segCount > 1;
  const headTask = group.segments[0]?.intent_text || "(no intent)";

  return (
    <li className="px-5 py-3">
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <Badge variant="outline" className="font-mono uppercase">
          {group.agent_type}
        </Badge>
        {isMulti || totalTurns > 0 ? (
          <Badge className="bg-cyan-100 font-mono text-cyan-900">
            {isMulti ? `${segCount} 段 · ${totalTurns} turns` : `${totalTurns} turns`}
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
