import Link from "@/components/ui/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Database, Search, ShieldCheck, Users } from "lucide-react";
import { apiGetProject, apiRagContext } from "@/lib/projects-api";
import { formatDate, shortId } from "@/lib/utils";
import { grantMyPoolAction, revokeProjectGrantAction } from "../actions";
import InviteForm from "./InviteForm";

export const dynamic = "force-dynamic";

type SearchParams = {
  q?: string;
  topK?: string;
};

export default async function ProjectDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { slug } = await params;
  const sp = await searchParams;
  const projectRes = await apiGetProject(slug);
  if (!projectRes.ok || !projectRes.data) {
    return (
      <div className="rounded-lg border border-border/60 bg-white/85 p-6 text-sm text-muted-foreground">
        项目不存在或你没有权限访问。<Link href="/projects" className="text-cyan-700 hover:underline">返回项目池</Link>
      </div>
    );
  }
  const project = projectRes.data;
  const q = (sp.q ?? "").trim();
  const topK = Math.max(1, Math.min(30, Number(sp.topK ?? "8")));
  const rag = q
    ? await apiRagContext({
        q,
        top_k: topK,
        scope: `project:${project.slug}`,
      })
    : null;

  return (
    <div className="flex flex-col gap-6 pb-12">
      <section className="rounded-lg border border-border/60 bg-white/85 px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Database className="h-4 w-4" />
              <span className="font-semibold text-foreground">{project.name}</span>
              <span>·</span>
              <span className="font-mono">{project.slug}</span>
            </div>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
              项目 RAG 池从已授权 owner 的个人库生成上下文，默认不包含 high 敏感度经验。
            </p>
          </div>
          <Link href="/projects">
            <Button variant="ghost" className="h-9 px-3 text-xs">返回</Button>
          </Link>
        </div>
      </section>

      <section className="rounded-lg border border-border/60 bg-white/85 p-4">
        <form className="flex flex-col gap-3 lg:flex-row lg:items-end">
          <div className="min-w-0 flex-1">
            <label className="mb-1 block text-xs text-muted-foreground">项目 RAG 检索</label>
            <Input
              name="q"
              defaultValue={q}
              placeholder="例如：MOVA 评测、FastAPI HMAC、自动上传进度"
              className="h-10"
            />
          </div>
          <div className="w-28">
            <label className="mb-1 block text-xs text-muted-foreground">Top K</label>
            <Input name="topK" defaultValue={String(topK)} className="h-10" />
          </div>
          <Button type="submit" className="h-10 px-4">
            <Search className="mr-1 h-3.5 w-3.5" />
            检索
          </Button>
        </form>
      </section>

      {q ? (
        <section className="rounded-lg border border-border/60 bg-white/85">
          <div className="flex items-center gap-2 border-b border-border/60 px-5 py-3 text-sm">
            <Search className="h-4 w-4 text-cyan-700" />
            <span className="font-semibold">RAG context</span>
            <span className="text-muted-foreground">· {rag?.data?.chunks.length ?? 0} chunks</span>
          </div>
          {!rag?.ok || !rag.data ? (
            <div className="px-5 py-8 text-sm text-red-700">{rag?.message ?? "检索失败"}</div>
          ) : rag.data.chunks.length === 0 ? (
            <div className="px-5 py-8 text-sm text-muted-foreground">项目池里还没有可用命中。</div>
          ) : (
            <div className="grid gap-4 p-5 lg:grid-cols-[1fr_1.1fr]">
              <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap rounded-md border border-border/60 bg-muted/30 p-3 text-xs leading-5">
                {rag.data.context}
              </pre>
              <ul className="divide-y divide-border/60 rounded-md border border-border/60">
                {rag.data.chunks.map((chunk, index) => (
                  <li key={chunk.chunk_id} className="p-3">
                    <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                      <Badge className="bg-cyan-50 text-cyan-800">#{index + 1}</Badge>
                      <Badge variant="outline">{chunk.chunk_type}</Badge>
                      <Badge variant="outline">{Math.round(chunk.similarity * 100)}%</Badge>
                      <Badge variant="outline">{chunk.owner}</Badge>
                      <Link
                        href={`/experiences/${chunk.experience_id}`}
                        className="ml-auto font-mono text-[11px] text-cyan-700 hover:underline"
                      >
                        {shortId(chunk.experience_id)}
                      </Link>
                    </div>
                    <p className="text-sm leading-6 text-muted-foreground">{chunk.text}</p>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      ) : null}

      <section className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-lg border border-border/60 bg-white/85">
          <div className="flex items-center gap-2 border-b border-border/60 px-4 py-3 text-sm font-semibold">
            <ShieldCheck className="h-4 w-4 text-cyan-700" />
            授权的个人库
          </div>
          <div className="p-4">
            <form action={grantMyPoolAction} className="mb-4 flex flex-wrap items-center gap-3">
              <input type="hidden" name="project" value={project.slug} />
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                <input name="include_high" type="checkbox" className="h-4 w-4 rounded border-border" />
                包含 high 敏感度经验
              </label>
              <Button type="submit" variant="outline" className="h-9 px-3 text-xs">
                授权我的个人池
              </Button>
            </form>
            {project.grants.length === 0 ? (
              <p className="text-sm text-muted-foreground">还没有 owner 授权个人池。</p>
            ) : (
              <ul className="space-y-2">
                {project.grants.map((grant) => (
                  <li key={grant.owner} className="flex flex-wrap items-center gap-2 text-sm">
                    <Badge variant="outline" className="font-mono">{grant.owner}</Badge>
                    <span className="text-xs text-muted-foreground">{formatDate(grant.created_at)}</span>
                    {grant.include_high_sensitivity ? (
                      <Badge className="bg-amber-100 text-amber-900">includes high</Badge>
                    ) : null}
                    <form action={revokeProjectGrantAction} className="ml-auto">
                      <input type="hidden" name="project" value={project.slug} />
                      <input type="hidden" name="owner" value={grant.owner} />
                      <Button type="submit" variant="ghost" className="h-8 px-2 text-xs">撤回</Button>
                    </form>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <div className="rounded-lg border border-border/60 bg-white/85">
          <div className="flex items-center gap-2 border-b border-border/60 px-4 py-3 text-sm font-semibold">
            <Users className="h-4 w-4 text-cyan-700" />
            成员与邀请
          </div>
          <div className="space-y-4 p-4">
            <ul className="space-y-2">
              {project.members.map((member) => (
                <li key={member.user_id} className="flex flex-wrap items-center gap-2 text-sm">
                  <span>{member.display_name ?? member.email}</span>
                  <Badge variant="outline">{member.role}</Badge>
                  <span className="text-xs text-muted-foreground">{member.email}</span>
                </li>
              ))}
            </ul>
            <InviteForm project={project.slug} />
          </div>
        </div>
      </section>
    </div>
  );
}
