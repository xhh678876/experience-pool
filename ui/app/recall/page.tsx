import Link from "@/components/ui/link";
import {
  ArrowRight,
  CheckCircle2,
  Filter,
  Gauge,
  Radar,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CopyButton } from "@/components/ui/copy-button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { getCurrentUser } from "@/lib/auth";
import { distinctValues } from "@/lib/queries";
import {
  apiRagContext,
  type RagChunkResult,
  type RagExperienceResult,
} from "@/lib/users-api";
import { formatDate, sensitivityColor, shortId } from "@/lib/utils";

export const dynamic = "force-dynamic";

type SearchParams = {
  q?: string;
  task?: string;
  topK?: string;
};

const SAMPLE_QUERIES = [
  "FastAPI HMAC 验签失败",
  "Claude Code 插件自动上传",
  "MOVA 测评内容整理",
  "Cloudflare 内网转发",
  "SQLite WAL 检索为空",
];

export default async function RecallPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp = await searchParams;
  const me = await getCurrentUser();
  const viewer = me?.default_agent_name ?? "";
  const task = sp.task ?? "all";
  const topK = Math.max(1, Math.min(10, Number(sp.topK ?? "3")));
  const q = (sp.q ?? "").trim();
  const taskTypes = distinctValues("task_type", viewer);

  const ragCall = q && me
    ? await apiRagContext({
        q,
        top_k: topK,
        task_type: task === "all" ? undefined : task,
        scope: "personal",
        record_event: false,
      })
    : null;
  const rag = ragCall?.data;
  const hits = rag?.chunks ?? [];
  const experiences = new Map(
    (rag?.experiences ?? []).map((item) => [item.experience_id, item]),
  );
  const profile = buildRecallProfile(hits);
  const contextText = rag?.context ?? buildEmptyContext(q, topK, Boolean(me), ragCall?.message);
  const retrieval = rag?.retrieval_meta;

  return (
    <div className="flex flex-col gap-4 pb-12">
      <section className="rounded-lg border border-border/70 bg-white px-5 py-4 shadow-sm">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div className="max-w-3xl">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
              <span className="inline-flex items-center gap-1.5 font-semibold text-foreground">
                <Radar className="h-4 w-4 text-cyan-700" />
                自动召回调试台
              </span>
              <Badge className="bg-cyan-50 text-cyan-800">UserPromptSubmit hook</Badge>
              <Badge className="bg-emerald-50 text-emerald-800">private-aware</Badge>
            </div>
            <p className="text-sm leading-6 text-muted-foreground">
              直接调用插件使用的 chunk RAG、混合检索和重排链路；预览请求不写入复用事件，不会污染 Q 值。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <CopyButton text={contextText} label="复制注入上下文" />
            <Link href="/search">
              <Button variant="outline" size="sm">
                高级检索
                <ArrowRight className="ml-1.5 h-3.5 w-3.5" />
              </Button>
            </Link>
          </div>
        </div>

        <form className="mt-4 grid gap-2 lg:grid-cols-[minmax(0,1fr)_180px_110px_auto] lg:items-end">
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">用户消息 / 任务描述</label>
            <Input
              name="q"
              defaultValue={q}
              placeholder="例如：修复 FastAPI HMAC 签名失败"
              className="h-10 bg-white"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">任务类型</label>
            <Select name="task" defaultValue={task} className="h-10">
              <option value="all">全部</option>
              {taskTypes.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">Top K</label>
            <Select name="topK" defaultValue={String(topK)} className="h-10">
              {[1, 3, 5, 8, 10].map((n) => (
                <option key={n} value={String(n)}>
                  {n}
                </option>
              ))}
            </Select>
          </div>
          {me ? (
            <Button type="submit" className="h-10 px-4">
              <Search className="mr-1.5 h-4 w-4" />
              召回
            </Button>
          ) : (
            <Link href="/login" className="inline-flex h-10 items-center justify-center rounded-md bg-foreground px-4 text-sm font-medium text-background">
              登录后检索私有池
            </Link>
          )}
        </form>

        {me ? (
          <p className="mt-2 text-[11px] text-muted-foreground">
            当前权限：{me.email} · agent={viewer} · scope=personal
          </p>
        ) : null}

        {!q ? (
          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] text-muted-foreground">样例</span>
            {SAMPLE_QUERIES.map((sample) => (
              <Link
                key={sample}
                href={`/recall?q=${encodeURIComponent(sample)}&topK=${topK}`}
                className="rounded-md border border-border/60 px-2.5 py-1 text-[11px] text-muted-foreground hover:border-cyan-600/40 hover:text-cyan-800"
              >
                {sample}
              </Link>
            ))}
          </div>
        ) : null}
      </section>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
        <div className="flex flex-col gap-4">
          <div className="grid gap-3 md:grid-cols-4">
            <MetricCard
              icon={<Filter className="h-4 w-4" />}
              label="候选"
              value={retrieval?.acl_candidates ?? 0}
              detail="ACL 后可读"
            />
            <MetricCard
              icon={<Sparkles className="h-4 w-4" />}
              label="注入"
              value={hits.length}
              detail={`top ${topK}`}
            />
            <MetricCard
              icon={<Gauge className="h-4 w-4" />}
              label="重排候选"
              value={retrieval?.reranked_candidates ?? 0}
              detail={`阈值 ${retrieval?.min_score ?? "-"}`}
            />
            <MetricCard
              icon={<ShieldCheck className="h-4 w-4" />}
              label="父 session"
              value={profile.parentSessions}
              detail="结果去重后"
            />
          </div>

          <div className="rounded-lg border border-border/70 bg-white shadow-sm">
            <div className="flex items-center gap-2 border-b border-border/60 px-4 py-3 text-sm">
              <SlidersHorizontal className="h-4 w-4 text-cyan-700" />
              <span className="font-semibold">召回分布</span>
              {q ? (
                <span className="text-muted-foreground">
                  · {retrieval?.accepted_candidates ?? 0} 个过阈值，返回 {hits.length} 个 chunk
                </span>
              ) : null}
            </div>
            <div className="grid gap-4 px-4 py-4 lg:grid-cols-2">
              <DistributionBlock
                title="相似度分桶"
                rows={[
                  { label: "high", value: profile.high, color: "bg-emerald-600" },
                  { label: "mid", value: profile.mid, color: "bg-cyan-600" },
                  { label: "low", value: profile.low, color: "bg-amber-500" },
                  { label: "far", value: profile.far, color: "bg-muted-foreground" },
                ]}
                total={Math.max(1, hits.length)}
              />
              <DistributionBlock
                title="来源范围"
                rows={[
                  { label: "personal", value: profile.personalRows, color: "bg-emerald-600" },
                  { label: "project", value: profile.projectRows, color: "bg-cyan-600" },
                  { label: "community", value: profile.communityRows, color: "bg-slate-500" },
                ]}
                total={Math.max(1, hits.length)}
              />
            </div>
          </div>

          <div className="rounded-lg border border-border/70 bg-white shadow-sm">
            <div className="flex items-center gap-2 border-b border-border/60 px-4 py-3 text-sm">
              <CheckCircle2 className="h-4 w-4 text-cyan-700" />
              <span className="font-semibold">Top-K 命中</span>
            </div>
            {!q ? (
              <div className="px-4 py-8 text-center text-sm text-muted-foreground">
                输入一条任务描述后，这里会显示自动召回会选中的经验。
              </div>
            ) : hits.length === 0 ? (
              <div className="px-4 py-8 text-center text-sm text-muted-foreground">
                当前查询没有命中。可以放宽任务类型，或换成更具体的关键词。
              </div>
            ) : (
              <ul className="divide-y divide-border/60">
                {hits.map((hit, index) => (
                  <RecallHitRow
                    key={hit.chunk_id}
                    hit={hit}
                    experience={experiences.get(hit.experience_id)}
                    rank={index + 1}
                  />
                ))}
              </ul>
            )}
          </div>
        </div>

        <aside className="flex flex-col gap-4">
          <div className="rounded-lg border border-border/70 bg-white shadow-sm">
            <div className="flex items-center justify-between gap-2 border-b border-border/60 px-4 py-3">
              <div className="flex items-center gap-2 text-sm font-semibold">
                <Sparkles className="h-4 w-4 text-cyan-700" />
                注入上下文预览
              </div>
              <CopyButton text={contextText} label="复制" />
            </div>
            <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap px-4 py-4 text-xs leading-5 text-muted-foreground">
              {contextText}
            </pre>
          </div>

          <div className="rounded-lg border border-border/70 bg-white px-4 py-4 shadow-sm">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
              <Radar className="h-4 w-4 text-cyan-700" />
              Hook 配置
            </div>
            <div className="space-y-2 text-xs text-muted-foreground">
              <ConfigLine name="EXPOOL_AUTO_SEARCH" value="1" note="设 0 临时关闭" />
              <ConfigLine name="EXPOOL_AUTO_SEARCH_TOP_K" value={String(topK)} note="默认 3" />
              <ConfigLine name="EXPOOL_AUTO_SEARCH_MIN_CHARS" value="20" note="短消息跳过" />
              <ConfigLine name="EXPOOL_AUTO_SEARCH_TIMEOUT" value="8" note="秒" />
            </div>
            <div className="mt-4 rounded-md border border-border/70 bg-muted/30 p-3 text-xs leading-5 text-muted-foreground">
              插件路径：`hooks/hooks.json` 注册 `UserPromptSubmit`，执行 `scripts/auto-search.sh`，成功后把结果作为 additionalContext 注入模型。
            </div>
          </div>
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
  value: number;
  detail: string;
}) {
  return (
    <div className="rounded-lg border border-border/70 bg-white px-4 py-3 shadow-sm">
      <div className="mb-2 flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          {icon}
          {label}
        </span>
        <span>{detail}</span>
      </div>
      <div className="font-mono text-2xl font-semibold text-foreground">{value}</div>
    </div>
  );
}

function DistributionBlock({
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
            <div key={row.label} className="grid grid-cols-[84px_minmax(0,1fr)_44px] items-center gap-2 text-xs">
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

function RecallHitRow({
  hit,
  experience,
  rank,
}: {
  hit: RagChunkResult;
  experience?: RagExperienceResult;
  rank: number;
}) {
  const sim = Math.round(hit.similarity * 100);
  const simClass =
    sim >= 70
      ? "bg-emerald-100 text-emerald-900"
      : sim >= 40
      ? "bg-cyan-100 text-cyan-900"
      : sim >= 15
      ? "bg-amber-100 text-amber-900"
      : "bg-muted text-muted-foreground";

  return (
    <li className="px-4 py-3">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded bg-muted/70 px-1.5 py-0.5 font-mono text-muted-foreground">#{rank}</span>
        <Badge className="bg-cyan-50 text-cyan-900">score {hit.score.toFixed(2)}</Badge>
        <Badge className={simClass}>sim {sim}%</Badge>
        <Badge className={sensitivityColor(hit.sensitivity)}>{hit.sensitivity}</Badge>
        <Badge variant="outline" className="font-mono">
          {hit.source}:{hit.chunk_type}
        </Badge>
        <Badge variant="outline" className="font-mono">
          {hit.task_type}
        </Badge>
        <span className="text-muted-foreground">{hit.agent_name}</span>
        <span className="text-muted-foreground">· {formatDate(hit.created_at)}</span>
        <Link
          href={`/experiences/${hit.experience_id}`}
          className="ml-auto font-mono text-[11px] text-cyan-700 hover:underline"
        >
          {shortId(hit.experience_id)}
        </Link>
      </div>
      <Link
        href={`/experiences/${hit.experience_id}`}
        className="text-sm font-medium text-foreground hover:text-cyan-800 hover:underline"
      >
        {experience?.intent_text || experience?.query || firstLine(hit.text) || "(no title)"}
      </Link>
      <p className="mt-1 line-clamp-3 text-xs leading-5 text-muted-foreground">
        {hit.text.slice(0, 420)}
      </p>
      <div className="mt-2 flex flex-wrap gap-2 text-[10px] font-mono text-muted-foreground">
        <span>turns {formatTurnRange(hit.turn_start, hit.turn_end)}</span>
        <span>lex {hit.lexical.toFixed(2)}</span>
        <span>action {hit.action_lexical.toFixed(2)}</span>
        <span>situation {hit.situation_lexical.toFixed(2)}</span>
        <span title={hit.parent_session_id ?? undefined}>
          parent {shortId(hit.parent_session_id || hit.experience_id)}
        </span>
      </div>
    </li>
  );
}

function ConfigLine({ name, value, note }: { name: string; value: string; note: string }) {
  return (
    <div className="flex items-center justify-between gap-2 rounded-md border border-border/60 px-2.5 py-2">
      <span className="font-mono text-[11px] text-foreground">{name}</span>
      <span className="text-right">
        <span className="font-mono">{value}</span>
        <span className="ml-2 text-muted-foreground/80">{note}</span>
      </span>
    </div>
  );
}

function buildRecallProfile(hits: RagChunkResult[]) {
  return {
    high: hits.filter((h) => h.similarity >= 0.7).length,
    mid: hits.filter((h) => h.similarity >= 0.4 && h.similarity < 0.7).length,
    low: hits.filter((h) => h.similarity >= 0.15 && h.similarity < 0.4).length,
    far: hits.filter((h) => h.similarity < 0.15).length,
    personalRows: hits.filter((h) => h.source === "personal").length,
    projectRows: hits.filter((h) => h.source === "project").length,
    communityRows: hits.filter((h) => h.source === "community").length,
    parentSessions: new Set(
      hits.map((h) => h.parent_session_id || h.session_id || h.experience_id),
    ).size,
  };
}

function buildEmptyContext(
  q: string,
  topK: number,
  loggedIn: boolean,
  error?: string,
): string {
  if (!q) {
    return "【经验池RAG上下文】\n输入任务描述后，这里会预览插件实际注入的 chunk 上下文。";
  }
  if (!loggedIn) {
    return `【经验池RAG上下文】\nquery: ${q}\n\n登录后才能按个人私有池权限执行召回。`;
  }
  return `【经验池RAG上下文】\nquery: ${q}\ntop-k: ${topK}\n\n${error || "未找到达到相关性阈值的经验。"}`;
}

function firstLine(text: string): string {
  return text.split(/\r?\n/, 1)[0]?.trim() ?? "";
}

function formatTurnRange(start: number | null, end: number | null): string {
  if (start == null) return "-";
  if (end == null || end === start) return String(start);
  return `${start}-${end}`;
}
