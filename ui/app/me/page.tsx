import Link from "@/components/ui/link";
import {
  ShieldCheck,
  AlertTriangle,
  FileText,
  Trash2,
  Globe2,
  Lock,
  Sparkles,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  listMyExperiences,
  getOwnerQuota,
  getMyExperienceStats,
} from "@/lib/me-queries";
import { getReviewerName } from "@/lib/auth";
import { formatDate, shortId } from "@/lib/utils";
import RevokeButton from "./RevokeButton";
import PublishButton from "./PublishButton";
import BackfillCard from "./BackfillCard";

export const dynamic = "force-dynamic";

interface PageProps {
  searchParams: Promise<{ include_revoked?: string; page?: string }>;
}

const PAGE_SIZE = 200;

export default async function MePage({ searchParams }: PageProps) {
  const params = await searchParams;
  const includeRevoked = params.include_revoked === "1";
  const currentPage = Math.max(1, Number.parseInt(params.page ?? "1", 10) || 1);
  const viewer = await getReviewerName();
  const [quota, stats] = await Promise.all([
    getOwnerQuota(viewer),
    getMyExperienceStats(viewer),
  ]);

  const totalForView = includeRevoked ? stats.total : stats.live;
  const totalPages = Math.max(1, Math.ceil(totalForView / PAGE_SIZE));
  const safePage = Math.min(currentPage, totalPages);
  const offset = (safePage - 1) * PAGE_SIZE;
  const rows = await listMyExperiences(viewer, {
    includeRevoked,
    limit: PAGE_SIZE,
    offset,
  });
  const firstShown = totalForView === 0 ? 0 : offset + 1;
  const lastShown = Math.min(offset + rows.length, totalForView);
  const progressPct = Math.min(
    100,
    (quota.publish_count / quota.threshold) * 100
  );
  const pageHref = (page: number) => {
    const q = new URLSearchParams();
    if (includeRevoked) q.set("include_revoked", "1");
    if (page > 1) q.set("page", String(page));
    const qs = q.toString();
    return `/me${qs ? `?${qs}` : ""}`;
  };

  return (
    <div className="flex flex-col gap-6 pb-12">
      <BackfillCard />
      <section className="flex flex-col gap-3 rounded-2xl border border-border/60 bg-white/85 px-5 py-4">
        <div className="flex items-center gap-2 text-sm">
          <ShieldCheck className="h-4 w-4 text-cyan-700" />
          <span className="font-semibold">我的经验池</span>
          <span className="text-muted-foreground">
            · owner: <code className="font-mono">{quota.owner}</code>
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          <Badge className="bg-cyan-100 text-cyan-900 font-mono">{stats.live} 私有</Badge>
          <Badge className="bg-emerald-100 text-emerald-900 font-mono">
            <Globe2 className="mr-0.5 inline h-3 w-3" />
            {stats.published} 已发布
          </Badge>
          {stats.revoked > 0 ? (
            <Badge className="bg-rose-100 text-rose-900 font-mono">
              {stats.revoked} 已撤回
            </Badge>
          ) : null}
          <Link
            href={includeRevoked ? "/me" : "/me?include_revoked=1"}
            className="text-cyan-700 hover:underline"
          >
            {includeRevoked ? "隐藏已撤回" : "显示已撤回"}
          </Link>
        </div>

        {/* 社区池解锁进度 */}
        <div className="rounded-md border border-border/40 bg-muted/20 px-3 py-2.5">
          <div className="mb-1.5 flex items-center justify-between gap-2 text-xs">
            <span className="inline-flex items-center gap-1.5 font-medium">
              {quota.community_unlocked ? (
                <>
                  <Sparkles className="h-3.5 w-3.5 text-emerald-600" />
                  <span className="text-emerald-800">社区池已解锁</span>
                </>
              ) : (
                <>
                  <Lock className="h-3.5 w-3.5 text-amber-600" />
                  <span className="text-amber-800">社区池未解锁</span>
                </>
              )}
            </span>
            <span className="font-mono text-muted-foreground">
              {quota.publish_count} / {quota.threshold}
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-muted/60">
            <div
              className={
                quota.community_unlocked
                  ? "h-full bg-emerald-500 transition-all"
                  : "h-full bg-cyan-500 transition-all"
              }
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <p className="mt-1.5 text-[11px] text-muted-foreground">
            {quota.hint}
            {quota.community_unlocked ? (
              <>
                {" · "}
                <Link
                  href="/community"
                  className="text-cyan-700 hover:underline"
                >
                  浏览社区池 →
                </Link>
              </>
            ) : null}
          </p>
        </div>

        <div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-50/70 px-3 py-2 text-xs leading-5 text-amber-900">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-none" />
          <span>
            <strong>发布到社区前会跑严格脱敏</strong>：屏蔽
            <code className="mx-1 font-mono">file://</code>、本地路径、
            <code className="mx-1 font-mono">localhost</code> URL、UUID
            等。任何命中都会拒绝发布并告诉你具体位置。
          </span>
        </div>
      </section>

      {rows.length === 0 ? (
        <section className="rounded-2xl border border-dashed border-border/60 bg-muted/30 px-6 py-10 text-center">
          <FileText className="mx-auto h-8 w-8 text-muted-foreground" />
          <p className="mt-3 text-sm text-muted-foreground">
            owner <code className="font-mono">{quota.owner}</code> 还没上传过经验
          </p>
        </section>
      ) : (
        <section className="rounded-2xl border border-border/60 bg-white/85">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/60 px-4 py-3 text-sm">
            <div>
              <span className="font-semibold">{totalForView}</span>
              <span className="text-muted-foreground">
                {" "}条 · 当前 {firstShown}-{lastShown} · 按上传时间倒序
              </span>
            </div>
            <PaginationControls
              currentPage={safePage}
              totalPages={totalPages}
              pageHref={pageHref}
            />
          </div>
          <ul className="divide-y divide-border/60">
            {rows.map((r) => {
              const isRevoked = !!r.revoked;
              const publishStatus = r.publish_status ?? "private";
              return (
                <li
                  key={r.experience_id}
                  className={`px-4 py-3 ${isRevoked ? "opacity-60" : ""}`}
                >
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <Link
                      href={isRevoked ? "#" : `/experiences/${r.experience_id}`}
                      className={`font-mono text-[11px] ${
                        isRevoked
                          ? "cursor-not-allowed text-muted-foreground line-through"
                          : "text-cyan-700 hover:underline"
                      }`}
                    >
                      {shortId(r.experience_id)}
                    </Link>
                    <Badge variant="outline" className="font-mono">
                      {r.task_type}
                    </Badge>
                    {!r.trajectory_path ? (
                      <Badge
                        variant="outline"
                        className="border-amber-500/40 bg-amber-50 text-[10px] text-amber-800 font-mono"
                        title="只上传了卡片,没有原始对话"
                      >
                        无原文
                      </Badge>
                    ) : null}
                    {publishStatus === "published" ? (
                      <Badge className="bg-emerald-100 text-emerald-900 font-mono text-[10px]">
                        <Globe2 className="mr-0.5 inline h-3 w-3" />
                        已发布
                      </Badge>
                    ) : publishStatus === "rejected" ? (
                      <Badge className="bg-amber-100 text-amber-900 font-mono text-[10px]">
                        发布被拦截
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="font-mono text-[10px]">
                        私有
                      </Badge>
                    )}
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
                            : r.trajectory_score >= 0
                            ? "bg-cyan-100 text-cyan-900 font-mono text-[10px]"
                            : "bg-rose-100 text-rose-900 font-mono text-[10px]"
                        }
                      >
                        score {r.trajectory_score >= 0 ? "+" : ""}
                        {r.trajectory_score.toFixed(2)}
                      </Badge>
                    ) : null}
                    {isRevoked ? (
                      <Badge className="bg-rose-100 text-rose-900 font-mono">
                        <Trash2 className="mr-0.5 inline h-3 w-3" />
                        revoked {r.revoked_at ? formatDate(r.revoked_at) : ""}
                      </Badge>
                    ) : null}
                    <span className="ml-auto text-muted-foreground">
                      {formatDate(r.created_at)}
                    </span>
                  </div>
                  <div className="mt-1 flex items-start justify-between gap-3">
                    <p className="line-clamp-2 flex-1 text-sm">
                      {isRevoked ? (
                        <span className="italic text-muted-foreground">
                          (内容已删除)
                        </span>
                      ) : (
                        r.intent_text || r.query || "(no intent)"
                      )}
                    </p>
                    {!isRevoked ? (
                      <div className="flex shrink-0 items-center gap-1.5">
                        <PublishButton
                          experienceId={r.experience_id}
                          currentStatus={publishStatus}
                        />
                        <RevokeButton experienceId={r.experience_id} />
                      </div>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
          {totalPages > 1 ? (
            <div className="flex justify-end border-t border-border/60 px-4 py-3">
              <PaginationControls
                currentPage={safePage}
                totalPages={totalPages}
                pageHref={pageHref}
              />
            </div>
          ) : null}
        </section>
      )}
    </div>
  );
}

function PaginationControls({
  currentPage,
  totalPages,
  pageHref,
}: {
  currentPage: number;
  totalPages: number;
  pageHref: (page: number) => string;
}) {
  const prevPage = Math.max(1, currentPage - 1);
  const nextPage = Math.min(totalPages, currentPage + 1);
  const buttonClass =
    "inline-flex h-8 items-center gap-1 rounded-md border border-border/70 px-2.5 text-xs font-medium";
  const disabledClass =
    "inline-flex h-8 items-center gap-1 rounded-md border border-border/40 px-2.5 text-xs text-muted-foreground opacity-50";

  return (
    <div className="flex items-center gap-2">
      {currentPage <= 1 ? (
        <span className={disabledClass}>
          <ChevronLeft className="h-3.5 w-3.5" />
          上一页
        </span>
      ) : (
        <Link href={pageHref(prevPage)} className={`${buttonClass} hover:bg-muted/50`}>
          <ChevronLeft className="h-3.5 w-3.5" />
          上一页
        </Link>
      )}
      <span className="font-mono text-xs text-muted-foreground">
        {currentPage} / {totalPages}
      </span>
      {currentPage >= totalPages ? (
        <span className={disabledClass}>
          下一页
          <ChevronRight className="h-3.5 w-3.5" />
        </span>
      ) : (
        <Link href={pageHref(nextPage)} className={`${buttonClass} hover:bg-muted/50`}>
          下一页
          <ChevronRight className="h-3.5 w-3.5" />
        </Link>
      )}
    </div>
  );
}
