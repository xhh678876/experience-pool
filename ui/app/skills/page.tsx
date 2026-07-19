import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import Link, { withBase } from "@/components/ui/link";
import { listSkills } from "@/lib/skills-queries";
import { Search, Wrench, X } from "lucide-react";

export const dynamic = "force-dynamic";

interface SkillsPageProps {
  searchParams?: Promise<{
    review_status?: string;
    search?: string;
  }>;
}

const STATUS_LABELS: Record<string, string> = {
  all: "全部",
  auto_approved: "自动通过",
  approved: "已通过",
  pending: "待审",
  rejected: "已拒",
  edited: "编辑过",
};

export default async function SkillsPage({ searchParams }: SkillsPageProps) {
  const params = (await searchParams) ?? {};
  const reviewStatus =
    params.review_status && params.review_status !== "all" ? params.review_status : undefined;
  const search = params.search?.trim() ? params.search : undefined;
  const items = await listSkills({ reviewStatus, search });
  const hasFilters = Boolean(reviewStatus || search);

  const statuses = ["all", "auto_approved", "approved", "pending", "rejected", "edited"];

  return (
    <div className="flex flex-col gap-6 pb-12">
      <section className="flex flex-col gap-3 rounded-2xl border border-border/60 bg-white/85 px-5 py-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Wrench className="h-3.5 w-3.5" />
            技能市场
          </div>
          <h1 className="mt-1 text-xl font-semibold tracking-tight">技能 bundle</h1>
          <p className="mt-1 text-xs text-muted-foreground">
            可复用的 SKILL.md + 辅助文件，跨 Agent 共享 ·
            当前 <span className="font-mono text-foreground">{items.length}</span> 个
          </p>
        </div>
        <form
          method="get"
          action={withBase("/skills")}
          className="flex w-full items-center gap-2 sm:w-auto"
        >
          {reviewStatus ? <input type="hidden" name="review_status" value={reviewStatus} /> : null}
          <div className="flex w-full items-center gap-2 rounded-xl border border-border/60 bg-white px-3 py-1.5 sm:w-72">
            <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <Input
              name="search"
              defaultValue={search ?? ""}
              placeholder="搜索名称、描述或 skill_id"
              className="h-7 border-0 bg-transparent px-0 text-sm shadow-none focus-visible:ring-0"
            />
          </div>
          <Button type="submit" size="sm" className="h-9 rounded-xl px-4">
            搜索
          </Button>
          {hasFilters ? (
            <Link
              href="/skills"
              className="inline-flex h-9 items-center gap-1 rounded-xl border border-border/60 bg-white/80 px-3 text-xs text-muted-foreground hover:border-rose-400/50 hover:text-rose-700"
            >
              <X className="h-3.5 w-3.5" />
              清空
            </Link>
          ) : null}
        </form>
      </section>

      <section className="flex flex-wrap items-center gap-2 rounded-2xl border border-border/60 bg-white/70 px-4 py-3">
        <span className="text-[11px] uppercase tracking-wider text-muted-foreground">状态</span>
        {statuses.map((s) => {
          const active = s === (reviewStatus ?? "all");
          const params = new URLSearchParams();
          if (s !== "all") params.set("review_status", s);
          if (search) params.set("search", search);
          const qs = params.toString();
          return (
            <Link
              key={s}
              href={`/skills${qs ? `?${qs}` : ""}`}
              className={
                "inline-flex h-7 items-center rounded-full border px-3 text-xs transition " +
                (active
                  ? "border-cyan-700 bg-cyan-700 text-white shadow-sm"
                  : "border-border/60 bg-white text-muted-foreground hover:border-cyan-600/40 hover:text-cyan-800")
              }
            >
              {STATUS_LABELS[s] ?? s}
            </Link>
          );
        })}
      </section>

      {items.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border/70 bg-white/50 px-6 py-16 text-center">
          <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-lg border border-border/70 bg-muted/40">
            <Wrench className="h-4 w-4 text-muted-foreground" />
          </div>
          <div className="mt-3 text-sm font-medium">暂无技能 bundle</div>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">
            可以用{" "}
            <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]">
              expctl push-skill --bundle ./my-skill --agent &lt;name&gt;
            </code>{" "}
            上传。
          </p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {items.map((s) => (
            <Link
              key={s.skill_id}
              href={`/skills/${s.skill_id}`}
              className="group flex flex-col gap-3 rounded-xl border border-border/60 bg-white/95 p-4 transition hover:-translate-y-0.5 hover:border-cyan-600/40 hover:shadow-lg hover:shadow-cyan-900/5"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="line-clamp-1 text-sm font-semibold text-foreground group-hover:text-cyan-900">
                    {s.name}
                  </div>
                  <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                    v{s.version}
                  </div>
                </div>
                <Badge className="shrink-0 border-cyan-600/25 bg-cyan-50 text-[11px] text-cyan-800">
                  Q {s.q_scalar.toFixed(2)}
                </Badge>
              </div>

              <p className="line-clamp-3 text-xs leading-5 text-muted-foreground">
                {s.description || "(无描述)"}
              </p>

              <div className="flex flex-wrap items-center gap-1.5">
                <Badge variant="outline" className="border-border/60 bg-white/40 text-[11px] text-muted-foreground">
                  {STATUS_LABELS[s.review_status] ?? s.review_status}
                </Badge>
                <Badge variant="outline" className="border-border/60 bg-white/40 text-[11px] text-muted-foreground">
                  {s.acl}
                </Badge>
                <Badge variant="outline" className="border-border/60 bg-white/40 text-[11px] text-muted-foreground">
                  {s.sensitivity}
                </Badge>
                {s.sanitization_status === "human_review" ? (
                  <Badge className="border-rose-500/30 bg-rose-50 text-[11px] text-rose-700">
                    脱敏需复核
                  </Badge>
                ) : null}
              </div>

              <div className="mt-auto flex items-center justify-between gap-3 border-t border-border/50 pt-3 text-[11px] text-muted-foreground">
                <span>
                  {s.file_count} 文件 · {(s.bundle_size_bytes / 1024).toFixed(1)} KB
                </span>
                <span>
                  调用 <span className="font-mono text-foreground">{s.invoke_count}</span> ·
                  安装 <span className="font-mono text-foreground">{s.install_count}</span>
                </span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
