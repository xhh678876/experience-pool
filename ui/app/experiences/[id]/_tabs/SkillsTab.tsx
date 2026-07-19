import Link from "@/components/ui/link";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { listSkillsUsedByExperience } from "@/lib/skills-queries";

interface SkillsTabProps {
  experienceId: string;
}

export async function SkillsTab({ experienceId }: SkillsTabProps) {
  const uses = await listSkillsUsedByExperience(experienceId);
  if (uses.length === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted-foreground">
          这条经验没有声明技能使用。后续可在上传时追加{" "}
          <code className="rounded bg-muted px-1.5 py-0.5">--uses-skill foo</code>
          进行关联。
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          本经验使用的技能 ({uses.length})
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {uses.map((u) => (
          <div
            key={u.skill_id}
            className="flex items-baseline justify-between gap-3"
          >
            <Link
              href={`/skills/${u.skill_id}`}
              className="font-medium hover:underline"
            >
              {u.name}
              <span className="ml-2 text-xs text-muted-foreground">
                v{u.version}
              </span>
            </Link>
            <span className="flex-1 truncate text-muted-foreground">
              {u.description ?? "无描述"}
            </span>
            {u.credit_applied ? (
              <Badge variant="secondary">已回流</Badge>
            ) : (
              <Badge variant="outline">待回流</Badge>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
