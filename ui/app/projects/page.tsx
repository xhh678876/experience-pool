import Link from "@/components/ui/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { FolderKanban, KeyRound, Plus, Users } from "lucide-react";
import { apiListProjects } from "@/lib/projects-api";
import { getCurrentUser } from "@/lib/auth";
import { formatDate } from "@/lib/utils";
import { acceptProjectInviteAction, createProjectAction } from "./actions";

export const dynamic = "force-dynamic";

export default async function ProjectsPage() {
  const me = await getCurrentUser();
  const projects = me ? await apiListProjects() : null;
  const rows = projects?.data?.projects ?? [];

  return (
    <div className="flex flex-col gap-6 pb-12">
      <section className="rounded-lg border border-border/60 bg-white/85 px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <FolderKanban className="h-4 w-4" />
              <span className="font-semibold text-foreground">项目池</span>
              <span>·</span>
              <span>把多个成员的个人经验库按项目授权聚合成 RAG 池</span>
            </div>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
              项目池不会把经验发布到公共池；经验仍归各自 owner，只在项目成员和授权 owner 范围内检索。
            </p>
          </div>
          {me ? (
            <Badge variant="outline" className="font-mono">
              {me.email}
            </Badge>
          ) : (
            <Link href="/login">
              <Button>登录</Button>
            </Link>
          )}
        </div>
      </section>

      {me ? (
        <section className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
          <div className="rounded-lg border border-border/60 bg-white/85 p-4">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
              <Plus className="h-4 w-4 text-cyan-700" />
              创建项目
            </div>
            <form action={createProjectAction} className="grid gap-3 sm:grid-cols-[1fr_180px_auto]">
              <Input name="name" placeholder="项目名称，例如 MOVA Eval" className="h-10" />
              <Input name="slug" placeholder="slug，可选" className="h-10" />
              <Button type="submit" className="h-10 px-4">
                创建
              </Button>
            </form>
          </div>

          <div className="rounded-lg border border-border/60 bg-white/85 p-4">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
              <KeyRound className="h-4 w-4 text-cyan-700" />
              接受邀请
            </div>
            <form action={acceptProjectInviteAction} className="flex gap-2">
              <Input name="token" placeholder="exproj_..." className="h-10" />
              <Button type="submit" variant="outline" className="h-10 px-4">
                加入
              </Button>
            </form>
          </div>
        </section>
      ) : null}

      <section className="rounded-lg border border-border/60 bg-white/85">
        <div className="flex items-center gap-2 border-b border-border/60 px-5 py-3 text-sm">
          <Users className="h-4 w-4 text-cyan-700" />
          <span className="font-semibold">我的项目</span>
          <span className="text-muted-foreground">· {rows.length} 个</span>
        </div>
        {!me ? (
          <div className="px-5 py-8 text-sm text-muted-foreground">登录后查看和创建项目池。</div>
        ) : rows.length === 0 ? (
          <div className="px-5 py-8 text-sm text-muted-foreground">暂无项目。创建项目后会自动授权你的个人池给该项目。</div>
        ) : (
          <ul className="divide-y divide-border/60">
            {rows.map((project) => (
              <li key={project.project_id} className="flex flex-wrap items-center gap-3 px-5 py-3">
                <div className="min-w-0 flex-1">
                  <Link href={`/projects/${project.slug}`} className="font-medium hover:text-cyan-800 hover:underline">
                    {project.name}
                  </Link>
                  <div className="mt-1 flex flex-wrap gap-2 text-xs text-muted-foreground">
                    <span className="font-mono">{project.slug}</span>
                    <span>创建者 {project.created_by_owner}</span>
                    <span>{formatDate(project.created_at)}</span>
                  </div>
                </div>
                <Badge variant="outline">{project.role ?? project.relation ?? "member"}</Badge>
                <Badge className="bg-cyan-50 text-cyan-800">{project.shared_owners ?? 0} owners</Badge>
                <Badge className="bg-muted text-muted-foreground">{project.member_count ?? 0} members</Badge>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
