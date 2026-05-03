import Link from "@/components/ui/link";
import { notFound } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  getSkill,
  listExperiencesUsingSkill,
  listSkillQUpdates,
} from "@/lib/skills-queries";
import { qScalar } from "@/lib/types";

function formatDelta(d: number | null | undefined): string {
  if (d === null || d === undefined) return "—";
  const sign = d >= 0 ? "+" : "";
  return `${sign}${d.toFixed(4)}`;
}

interface SkillDetailProps {
  params: Promise<{ id: string }>;
}

export default async function SkillDetailPage({ params }: SkillDetailProps) {
  const { id } = await params;
  const skill = getSkill(id);
  if (!skill) {
    notFound();
  }
  const uses = listExperiencesUsingSkill(id);
  const updates = listSkillQUpdates(id);
  const triggers = JSON.parse(skill.trigger_keywords || "[]") as string[];
  const dependencies = JSON.parse(skill.dependencies || "[]") as string[];
  const tags = JSON.parse(skill.tags || "[]") as string[];

  const q = qScalar(skill);

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold">
            {skill.name}{" "}
            <span className="text-base font-normal text-muted-foreground">
              v{skill.version}
            </span>
          </h1>
          <p className="text-sm text-muted-foreground">{skill.description}</p>
        </div>
        <nav className="flex gap-3 text-sm">
          <Link className="underline" href="/skills">全部技能</Link>
          <Link className="underline" href="/">工作台</Link>
        </nav>
      </header>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">元数据</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div>
            <div className="text-muted-foreground">q_scalar</div>
            <div className="font-mono text-base">{q.toFixed(3)}</div>
          </div>
          <div>
            <div className="text-muted-foreground">调用次数</div>
            <div className="font-mono text-base">{skill.invoke_count}</div>
          </div>
          <div>
            <div className="text-muted-foreground">安装次数</div>
            <div className="font-mono text-base">{skill.install_count}</div>
          </div>
          <div>
            <div className="text-muted-foreground">q_update_count</div>
            <div className="font-mono text-base">{skill.q_update_count}</div>
          </div>
          <div>
            <div className="text-muted-foreground">文件数</div>
            <div className="font-mono">{skill.file_count}</div>
          </div>
          <div>
            <div className="text-muted-foreground">bundle 大小</div>
            <div className="font-mono">{(skill.bundle_size_bytes / 1024).toFixed(1)} KB</div>
          </div>
          <div className="col-span-2 sm:col-span-2">
            <div className="text-muted-foreground">bundle sha256</div>
            <div className="break-all font-mono text-xs">{skill.bundle_sha256}</div>
          </div>
          <div>
            <div className="text-muted-foreground">创建时间</div>
            <div>{skill.created_at}</div>
          </div>
          <div>
            <div className="text-muted-foreground">acl / sensitivity</div>
            <div className="space-x-1.5">
              <Badge variant="outline">{skill.acl}</Badge>
              <Badge variant="outline">{skill.sensitivity}</Badge>
            </div>
          </div>
          <div>
            <div className="text-muted-foreground">审阅状态</div>
            <Badge variant="secondary">{skill.review_status}</Badge>
          </div>
          <div>
            <div className="text-muted-foreground">脱敏状态</div>
            <Badge
              variant={skill.sanitization_status === "human_review" ? "destructive" : "secondary"}
            >
              {skill.sanitization_status}
            </Badge>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Q 拆分</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-5 gap-3 text-sm font-mono">
          <div>outcome<br/>{skill.q_outcome.toFixed(3)}</div>
          <div>intent<br/>{skill.q_intent.toFixed(3)}</div>
          <div>execution<br/>{skill.q_execution.toFixed(3)}</div>
          <div>orchestration<br/>{skill.q_orchestration.toFixed(3)}</div>
          <div>expression<br/>{skill.q_expression.toFixed(3)}</div>
        </CardContent>
      </Card>

      {(triggers.length > 0 || dependencies.length > 0 || tags.length > 0) && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">标签</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {triggers.length > 0 && (
              <div>
                <span className="mr-2 text-muted-foreground">triggers:</span>
                {triggers.map((t) => (
                  <Badge key={t} variant="outline" className="mr-1">{t}</Badge>
                ))}
              </div>
            )}
            {dependencies.length > 0 && (
              <div>
                <span className="mr-2 text-muted-foreground">dependencies:</span>
                {dependencies.map((d) => (
                  <Badge key={d} variant="outline" className="mr-1">{d}</Badge>
                ))}
              </div>
            )}
            {tags.length > 0 && (
              <div>
                <span className="mr-2 text-muted-foreground">tags:</span>
                {tags.map((t) => (
                  <Badge key={t} variant="secondary" className="mr-1">{t}</Badge>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">SKILL.md (已脱敏)</CardTitle>
        </CardHeader>
        <CardContent>
          <pre className="max-h-96 overflow-auto rounded bg-muted p-3 text-xs">
            {skill.content_md ?? "(empty)"}
          </pre>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            使用该技能的经验 ({uses.length})
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          {uses.length === 0 ? (
            <p className="text-muted-foreground">暂无关联经验。</p>
          ) : (
            uses.map((u) => (
              <div key={u.experience_id} className="flex items-baseline justify-between gap-3">
                <Link
                  href={`/experiences/${u.experience_id}`}
                  className="font-mono text-xs underline"
                >
                  {u.experience_id.slice(0, 8)}
                </Link>
                <span className="flex-1 truncate text-muted-foreground">
                  {u.intent_text ?? "无意图"}
                </span>
                <Badge variant="outline">{u.task_type ?? "?"}</Badge>
                {u.credit_applied ? (
                  <Badge variant="secondary">已回流</Badge>
                ) : (
                  <Badge variant="outline">待回流</Badge>
                )}
              </div>
            ))
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            Q 更新历史 ({updates.length})
          </CardTitle>
        </CardHeader>
        <CardContent>
          {updates.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              暂无 Q 更新。技能在被经验声明并完成评分后才会产生回流。
            </p>
          ) : (
            <table className="w-full text-xs">
              <thead className="text-left text-muted-foreground">
                <tr>
                  <th className="pb-2">时间</th>
                  <th className="pb-2">子经验</th>
                  <th className="pb-2">α·c</th>
                  <th className="pb-2">Δ outcome</th>
                  <th className="pb-2">Δ intent</th>
                  <th className="pb-2">Δ exec</th>
                  <th className="pb-2">Δ orch</th>
                  <th className="pb-2">Δ expr</th>
                </tr>
              </thead>
              <tbody>
                {updates.map((u) => (
                  <tr key={u.update_id} className="border-t">
                    <td className="py-1.5 font-mono">{u.created_at}</td>
                    <td className="py-1.5 font-mono">
                      {u.triggered_by_experience ? (
                        <Link
                          href={`/experiences/${u.triggered_by_experience}`}
                          className="underline"
                        >
                          {u.triggered_by_experience.slice(0, 8)}
                        </Link>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="py-1.5 font-mono">{(u.alpha * u.confidence).toFixed(3)}</td>
                    <td className="py-1.5 font-mono">{formatDelta(u.delta_outcome)}</td>
                    <td className="py-1.5 font-mono">{formatDelta(u.delta_intent)}</td>
                    <td className="py-1.5 font-mono">{formatDelta(u.delta_execution)}</td>
                    <td className="py-1.5 font-mono">{formatDelta(u.delta_orchestration)}</td>
                    <td className="py-1.5 font-mono">{formatDelta(u.delta_expression)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
