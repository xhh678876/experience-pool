import Link from "@/components/ui/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { EdgeRow, ExperienceListItem } from "@/lib/types";
import { shortId, statusColor } from "@/lib/utils";

type Node = ExperienceListItem;

export function LineageTab({
  experience,
  parents,
  children,
  nodes,
}: {
  experience: ExperienceListItem;
  parents: EdgeRow[];
  children: EdgeRow[];
  nodes: Node[];
}) {
  const nodeMap = new Map(nodes.map((n) => [n.experience_id, n]));
  const parentNodes = parents
    .map((e) => ({ edge: e, node: nodeMap.get(e.parent_id) }))
    .filter((x): x is { edge: EdgeRow; node: Node } => Boolean(x.node));
  const childNodes = children
    .map((e) => ({ edge: e, node: nodeMap.get(e.child_id) }))
    .filter((x): x is { edge: EdgeRow; node: Node } => Boolean(x.node));

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>血缘图</CardTitle>
          <p className="text-xs text-muted-foreground mt-1">
            左侧父节点向当前节点贡献信用，右侧子节点从当前节点继承信用。
          </p>
        </CardHeader>
        <CardContent>
          {parentNodes.length === 0 && childNodes.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              这条经验暂无声明父节点，也没有派生子节点。
            </p>
          ) : (
            <LineageSvg
              currentId={experience.experience_id}
              currentLabel={shortId(experience.experience_id)}
              currentQ={experience.q_scalar}
              parents={parentNodes}
              children={childNodes}
            />
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <NodeList title="父节点" rows={parentNodes.map((x) => ({ ...x.node, edge: x.edge, dir: "in" as const }))} />
        <NodeList title="子节点" rows={childNodes.map((x) => ({ ...x.node, edge: x.edge, dir: "out" as const }))} />
      </div>
    </div>
  );
}

function NodeList({
  title,
  rows,
}: {
  title: string;
  rows: (ExperienceListItem & { edge: EdgeRow; dir: "in" | "out" })[];
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">无。</p>
        ) : (
          rows.map((r) => (
            <Link
              key={r.experience_id}
              href={`/experiences/${r.experience_id}`}
              className="block rounded-md border p-3 hover:bg-muted"
            >
              <div className="flex items-center gap-2 mb-1">
                <span className="font-mono text-xs">{shortId(r.experience_id)}</span>
                <Badge>q: {r.q_scalar.toFixed(2)}</Badge>
                <Badge className={statusColor(r.review_status)}>{r.review_status}</Badge>
                {r.dir === "out" ? (
                  r.edge.credit_applied ? (
                    <Badge className="border-green-300 bg-green-50 text-green-900 dark:bg-green-950 dark:text-green-200 dark:border-green-800">
                      已回流
                    </Badge>
                  ) : (
                    <Badge className="border-amber-300 bg-amber-50 text-amber-900 dark:bg-amber-950 dark:text-amber-200 dark:border-amber-800">
                      待回流
                    </Badge>
                  )
                ) : null}
              </div>
              <div className="text-sm line-clamp-2">{r.intent_text ?? "-"}</div>
            </Link>
          ))
        )}
      </CardContent>
    </Card>
  );
}

function LineageSvg({
  currentId,
  currentLabel,
  currentQ,
  parents,
  children,
}: {
  currentId: string;
  currentLabel: string;
  currentQ: number;
  parents: { edge: EdgeRow; node: ExperienceListItem }[];
  children: { edge: EdgeRow; node: ExperienceListItem }[];
}) {
  const colW = 260;
  const nodeH = 64;
  const gapY = 14;
  const padY = 24;

  const leftCount = Math.max(parents.length, 1);
  const rightCount = Math.max(children.length, 1);
  const totalRows = Math.max(leftCount, rightCount, 1);
  const height = padY * 2 + totalRows * (nodeH + gapY) - gapY;
  const centerY = height / 2;

  function nodeY(i: number, count: number): number {
    const blockH = count * (nodeH + gapY) - gapY;
    const startY = (height - blockH) / 2;
    return startY + i * (nodeH + gapY);
  }

  const cx = colW + 80;
  const width = colW * 2 + 200;

  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        height={height}
        className="max-w-full h-auto"
      >
        {/* Edges from parents to current */}
        {parents.map((p, i) => {
          const y = nodeY(i, parents.length) + nodeH / 2;
          return (
            <line
              key={`p-${p.node.experience_id}`}
              x1={colW}
              y1={y}
              x2={cx - 40}
              y2={centerY}
              className="stroke-foreground/40"
              strokeWidth={1.5}
            />
          );
        })}
        {/* Edges from current to children */}
        {children.map((c, i) => {
          const y = nodeY(i, children.length) + nodeH / 2;
          return (
            <line
              key={`c-${c.node.experience_id}`}
              x1={cx + 40}
              y1={centerY}
              x2={cx + 40 + colW}
              y2={y}
              className={c.edge.credit_applied ? "stroke-green-500" : "stroke-amber-500"}
              strokeWidth={1.5}
              strokeDasharray={c.edge.credit_applied ? undefined : "4 3"}
            />
          );
        })}

        {/* Parent nodes */}
        {parents.map((p, i) => (
          <NodeBox
            key={`pn-${p.node.experience_id}`}
            x={0}
            y={nodeY(i, parents.length)}
            w={colW}
            h={nodeH}
            label={shortId(p.node.experience_id)}
            q={p.node.q_scalar}
            text={p.node.intent_text ?? ""}
            href={`/experiences/${p.node.experience_id}`}
          />
        ))}

        {/* Current node */}
        <NodeBox
          x={cx - 40}
          y={centerY - nodeH / 2}
          w={80}
          h={nodeH}
          label={currentLabel}
          q={currentQ}
          text=""
          href={`/experiences/${currentId}`}
          accent
        />

        {/* Child nodes */}
        {children.map((c, i) => (
          <g key={`cn-${c.node.experience_id}`}>
            <NodeBox
              x={cx + 40 + colW}
              y={nodeY(i, children.length)}
              w={colW}
              h={nodeH}
              label={shortId(c.node.experience_id)}
              q={c.node.q_scalar}
              text={c.node.intent_text ?? ""}
              href={`/experiences/${c.node.experience_id}`}
            />
            <text
              x={cx + 40 + colW - 8}
              y={nodeY(i, children.length) + 14}
              textAnchor="end"
              className={
                c.edge.credit_applied
                  ? "fill-green-600 text-[10px]"
                  : "fill-amber-600 text-[10px]"
              }
            >
              {c.edge.credit_applied ? "已回流" : "待回流"}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}

function NodeBox({
  x,
  y,
  w,
  h,
  label,
  q,
  text,
  href,
  accent,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  label: string;
  q: number;
  text: string;
  href: string;
  accent?: boolean;
}) {
  return (
    <a href={href}>
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        rx={8}
        className={accent ? "fill-foreground" : "fill-card stroke-border"}
        strokeWidth={1}
      />
      <text
        x={x + 10}
        y={y + 18}
        className={`text-[11px] ${accent ? "fill-background" : "fill-foreground"} font-mono`}
      >
        {label}
      </text>
      <text
        x={x + w - 10}
        y={y + 18}
        textAnchor="end"
        className={`text-[11px] ${accent ? "fill-background" : "fill-foreground"} tabular-nums`}
      >
        q {q.toFixed(2)}
      </text>
      {text && !accent ? (
        <foreignObject x={x + 10} y={y + 24} width={w - 20} height={h - 28}>
          <div
            style={{
              fontSize: 11,
              lineHeight: "1.3",
              color: "hsl(var(--muted-foreground))",
              overflow: "hidden",
              display: "-webkit-box",
              WebkitLineClamp: 2,
              WebkitBoxOrient: "vertical" as const,
            }}
          >
            {text}
          </div>
        </foreignObject>
      ) : null}
    </a>
  );
}
