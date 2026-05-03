import Link from "@/components/ui/link";
import {
  ShieldCheck,
  AlertTriangle,
  FileText,
  Trash2,
  Globe2,
  Lock,
  Sparkles,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { listMyExperiences, getOwnerQuota } from "@/lib/me-queries";
import { getReviewerName } from "@/lib/auth";
import { formatDate, shortId } from "@/lib/utils";
import RevokeButton from "./RevokeButton";
import PublishButton from "./PublishButton";

export const dynamic = "force-dynamic";

interface PageProps {
  searchParams: Promise<{ include_revoked?: string }>;
}

export default async function MePage({ searchParams }: PageProps) {
  const params = await searchParams;
  const includeRevoked = params.include_revoked === "1";
  const viewer = await getReviewerName();
  const [rows, quota] = await Promise.all([
    listMyExperiences(viewer, { includeRevoked, limit: 200 }),
    getOwnerQuota(viewer),
  ]);

  const live = rows.filter((r) => !r.revoked).length;
  const revoked = rows.filter((r) => r.revoked).length;
  const published = rows.filter((r) => r.publish_status === "published").length;
  const progressPct = Math.min(
    100,
    (quota.publish_count / quota.threshold) * 100
  );

  return (
    <div className="flex flex-col gap-6 pb-12">
      <section className="flex flex-col gap-3 rounded-2xl border border-border/60 bg-white/85 px-5 py-4">
        <div className="flex items-center gap-2 text-sm">
          <ShieldCheck className="h-4 w-4 text-cyan-700" />
          <span className="font-semibold">我的经验池</span>
          <span className="text-muted-foreground">
            · owner: <code className="font-mono">{quota.owner}</code>
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          <Badge className="bg-cyan-100 text-cyan-900 font-mono">{live} 私有</Badge>
          <Badge className="bg-emerald-100 text-emerald-900 font-mono">
            <Globe2 className="mr-0.5 inline h-3 w-3" />
            {published} 已发布
          </Badge>
          {revoked > 0 ? (
            <Badge className="bg-rose-100 text-rose-900 font-mono">
              {revoked} 已撤回
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
          <div className="border-b border-border/60 px-4 py-3 text-sm">
            <span className="font-semibold">{rows.length}</span>
            <span className="text-muted-foreground">
              {" "}条 · 按上传时间倒序
            </span>
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
        </section>
      )}
    </div>
  );
}
