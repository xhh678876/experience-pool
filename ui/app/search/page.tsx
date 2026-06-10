import Link from "@/components/ui/link";
import { Database, Search, Sparkles, Filter, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import {
  listAgents,
  searchMvpExperiences,
  type MvpExperienceHit,
} from "@/lib/mvp-queries";
import { distinctValues } from "@/lib/queries";
import { formatDate, sensitivityColor, shortId } from "@/lib/utils";

export const dynamic = "force-dynamic";

type SearchParams = {
  q?: string;
  agent?: string;
  task?: string;
  sensitivity?: string;
  acl?: string;
  topK?: string;
};

const ACL_LABELS: Record<string, string> = {
  all: "全部 ACL",
  public: "public",
  org: "org",
  team: "team:*",
  private: "private",
};

const ACL_OPTIONS = ["all", "public", "org", "team", "private"];

export default async function SearchPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp = await searchParams;
  const agents = listAgents();
  const viewer = sp.agent ?? agents[0]?.name ?? "";
  const task = sp.task ?? "all";
  const sensitivity = sp.sensitivity ?? "all";
  const acl = sp.acl ?? "all";
  const topK = Math.max(1, Math.min(50, Number(sp.topK ?? "12")));
  const q = (sp.q ?? "").trim();

  // Pull a wider set; we'll filter client-side by sensitivity / ACL.
  const rawHits: MvpExperienceHit[] = q
    ? await searchMvpExperiences({
        viewerName: viewer,
        query: q,
        taskType: task,
        topK: 50,
      })
    : ([] as MvpExperienceHit[]);

  const filtered = rawHits.filter((h) => {
    if (sensitivity !== "all" && h.sensitivity !== sensitivity) return false;
    if (acl === "all") return true;
    if (acl === "public") return h.acl === "public";
    if (acl === "org") return h.acl === "org";
    if (acl === "private") return h.acl === "private";
    if (acl === "team") return h.acl.startsWith("team:");
    return true;
  });
  const hits = filtered.slice(0, topK);

  const taskTypes = distinctValues("task_type");
  const sensitivities = distinctValues("sensitivity");
  const hasFilters = Boolean(q || task !== "all" || sensitivity !== "all" || acl !== "all");

  return (
    <div className="flex flex-col gap-6 pb-12">
      {/* HEADER */}
      <section className="flex flex-col gap-3 rounded-2xl border border-border/60 bg-white/85 px-5 py-4">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Search className="h-4 w-4" />
          <span className="font-semibold text-foreground">向量检索</span>
          <span>·</span>
          <span>语义召回 + 多维过滤 + ACL 隔离</span>
        </div>

        <form className="flex flex-col gap-2 lg:flex-row lg:items-end">
          <div className="flex-1 min-w-0">
            <label className="mb-1 block text-xs text-muted-foreground">查询语句</label>
            <Input
              name="q"
              defaultValue={q}
              placeholder="例如：FastAPI HMAC 验签 / k8s pod OOM 排查 / SQLite WAL 配置"
              className="h-10 text-sm"
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <div>
              <label className="mb-1 block text-xs text-muted-foreground">视角 agent</label>
              <Select name="agent" defaultValue={viewer} className="h-10 min-w-[140px]">
                {agents.map((a) => (
                  <option key={a.agent_id} value={a.name}>
                    {a.name} ({a.team})
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <label className="mb-1 block text-xs text-muted-foreground">任务类型</label>
              <Select name="task" defaultValue={task} className="h-10 min-w-[140px]">
                <option value="all">全部</option>
                {taskTypes.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <label className="mb-1 block text-xs text-muted-foreground">敏感度</label>
              <Select name="sensitivity" defaultValue={sensitivity} className="h-10 min-w-[110px]">
                <option value="all">全部</option>
                {sensitivities.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <label className="mb-1 block text-xs text-muted-foreground">ACL</label>
              <Select name="acl" defaultValue={acl} className="h-10 min-w-[110px]">
                {ACL_OPTIONS.map((a) => (
                  <option key={a} value={a}>
                    {ACL_LABELS[a]}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <label className="mb-1 block text-xs text-muted-foreground">Top K</label>
              <Select name="topK" defaultValue={String(topK)} className="h-10 min-w-[80px]">
                {[6, 12, 24, 50].map((n) => (
                  <option key={n} value={String(n)}>
                    {n}
                  </option>
                ))}
              </Select>
            </div>
          </div>
          <div className="flex items-end gap-2">
            <Button type="submit" className="h-10 px-4">
              <Search className="mr-1 h-3.5 w-3.5" />
              搜索
            </Button>
            {hasFilters ? (
              <Link href="/search">
                <Button type="button" variant="ghost" className="h-10 px-3 text-xs">
                  <X className="mr-1 h-3.5 w-3.5" />
                  清空
                </Button>
              </Link>
            ) : null}
          </div>
        </form>
      </section>

      {/* RESULTS */}
      {q ? (
        <section className="rounded-2xl border border-border/60 bg-white/85">
          <div className="flex items-center gap-2 border-b border-border/60 px-5 py-3 text-sm">
            <Sparkles className="h-4 w-4 text-cyan-700" />
            <span className="font-semibold">结果</span>
            <span className="text-muted-foreground">
              · {hits.length} 条命中 (从 {rawHits.length} 个候选过滤后)
            </span>
            {hits.length === 0 && rawHits.length > 0 ? (
              <Badge className="ml-2 bg-amber-100 text-amber-900">过滤后为空 — 试试放宽筛选</Badge>
            ) : null}
          </div>
          {hits.length === 0 ? (
            <div className="px-5 py-10 text-center text-sm text-muted-foreground">
              {rawHits.length === 0
                ? "未找到匹配。embedding 是基于 256 维 trigram 哈希的轻量算法，建议查询使用具体关键词。"
                : "命中过滤条件后为空，调整 ACL/敏感度/任务类型再试。"}
            </div>
          ) : (
            <ul className="divide-y divide-border/60">
              {hits.map((h, i) => (
                <ResultRow key={h.experience_id} hit={h} rank={i + 1} q={q} />
              ))}
            </ul>
          )}
        </section>
      ) : (
        <section className="rounded-2xl border border-dashed border-border/60 bg-muted/30 px-6 py-10 text-center">
          <Search className="mx-auto mb-3 h-6 w-6 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            输入查询开始检索。检索使用 256 维 trigram 哈希 embedding + 余弦相似度，
            ACL 在 SQL 之外二次过滤。
          </p>
          <p className="mt-1 text-xs text-muted-foreground/70">
            提示：vector + 你选的 agent 视角共同决定召回范围；选不同 agent 看到不同结果。
          </p>
        </section>
      )}
    </div>
  );
}

function ResultRow({
  hit,
  rank,
  q,
}: {
  hit: MvpExperienceHit;
  rank: number;
  q: string;
}) {
  const sim = hit.similarity ?? 0;
  const simPercent = Math.round(sim * 100);
  const simBucket =
    sim > 0.7 ? "high" : sim > 0.4 ? "mid" : sim > 0.15 ? "low" : "far";
  const simColor =
    simBucket === "high"
      ? "bg-emerald-100 text-emerald-900"
      : simBucket === "mid"
      ? "bg-cyan-100 text-cyan-900"
      : simBucket === "low"
      ? "bg-amber-100 text-amber-900"
      : "bg-muted text-muted-foreground";

  return (
    <li className="flex flex-col gap-2 px-5 py-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded bg-muted/60 px-1.5 py-0.5 font-mono text-muted-foreground">
          #{rank}
        </span>
        <Badge className={simColor}>sim {simPercent}%</Badge>
        <Badge className={sensitivityColor(hit.sensitivity)}>{hit.sensitivity}</Badge>
        <Badge variant="outline" className="font-mono">
          {hit.task_type}
        </Badge>
        <Badge variant="outline" className="font-mono">
          {hit.acl}
        </Badge>
        <span className="text-muted-foreground">
          {hit.agent_name} ({hit.team})
        </span>
        <span className="text-muted-foreground">·</span>
        <span className="text-muted-foreground">{formatDate(hit.created_at)}</span>
        <Link
          href={`/experiences/${hit.experience_id}`}
          className="ml-auto font-mono text-[11px] text-cyan-700 hover:underline"
        >
          {shortId(hit.experience_id)}
        </Link>
      </div>
      <div>
        <Link
          href={`/experiences/${hit.experience_id}`}
          className="text-sm font-medium hover:text-cyan-800 hover:underline"
        >
          {highlight(hit.intent || hit.query || "(no title)", q)}
        </Link>
      </div>
      {hit.outcome ? (
        <p className="text-xs leading-5 text-muted-foreground line-clamp-2">
          {highlight(hit.outcome.slice(0, 240), q)}
        </p>
      ) : null}
      {hit.steps.length > 0 ? (
        <p className="text-[11px] text-muted-foreground/80">
          {hit.steps.length} step · 模型 {hit.source_model} · 复用 {hit.visit_count}
        </p>
      ) : null}
    </li>
  );
}

function highlight(text: string, q: string): React.ReactNode {
  if (!q) return text;
  const tokens = Array.from(
    new Set(
      q
        .toLowerCase()
        .split(/\s+/)
        .filter((t) => t.length >= 2),
    ),
  );
  if (tokens.length === 0) return text;
  const escaped = tokens.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const re = new RegExp(`(${escaped.join("|")})`, "gi");
  const parts = text.split(re);
  return parts.map((part, i) =>
    re.test(part) ? (
      <mark key={i} className="bg-amber-200/60 px-0.5 rounded-sm">
        {part}
      </mark>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}
