import Link from "@/components/ui/link";
import { ShieldCheck, AlertTriangle, FileText, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { listMyExperiences } from "@/lib/me-queries";
import { getReviewerName } from "@/lib/auth";
import { formatDate, shortId } from "@/lib/utils";
import RevokeButton from "./RevokeButton";

export const dynamic = "force-dynamic";

interface PageProps {
  searchParams: Promise<{ include_revoked?: string }>;
}

export default async function MePage({ searchParams }: PageProps) {
  const params = await searchParams;
  const includeRevoked = params.include_revoked === "1";
  const viewer = await getReviewerName();
  const rows = await listMyExperiences(viewer, {
    includeRevoked,
    limit: 200,
  });

  const live = rows.filter((r) => !r.revoked).length;
  const revoked = rows.filter((r) => r.revoked).length;

  return (
    <div className="flex flex-col gap-6 pb-12">
      <section className="flex flex-col gap-2 rounded-2xl border border-border/60 bg-white/85 px-5 py-4">
        <div className="flex items-center gap-2 text-sm">
          <ShieldCheck className="h-4 w-4 text-cyan-700" />
          <span className="font-semibold">我的经验</span>
          <span className="text-muted-foreground">
            · 由 {viewer} 上传 · 一键 revoke 真删轨迹文件
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          <Badge className="bg-cyan-100 text-cyan-900 font-mono">{live} live</Badge>
          {revoked > 0 ? (
            <Badge className="bg-rose-100 text-rose-900 font-mono">
              {revoked} revoked
            </Badge>
          ) : null}
          <Link
            href={includeRevoked ? "/me" : "/me?include_revoked=1"}
            className="text-cyan-700 hover:underline"
          >
            {includeRevoked ? "hide revoked" : "show revoked"}
          </Link>
        </div>
        <div className="mt-1 flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-50/70 px-3 py-2 text-xs leading-5 text-amber-900">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-none" />
          <span>
            <strong>不可撤销。</strong>
            点击 revoke 会硬删 trajectory 文件并清空 vector / cluster / rewards 关联。
            DB 行保留 <code className="font-mono">revoked=1</code> 标记 + 永久 audit_log
            记录，但具体内容无法恢复。
          </span>
        </div>
      </section>

      {rows.length === 0 ? (
        <section className="rounded-2xl border border-dashed border-border/60 bg-muted/30 px-6 py-10 text-center">
          <FileText className="mx-auto h-8 w-8 text-muted-foreground" />
          <p className="mt-3 text-sm text-muted-foreground">
            {viewer} 还没上传过经验
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            如果你刚 revoke 过，请勾选 "show revoked" 查看历史
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
                    {r.is_memory_eligible ? (
                      <Badge className="bg-emerald-100 text-emerald-900 font-mono text-[10px]">
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
                    <Badge variant="outline" className="font-mono text-[10px]">
                      {r.acl}
                    </Badge>
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
                      <RevokeButton experienceId={r.experience_id} />
                    ) : null}
                  </div>
                  {!isRevoked && r.redactions_summary ? (
                    <div className="mt-1 text-[11px] text-muted-foreground">
                      sanitize hits:{" "}
                      <code className="font-mono">{r.redactions_summary}</code>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}
