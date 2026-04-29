import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { listSkillsUsedByExperience } from "@/lib/skills-queries";

interface SkillsTabProps {
  experienceId: string;
}

export function SkillsTab({ experienceId }: SkillsTabProps) {
  const uses = listSkillsUsedByExperience(experienceId);
  if (uses.length === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted-foreground">
          This experience didn&apos;t declare any skill usage. To link skills,
          push with{" "}
          <code className="rounded bg-muted px-1.5 py-0.5">--uses-skill foo</code>
          .
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          Skills used by this experience ({uses.length})
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
              {u.description ?? "(no description)"}
            </span>
            {u.credit_applied ? (
              <Badge variant="secondary">credit applied</Badge>
            ) : (
              <Badge variant="outline">credit pending</Badge>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
