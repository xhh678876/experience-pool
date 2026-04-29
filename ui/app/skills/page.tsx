import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { listSkills } from "@/lib/skills-queries";

interface SkillsPageProps {
  searchParams?: Promise<{
    review_status?: string;
    search?: string;
  }>;
}

export default async function SkillsPage({ searchParams }: SkillsPageProps) {
  const params = (await searchParams) ?? {};
  const items = listSkills({
    reviewStatus: params.review_status,
    search: params.search,
  });

  return (
    <main className="mx-auto max-w-6xl space-y-6 px-6 py-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Skills</h1>
          <p className="text-sm text-muted-foreground">
            Reusable bundles uploaded by agents. Q values rise as downstream
            experiences using the skill earn rewards.
          </p>
        </div>
        <nav className="flex gap-3 text-sm">
          <Link className="underline" href="/">Dashboard</Link>
          <Link className="underline" href="/experiences">Experiences</Link>
        </nav>
      </header>

      <form className="flex flex-wrap gap-3" method="get">
        <Input
          name="search"
          defaultValue={params.search ?? ""}
          placeholder="search by name, description, or skill_id"
          className="max-w-md"
        />
        <select
          name="review_status"
          defaultValue={params.review_status ?? "all"}
          className="rounded-md border border-input bg-background px-3 text-sm"
        >
          <option value="all">all statuses</option>
          <option value="auto_approved">auto_approved</option>
          <option value="approved">approved</option>
          <option value="pending">pending</option>
          <option value="rejected">rejected</option>
          <option value="edited">edited</option>
        </select>
        <button
          type="submit"
          className="rounded-md bg-primary px-4 text-sm text-primary-foreground"
        >
          Apply
        </button>
      </form>

      {items.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            No skills uploaded yet. Run{" "}
            <code className="rounded bg-muted px-1.5 py-0.5">
              expctl push-skill --bundle ./my-skill --agent &lt;name&gt;
            </code>
            .
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-3">
          {items.map((s) => (
            <Card key={s.skill_id}>
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center justify-between text-base">
                  <Link
                    href={`/skills/${s.skill_id}`}
                    className="hover:underline"
                  >
                    {s.name}
                    <span className="ml-2 text-xs text-muted-foreground">
                      v{s.version}
                    </span>
                  </Link>
                  <span className="text-xs font-normal text-muted-foreground">
                    q_scalar {s.q_scalar.toFixed(3)} · invoke {s.invoke_count} ·
                    install {s.install_count}
                  </span>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <p className="text-muted-foreground">{s.description}</p>
                <div className="flex flex-wrap gap-1.5">
                  <Badge variant="secondary">{s.review_status}</Badge>
                  <Badge variant="outline">{s.acl}</Badge>
                  <Badge variant="outline">{s.sensitivity}</Badge>
                  {s.sanitization_status === "human_review" && (
                    <Badge variant="destructive">sanitizer flagged</Badge>
                  )}
                  <span className="text-xs text-muted-foreground">
                    {s.file_count} file{s.file_count === 1 ? "" : "s"} ·{" "}
                    {(s.bundle_size_bytes / 1024).toFixed(1)} KB
                  </span>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </main>
  );
}
