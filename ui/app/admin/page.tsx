import Link from "@/components/ui/link";
import { Database, Gauge, Shield, TrendingUp, Users, Lock, Cpu, Coins, Sparkles, Layers3 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { getMvpStats, listAgents } from "@/lib/mvp-queries";
import { getDashboardStats, getUsageStats, getCrystalStats, listCrystallizedSkills } from "@/lib/queries";
import { formatDate, shortId } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function AdminPage() {
  const mvp = getMvpStats();
  const dash = getDashboardStats();
  const agents = listAgents();
  const usage = getUsageStats();
  const crystal = getCrystalStats();
  const crystalSkills = listCrystallizedSkills(10);

  return (
    <div className="flex flex-col gap-6 pb-12">
      <section className="flex flex-col gap-3 rounded-2xl border border-border/60 bg-white/85 px-5 py-4">
        <div className="flex items-center gap-2 text-sm">
          <Gauge className="h-4 w-4 text-cyan-700" />
          <span className="font-semibold">池统计</span>
          <span className="text-muted-foreground">
            · 只读看板 · 公开数据 · 不需身份
          </span>
        </div>
      </section>

      {/* Top tile row */}
      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          icon={<Database className="h-4 w-4" />}
          label="经验总数"
          value={dash.total}
          hint={`Lite MVP: ${mvp.liteExperiences}`}
        />
        <StatTile
          icon={<Users className="h-4 w-4" />}
          label="agent / team"
          value={`${mvp.agents} / ${mvp.teams}`}
          hint={`${agents.length} 个已注册 agent`}
        />
        <StatTile
          icon={<Shield className="h-4 w-4" />}
          label="脱敏命中"
          value={mvp.redactions}
          hint="client-side rule-1 累计"
        />
        <StatTile
          icon={<Lock className="h-4 w-4" />}
          label="ACL 分布"
          value={`${mvp.privateRows} 私 / ${mvp.teamRows} 团 / ${mvp.publicRows} 公`}
          hint="按可见范围"
        />
      </section>

      {/* LLM usage (auto-label accounting) */}
      <section className="rounded-2xl border border-border/60 bg-white/85">
        <div className="flex items-center gap-2 border-b border-border/60 px-5 py-3 text-sm">
          <Cpu className="h-4 w-4 text-cyan-700" />
          <span className="font-semibold">自动标注用量</span>
          <span className="text-muted-foreground">
            · 每条新上传 experience 服务端自动调 LLM 提炼标题 + 5 维奖励
          </span>
        </div>
        <div className="grid gap-2 px-5 py-3 sm:grid-cols-4">
          <UsageTile
            icon={<Cpu className="h-3.5 w-3.5" />}
            label="总调用"
            value={usage.total_calls}
            sub={`✓ ${usage.ok} / ✗ ${usage.errors}`}
          />
          <UsageTile
            icon={<TrendingUp className="h-3.5 w-3.5" />}
            label="prompt token"
            value={usage.prompt_tokens.toLocaleString()}
          />
          <UsageTile
            icon={<TrendingUp className="h-3.5 w-3.5" />}
            label="completion token"
            value={usage.completion_tokens.toLocaleString()}
          />
          <UsageTile
            icon={<Coins className="h-3.5 w-3.5" />}
            label="估算成本"
            value={usage.total_cost_usd > 0 ? `$${usage.total_cost_usd.toFixed(4)}` : "—"}
            sub={usage.total_cost_usd === 0 ? "未配价格" : undefined}
          />
        </div>
        {usage.by_model.length > 0 ? (
          <div className="border-t border-border/60 px-5 py-2">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
              按模型
            </div>
            <ul className="mt-1 flex flex-wrap gap-2">
              {usage.by_model.map((m) => (
                <li
                  key={m.model}
                  className="flex items-center gap-2 rounded-md border border-border/60 bg-muted/40 px-2 py-1 text-xs"
                >
                  <span className="font-mono">{m.model}</span>
                  <span className="text-muted-foreground">·</span>
                  <span className="font-mono">{m.calls} 次</span>
                  <span className="text-muted-foreground">·</span>
                  <span className="font-mono">{m.tokens.toLocaleString()} tok</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {Object.keys(usage.by_kind).length > 0 ? (
          <div className="border-t border-border/60 px-5 py-2 text-xs text-muted-foreground">
            <span className="text-[10px] uppercase tracking-wider">用途分布:</span>{" "}
            {Object.entries(usage.by_kind).map(([k, v]) => (
              <span key={k} className="ml-2">
                <span className="font-mono text-foreground">{k}</span>{" "}
                {v.calls} 次 / {v.tokens.toLocaleString()} tok
              </span>
            ))}
          </div>
        ) : null}
      </section>

      {/* Skill crystallization (Ultron-borrowed) */}
      <section className="rounded-2xl border border-border/60 bg-white/85">
        <div className="flex items-center gap-2 border-b border-border/60 px-5 py-3 text-sm">
          <Sparkles className="h-4 w-4 text-amber-700" />
          <span className="font-semibold">技能结晶</span>
          <span className="text-muted-foreground">
            · 经验自动归簇,簇 ≥ N 时 LLM 综合多步 workflow skill,structure_score 严格上升才发新版
          </span>
        </div>
        <div className="grid gap-2 px-5 py-3 sm:grid-cols-4">
          <UsageTile icon={<Layers3 className="h-3.5 w-3.5" />} label="知识簇" value={crystal.clusters} sub={`平均 ${crystal.avg_members} 成员`} />
          <UsageTile icon={<Sparkles className="h-3.5 w-3.5" />} label="已结晶" value={crystal.crystallized} sub={`最大簇 ${crystal.max_members} 个`} />
          <UsageTile icon={<Database className="h-3.5 w-3.5" />} label="结晶技能" value={crystal.skills} />
          <UsageTile icon={<TrendingUp className="h-3.5 w-3.5" />} label="结晶率" value={crystal.clusters ? `${Math.round((crystal.crystallized / crystal.clusters) * 100)}%` : "—"} />
        </div>
        {crystalSkills.length > 0 ? (
          <div className="border-t border-border/60 px-5 py-2">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">已发布技能</div>
            <ul className="divide-y divide-border/60">
              {crystalSkills.map((s) => (
                <li key={s.skill_id} className="flex items-center gap-2 py-1.5 text-xs">
                  <Badge variant="outline" className="font-mono">v{s.version}</Badge>
                  <span className="font-mono">{s.name}</span>
                  <Badge className={s.structure_score >= 0.7 ? "bg-emerald-100 text-emerald-900" : "bg-cyan-100 text-cyan-900"}>
                    score {s.structure_score.toFixed(2)}
                  </Badge>
                  <span className="ml-auto text-muted-foreground">
                    {s.member_count} 个经验综合
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>

      {/* Status breakdown */}
      <section className="rounded-2xl border border-border/60 bg-white/85">
        <div className="border-b border-border/60 px-5 py-3 text-sm font-semibold">
          审核状态
        </div>
        <div className="grid gap-2 px-5 py-3 sm:grid-cols-4">
          <StatusPill label="待审" count={dash.pending} color="amber" />
          <StatusPill label="已通过" count={dash.approved} color="emerald" />
          <StatusPill label="已拒" count={dash.rejected} color="rose" />
          <StatusPill label="编辑过" count={dash.edited} color="cyan" />
        </div>
      </section>

      {/* Top tasks */}
      <section className="grid gap-3 lg:grid-cols-2">
        <div className="rounded-2xl border border-border/60 bg-white/85">
          <div className="border-b border-border/60 px-5 py-3 text-sm font-semibold">
            热门任务类型
          </div>
          <ul className="divide-y divide-border/60">
            {dash.byTaskType.slice(0, 12).map((t) => (
              <li key={t.task_type} className="flex items-center gap-3 px-5 py-2">
                <Badge variant="outline" className="font-mono">
                  {t.task_type}
                </Badge>
                <Bar pct={pct(t.count, dash.byTaskType[0]?.count ?? 1)} />
                <span className="ml-auto font-mono text-xs">{t.count}</span>
              </li>
            ))}
            {dash.byTaskType.length === 0 ? (
              <li className="px-5 py-4 text-xs text-muted-foreground">暂无</li>
            ) : null}
          </ul>
        </div>
        <div className="rounded-2xl border border-border/60 bg-white/85">
          <div className="border-b border-border/60 px-5 py-3 text-sm font-semibold">
            敏感度分布
          </div>
          <ul className="divide-y divide-border/60">
            {dash.bySensitivity.map((s) => (
              <li key={s.sensitivity} className="flex items-center gap-3 px-5 py-2">
                <Badge
                  className={
                    s.sensitivity === "high"
                      ? "bg-rose-100 text-rose-900"
                      : s.sensitivity === "medium"
                      ? "bg-amber-100 text-amber-900"
                      : "bg-emerald-100 text-emerald-900"
                  }
                >
                  {s.sensitivity}
                </Badge>
                <Bar pct={pct(s.count, dash.bySensitivity[0]?.count ?? 1)} />
                <span className="ml-auto font-mono text-xs">{s.count}</span>
              </li>
            ))}
            {dash.bySensitivity.length === 0 ? (
              <li className="px-5 py-4 text-xs text-muted-foreground">暂无</li>
            ) : null}
          </ul>
        </div>
      </section>

      {/* Last 7 days */}
      <section className="rounded-2xl border border-border/60 bg-white/85">
        <div className="flex items-center gap-2 border-b border-border/60 px-5 py-3 text-sm">
          <TrendingUp className="h-4 w-4 text-cyan-700" />
          <span className="font-semibold">近 7 日入库</span>
        </div>
        <div className="flex items-end gap-1 px-5 py-4">
          {dash.last7Days.map((d) => {
            const max = Math.max(1, ...dash.last7Days.map((x) => x.count));
            const h = Math.round((d.count / max) * 80);
            return (
              <div key={d.day} className="flex flex-1 flex-col items-center gap-1">
                <div className="text-[10px] font-mono text-muted-foreground">
                  {d.count}
                </div>
                <div
                  className="w-full rounded-t bg-cyan-600/70"
                  style={{ height: `${Math.max(2, h)}px` }}
                  title={`${d.day}: ${d.count}`}
                />
                <div className="text-[10px] font-mono text-muted-foreground">
                  {d.day.slice(5)}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* Top reused */}
      <section className="rounded-2xl border border-border/60 bg-white/85">
        <div className="border-b border-border/60 px-5 py-3 text-sm font-semibold">
          复用最多
        </div>
        <ul className="divide-y divide-border/60">
          {dash.topReused.slice(0, 12).map((e) => (
            <li key={e.experience_id} className="flex items-center gap-2 px-5 py-2 text-sm">
              <Link
                href={`/experiences/${e.experience_id}`}
                className="font-mono text-[11px] text-cyan-700 hover:underline"
              >
                {shortId(e.experience_id)}
              </Link>
              <Badge variant="outline" className="font-mono">{e.task_type}</Badge>
              <Link
                href={`/experiences/${e.experience_id}`}
                className="truncate hover:text-cyan-800 hover:underline"
              >
                {e.intent_text || "(no intent)"}
              </Link>
              <span className="ml-auto font-mono text-xs text-muted-foreground">
                {e.visit_count} 次
              </span>
            </li>
          ))}
          {dash.topReused.length === 0 ? (
            <li className="px-5 py-4 text-xs text-muted-foreground">暂无</li>
          ) : null}
        </ul>
      </section>
    </div>
  );
}

function StatTile({
  icon,
  label,
  value,
  hint,
}: {
  icon: React.ReactNode;
  label: string;
  value: number | string;
  hint: string;
}) {
  return (
    <div className="rounded-2xl border border-border/60 bg-white/85 px-4 py-3">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className="mt-1 font-mono text-2xl font-semibold">{value}</div>
      <div className="text-[11px] text-muted-foreground/80">{hint}</div>
    </div>
  );
}

function UsageTile({
  icon,
  label,
  value,
  sub,
}: {
  icon: React.ReactNode;
  label: string;
  value: number | string;
  sub?: string;
}) {
  return (
    <div className="rounded-lg border border-border/60 bg-muted/30 px-3 py-2">
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className="mt-0.5 font-mono text-xl font-semibold">{value}</div>
      {sub ? <div className="text-[10px] text-muted-foreground">{sub}</div> : null}
    </div>
  );
}

function StatusPill({
  label,
  count,
  color,
}: {
  label: string;
  count: number;
  color: "amber" | "emerald" | "rose" | "cyan";
}) {
  const colors = {
    amber: "border-amber-500/30 bg-amber-50 text-amber-900",
    emerald: "border-emerald-500/30 bg-emerald-50 text-emerald-900",
    rose: "border-rose-500/30 bg-rose-50 text-rose-900",
    cyan: "border-cyan-500/30 bg-cyan-50 text-cyan-900",
  };
  return (
    <div className={`rounded-lg border px-3 py-2 ${colors[color]}`}>
      <div className="text-xs uppercase tracking-wider opacity-80">{label}</div>
      <div className="mt-1 font-mono text-2xl font-semibold">{count}</div>
    </div>
  );
}

function Bar({ pct }: { pct: number }) {
  return (
    <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted/50">
      <div
        className="h-full bg-cyan-600/70"
        style={{ width: `${Math.max(2, Math.min(100, pct))}%` }}
      />
    </div>
  );
}

function pct(a: number, b: number): number {
  if (!b) return 0;
  return (a / b) * 100;
}
