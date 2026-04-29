import Link from "next/link";
import { getDashboardStats } from "@/lib/queries";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { shortId, statusColor } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const stats = getDashboardStats();
  const maxBucket = Math.max(1, ...stats.qDistribution.map((b) => b.count));
  const maxDay = Math.max(1, ...stats.last7Days.map((d) => d.count));

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Pool health, review backlog, and credit assignment signal.
        </p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Stat label="Total experiences" value={stats.total} />
        <Stat label="Pending review" value={stats.pending} accent="warn" />
        <Stat label="Approved" value={stats.approved} accent="success" />
        <Stat label="Rejected" value={stats.rejected} accent="destructive" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Q-value distribution</CardTitle>
            <p className="text-xs text-muted-foreground mt-1">
              Mean of 5 dimensions, bucketed in 0.2 wide bins from -1.0 to 1.0.
            </p>
          </CardHeader>
          <CardContent>
            <Histogram buckets={stats.qDistribution} max={maxBucket} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Last 7 days</CardTitle>
            <p className="text-xs text-muted-foreground mt-1">
              New experiences ingested per day.
            </p>
          </CardHeader>
          <CardContent>
            <Sparkline points={stats.last7Days} max={maxDay} />
            <div className="text-2xl font-semibold mt-4">
              {stats.last7Days.reduce((s, d) => s + d.count, 0)}
              <span className="text-sm text-muted-foreground font-normal ml-2">
                in last 7 days
              </span>
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader>
            <CardTitle>By task type</CardTitle>
          </CardHeader>
          <CardContent>
            <BarList rows={stats.byTaskType.map((r) => ({ label: r.task_type, count: r.count }))} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>By sensitivity</CardTitle>
          </CardHeader>
          <CardContent>
            <BarList rows={stats.bySensitivity.map((r) => ({ label: r.sensitivity, count: r.count }))} />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Top reused experiences</CardTitle>
          <p className="text-xs text-muted-foreground mt-1">
            Highest reuse_count first. These shape behavior the most.
          </p>
        </CardHeader>
        <CardContent>
          {stats.topReused.length === 0 ? (
            <div className="text-sm text-muted-foreground">No experiences yet.</div>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="py-2 font-medium">id</th>
                  <th className="py-2 font-medium">intent</th>
                  <th className="py-2 font-medium">task</th>
                  <th className="py-2 font-medium">q</th>
                  <th className="py-2 font-medium">reuse</th>
                  <th className="py-2 font-medium">status</th>
                </tr>
              </thead>
              <tbody>
                {stats.topReused.map((e) => (
                  <tr key={e.experience_id} className="border-t">
                    <td className="py-2 font-mono text-xs">
                      <Link
                        href={`/experiences/${e.experience_id}`}
                        className="hover:underline"
                      >
                        {shortId(e.experience_id)}
                      </Link>
                    </td>
                    <td className="py-2 max-w-md truncate">{e.intent_text ?? "-"}</td>
                    <td className="py-2 text-muted-foreground">{e.task_type}</td>
                    <td className="py-2 tabular-nums">{e.q_scalar.toFixed(2)}</td>
                    <td className="py-2 tabular-nums">{e.reuse_count}</td>
                    <td className="py-2">
                      <Badge className={statusColor(e.review_status)}>{e.review_status}</Badge>
                    </td>
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

function Stat({
  label,
  value,
  accent,
}: {
  label: string;
  value: number;
  accent?: "warn" | "success" | "destructive";
}) {
  const accentRing =
    accent === "warn"
      ? "border-amber-300 dark:border-amber-800"
      : accent === "success"
      ? "border-green-300 dark:border-green-800"
      : accent === "destructive"
      ? "border-red-300 dark:border-red-800"
      : "border-border";
  return (
    <Card className={`${accentRing}`}>
      <CardContent className="pt-5">
        <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
        <div className="text-3xl font-semibold mt-1 tabular-nums">{value}</div>
      </CardContent>
    </Card>
  );
}

function Histogram({
  buckets,
  max,
}: {
  buckets: { bucket: number; count: number }[];
  max: number;
}) {
  const width = 640;
  const height = 180;
  const padX = 28;
  const padY = 24;
  const innerW = width - padX * 2;
  const innerH = height - padY * 2;
  const barW = innerW / buckets.length - 4;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="w-full h-auto"
      role="img"
      aria-label="Q-value distribution"
    >
      <line
        x1={padX}
        x2={width - padX}
        y1={height - padY}
        y2={height - padY}
        className="stroke-border"
        strokeWidth={1}
      />
      {buckets.map((b, i) => {
        const h = (b.count / max) * innerH;
        const x = padX + i * (innerW / buckets.length) + 2;
        const y = height - padY - h;
        return (
          <g key={b.bucket}>
            <rect
              x={x}
              y={y}
              width={barW}
              height={h}
              rx={3}
              className="fill-foreground/80"
            />
            <text
              x={x + barW / 2}
              y={height - padY + 14}
              className="fill-muted-foreground text-[9px]"
              textAnchor="middle"
            >
              {b.bucket.toFixed(1)}
            </text>
            {b.count > 0 ? (
              <text
                x={x + barW / 2}
                y={y - 4}
                className="fill-muted-foreground text-[9px]"
                textAnchor="middle"
              >
                {b.count}
              </text>
            ) : null}
          </g>
        );
      })}
    </svg>
  );
}

function Sparkline({
  points,
  max,
}: {
  points: { day: string; count: number }[];
  max: number;
}) {
  const width = 280;
  const height = 80;
  const padX = 8;
  const padY = 8;
  const innerW = width - padX * 2;
  const innerH = height - padY * 2;
  const stepX = innerW / Math.max(1, points.length - 1);

  const pts = points.map((p, i) => {
    const x = padX + i * stepX;
    const y = padY + (1 - p.count / max) * innerH;
    return `${x},${y}`;
  });

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-20" role="img">
      <polyline
        points={pts.join(" ")}
        fill="none"
        className="stroke-foreground"
        strokeWidth={2}
      />
      {points.map((p, i) => {
        const x = padX + i * stepX;
        const y = padY + (1 - p.count / max) * innerH;
        return <circle key={p.day} cx={x} cy={y} r={2.5} className="fill-foreground" />;
      })}
    </svg>
  );
}

function BarList({ rows }: { rows: { label: string; count: number }[] }) {
  if (rows.length === 0) {
    return <div className="text-sm text-muted-foreground">No data.</div>;
  }
  const max = Math.max(1, ...rows.map((r) => r.count));
  return (
    <ul className="space-y-2">
      {rows.map((r) => (
        <li key={r.label} className="flex items-center gap-3">
          <span className="text-sm w-32 shrink-0 truncate">{r.label}</span>
          <div className="flex-1 h-2 rounded bg-muted overflow-hidden">
            <div
              className="h-full bg-foreground/80"
              style={{ width: `${(r.count / max) * 100}%` }}
            />
          </div>
          <span className="text-xs tabular-nums text-muted-foreground w-8 text-right">
            {r.count}
          </span>
        </li>
      ))}
    </ul>
  );
}
