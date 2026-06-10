import Link, { withBase } from "@/components/ui/link";
import { distinctValues, listExperiences } from "@/lib/queries";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { formatDate, sensitivityColor, shortId, statusColor } from "@/lib/utils";
import { Database, Filter, Search, X } from "lucide-react";

export const dynamic = "force-dynamic";

type SearchParams = {
  status?: string;
  task?: string;
  sensitivity?: string;
  q?: string;
};

const STATUS_LABELS: Record<string, string> = {
  all: "全部状态",
  pending: "待审",
  approved: "已通过",
  auto_approved: "自动通过",
  rejected: "已拒",
  edited: "编辑过",
};

export default async function ExperiencesPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp = await searchParams;
  const items = await listExperiences({
    reviewStatus: sp.status,
    taskType: sp.task,
    sensitivity: sp.sensitivity,
    search: sp.q,
    limit: 200,
  });

  const taskTypes = distinctValues("task_type");
  const sensitivities = distinctValues("sensitivity");
  const statuses = distinctValues("review_status");
  const hasFilters = Boolean(sp.status || sp.task || sp.sensitivity || sp.q);

  return (
    <div className="flex flex-col gap-6 pb-12">
      {/* HEADER */}
      <section className="flex flex-col gap-3 rounded-2xl border border-border/60 bg-white/85 px-5 py-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Database className="h-3.5 w-3.5" />
            全库
          </div>
          <h1 className="mt-1 text-xl font-semibold tracking-tight">经验库</h1>
          <p className="mt-1 text-xs text-muted-foreground">
            当前条件命中 <span className="font-mono text-foreground">{items.length}</span> 条 ·
            按上传时间倒序
          </p>
        </div>
        <form
          action={withBase("/experiences")}
          method="get"
          className="flex w-full items-center gap-2 sm:w-auto"
        >
          {sp.status ? <input type="hidden" name="status" value={sp.status} /> : null}
          {sp.task ? <input type="hidden" name="task" value={sp.task} /> : null}
          {sp.sensitivity ? <input type="hidden" name="sensitivity" value={sp.sensitivity} /> : null}
          <div className="flex w-full items-center gap-2 rounded-xl border border-border/60 bg-white px-3 py-1.5 sm:w-72">
            <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <Input
              name="q"
              defaultValue={sp.q ?? ""}
              placeholder="搜索 intent 或 ID"
              className="h-7 border-0 bg-transparent px-0 text-sm shadow-none focus-visible:ring-0"
            />
          </div>
          <Button type="submit" size="sm" className="h-9 rounded-xl px-4">
            搜索
          </Button>
          {hasFilters ? (
            <Link
              href="/experiences"
              className="inline-flex h-9 items-center gap-1 rounded-xl border border-border/60 bg-white/80 px-3 text-xs text-muted-foreground hover:border-rose-400/50 hover:text-rose-700"
            >
              <X className="h-3.5 w-3.5" />
              清空
            </Link>
          ) : null}
        </form>
      </section>

      {/* FILTER CHIPS */}
      <section className="flex flex-col gap-2 rounded-2xl border border-border/60 bg-white/70 px-4 py-3">
        <FilterRow
          icon={<Filter className="h-3.5 w-3.5" />}
          label="状态"
          name="status"
          values={["all", ...statuses]}
          current={sp.status ?? "all"}
          sp={sp}
          renderLabel={(v) => STATUS_LABELS[v] ?? v}
        />
        <FilterRow
          label="任务"
          name="task"
          values={["all", ...taskTypes]}
          current={sp.task ?? "all"}
          sp={sp}
        />
        <FilterRow
          label="敏感度"
          name="sensitivity"
          values={["all", ...sensitivities]}
          current={sp.sensitivity ?? "all"}
          sp={sp}
        />
      </section>

      {/* CARD GRID */}
      {items.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border/70 bg-white/50 px-6 py-16 text-center">
          <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-lg border border-border/70 bg-muted/40">
            <Search className="h-4 w-4 text-muted-foreground" />
          </div>
          <div className="mt-3 text-sm font-medium">没有匹配的经验</div>
          <p className="mt-1 text-xs text-muted-foreground">
            换个筛选条件或直接
            <Link href="/experiences" className="ml-1 underline-offset-4 hover:underline">
              清空筛选
            </Link>
            。
          </p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {items.map((e) => (
            <Link
              key={e.experience_id}
              href={`/experiences/${e.experience_id}`}
              className="group flex flex-col gap-3 rounded-xl border border-border/60 bg-white/95 p-4 transition hover:-translate-y-0.5 hover:border-cyan-600/40 hover:shadow-lg hover:shadow-cyan-900/5"
            >
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge className={`text-[11px] ${statusColor(e.review_status)}`}>
                  {STATUS_LABELS[e.review_status] ?? e.review_status}
                </Badge>
                <Badge className={`text-[11px] ${sensitivityColor(e.sensitivity)}`}>
                  {e.sensitivity}
                </Badge>
                <Badge variant="outline" className="border-border/60 bg-white/40 text-[11px] text-muted-foreground">
                  {e.task_type}
                </Badge>
                {!e.trajectory_path ? (
                  <Badge
                    variant="outline"
                    className="border-amber-500/40 bg-amber-50 text-[11px] text-amber-800"
                    title="只上传了卡片摘要,没有原始对话(--no-trace 或老 client)"
                  >
                    无原文
                  </Badge>
                ) : null}
              </div>
              <h3 className="line-clamp-2 text-sm font-semibold leading-6 text-foreground group-hover:text-cyan-900">
                {e.intent_text || "(无 intent)"}
              </h3>
              <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                <span className="font-mono">{shortId(e.experience_id)}</span>
                <span className="font-mono">{e.source_model}</span>
              </div>
              <div className="mt-auto flex items-center justify-between gap-3 border-t border-border/50 pt-3 text-[11px]">
                <span className="text-muted-foreground">
                  Q <span className="font-mono text-foreground">{e.q_scalar.toFixed(2)}</span>
                  <span className="mx-1.5">·</span>
                  reuse <span className="font-mono text-foreground">{e.reuse_count}</span>
                </span>
                <span className="font-mono text-muted-foreground">{formatDate(e.created_at)}</span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function FilterRow({
  icon,
  label,
  name,
  values,
  current,
  sp,
  renderLabel,
}: {
  icon?: React.ReactNode;
  label: string;
  name: keyof SearchParams;
  values: string[];
  current: string;
  sp: SearchParams;
  renderLabel?: (v: string) => string;
}) {
  function buildHref(value: string): string {
    const params = new URLSearchParams();
    const next: SearchParams = { ...sp, [name]: value };
    for (const [k, v] of Object.entries(next)) {
      if (v && v !== "all") params.set(k, String(v));
    }
    const qs = params.toString();
    return `/experiences${qs ? `?${qs}` : ""}`;
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="inline-flex shrink-0 items-center gap-1 text-[11px] uppercase tracking-wider text-muted-foreground">
        {icon}
        {label}
      </span>
      {values.map((v) => {
        const active = v === current;
        const labelText = renderLabel ? renderLabel(v) : v === "all" ? "全部" : v;
        return (
          <Link
            key={v}
            href={buildHref(v)}
            className={
              "inline-flex h-7 items-center gap-1 rounded-full border px-3 text-xs transition " +
              (active
                ? "border-cyan-700 bg-cyan-700 text-white shadow-sm"
                : "border-border/60 bg-white text-muted-foreground hover:border-cyan-600/40 hover:text-cyan-800")
            }
          >
            {labelText}
          </Link>
        );
      })}
    </div>
  );
}
