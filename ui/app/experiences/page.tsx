import Link from "next/link";
import { distinctValues, listExperiences } from "@/lib/queries";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { sensitivityColor, shortId, statusColor } from "@/lib/utils";

export const dynamic = "force-dynamic";

type SearchParams = {
  status?: string;
  task?: string;
  sensitivity?: string;
  q?: string;
};

export default async function ExperiencesPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp = await searchParams;
  const items = listExperiences({
    reviewStatus: sp.status,
    taskType: sp.task,
    sensitivity: sp.sensitivity,
    search: sp.q,
    limit: 200,
  });

  const taskTypes = distinctValues("task_type");
  const sensitivities = distinctValues("sensitivity");
  const statuses = ["pending", "approved", "rejected", "edited"];

  return (
    <div className="grid grid-cols-12 gap-6">
      <aside className="col-span-12 lg:col-span-3 space-y-6">
        <div>
          <h2 className="text-sm font-medium uppercase tracking-wide text-muted-foreground mb-3">
            Search
          </h2>
          <form action="/experiences" method="get" className="space-y-2">
            <Input
              name="q"
              defaultValue={sp.q ?? ""}
              placeholder="Search intent or id"
            />
            {sp.status ? <input type="hidden" name="status" value={sp.status} /> : null}
            {sp.task ? <input type="hidden" name="task" value={sp.task} /> : null}
            {sp.sensitivity ? (
              <input type="hidden" name="sensitivity" value={sp.sensitivity} />
            ) : null}
            <Button type="submit" variant="outline" size="sm" className="w-full">
              Search
            </Button>
          </form>
        </div>

        <FilterGroup
          title="Review status"
          name="status"
          values={["all", ...statuses]}
          current={sp.status ?? "all"}
          sp={sp}
        />
        <FilterGroup
          title="Task type"
          name="task"
          values={["all", ...taskTypes]}
          current={sp.task ?? "all"}
          sp={sp}
        />
        <FilterGroup
          title="Sensitivity"
          name="sensitivity"
          values={["all", ...sensitivities]}
          current={sp.sensitivity ?? "all"}
          sp={sp}
        />
      </aside>

      <section className="col-span-12 lg:col-span-9">
        <div className="flex items-center justify-between mb-4">
          <h1 className="text-2xl font-semibold tracking-tight">Experiences</h1>
          <span className="text-sm text-muted-foreground">{items.length} shown</span>
        </div>

        <Card>
          <CardContent className="p-0 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-muted-foreground bg-muted/40">
                <tr>
                  <th className="px-4 py-3 font-medium">id</th>
                  <th className="px-4 py-3 font-medium">intent</th>
                  <th className="px-4 py-3 font-medium">task</th>
                  <th className="px-4 py-3 font-medium">model</th>
                  <th className="px-4 py-3 font-medium">q</th>
                  <th className="px-4 py-3 font-medium">reuse</th>
                  <th className="px-4 py-3 font-medium">status</th>
                  <th className="px-4 py-3 font-medium">sensitivity</th>
                </tr>
              </thead>
              <tbody>
                {items.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-4 py-8 text-center text-muted-foreground">
                      No experiences match the current filters.
                    </td>
                  </tr>
                ) : (
                  items.map((e) => (
                    <tr key={e.experience_id} className="border-t hover:bg-muted/30">
                      <td className="px-4 py-3 font-mono text-xs">
                        <Link
                          href={`/experiences/${e.experience_id}`}
                          className="hover:underline"
                        >
                          {shortId(e.experience_id)}
                        </Link>
                      </td>
                      <td className="px-4 py-3 max-w-md">
                        <Link
                          href={`/experiences/${e.experience_id}`}
                          className="hover:underline line-clamp-2"
                        >
                          {e.intent_text ?? "-"}
                        </Link>
                      </td>
                      <td className="px-4 py-3 text-muted-foreground">{e.task_type}</td>
                      <td className="px-4 py-3 text-muted-foreground text-xs">
                        {e.source_model}
                      </td>
                      <td className="px-4 py-3 tabular-nums">{e.q_scalar.toFixed(2)}</td>
                      <td className="px-4 py-3 tabular-nums">{e.reuse_count}</td>
                      <td className="px-4 py-3">
                        <Badge className={statusColor(e.review_status)}>{e.review_status}</Badge>
                      </td>
                      <td className="px-4 py-3">
                        <Badge className={sensitivityColor(e.sensitivity)}>{e.sensitivity}</Badge>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}

function FilterGroup({
  title,
  name,
  values,
  current,
  sp,
}: {
  title: string;
  name: keyof SearchParams;
  values: string[];
  current: string;
  sp: SearchParams;
}) {
  function buildHref(value: string): string {
    const params = new URLSearchParams();
    const next: SearchParams = { ...sp, [name]: value };
    for (const [k, v] of Object.entries(next)) {
      if (v && v !== "all") params.set(k, String(v));
    }
    const qs = params.toString();
    return `/experiences${qs ? `?${qs}` : ""}`;
  }

  return (
    <div>
      <h2 className="text-sm font-medium uppercase tracking-wide text-muted-foreground mb-3">
        {title}
      </h2>
      <ul className="space-y-1">
        {values.map((v) => (
          <li key={v}>
            <Link
              href={buildHref(v)}
              className={`block text-sm rounded-md px-2 py-1 ${
                current === v
                  ? "bg-foreground text-background"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground"
              }`}
            >
              {v}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
