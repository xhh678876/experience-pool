import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ExperienceListItem, QUpdateRow, RewardRow } from "@/lib/types";
import { formatDate, tryParseJson } from "@/lib/utils";

type ScriptStep = {
  step?: number | string;
  what?: string;
  why?: string;
  how?: string;
  [k: string]: unknown;
};

type RawStep = string | ScriptStep;

function isLiteIngest(experience: ExperienceListItem): boolean {
  return (experience.ingest_path ?? "full") === "lite";
}

function normalizeSteps(raw: RawStep[]): ScriptStep[] {
  return raw.map((s, i) => {
    if (typeof s === "string") {
      return { step: i + 1, what: s };
    }
    return s;
  });
}

export function CardTab({
  experience,
  reward,
  updates,
  toolUsage,
}: {
  experience: ExperienceListItem;
  reward: RewardRow | null;
  updates: QUpdateRow[];
  toolUsage?: Record<string, number>;
}) {
  const rawSteps = tryParseJson<RawStep[]>(experience.script_steps) ?? [];
  const steps = normalizeSteps(rawSteps);
  const tools = tryParseJson<string[]>(experience.tool_capabilities) ?? [];
  const decisions = tryParseJson<string[] | KeyDecision[]>(experience.key_decisions) ?? [];
  const pitfalls = tryParseJson<string[]>(experience.pitfalls) ?? [];
  const preconditions = tryParseJson<string[]>(experience.preconditions) ?? [];
  const lite = isLiteIngest(experience);

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div className="lg:col-span-2 space-y-6">
        {experience.query ? (
          <Card>
            <CardHeader>
              <CardTitle>用户原始 query</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm leading-relaxed whitespace-pre-wrap">
                {experience.query}
              </p>
            </CardContent>
          </Card>
        ) : null}

        <Card>
          <CardHeader>
            <CardTitle>意图</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm leading-relaxed">
              {experience.intent_text ?? (
                <span className="text-muted-foreground">未提取意图。</span>
              )}
            </p>
            {experience.summary && experience.summary !== experience.intent_text ? (
              <p className="text-sm text-muted-foreground mt-3 leading-relaxed">
                {experience.summary}
              </p>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>关键步骤</CardTitle>
          </CardHeader>
          <CardContent>
            {steps.length === 0 ? (
              <p className="text-sm text-muted-foreground">未提取步骤。</p>
            ) : (
              <ol className="space-y-4">
                {steps.map((s, i) => {
                  const stepNo = s.step ?? i + 1;
                  const body = s.what ?? "";
                  return (
                    <li key={i} className="border-l-2 border-foreground/30 pl-4">
                      <div className="text-xs text-muted-foreground">步骤 {stepNo}</div>
                      <div className="text-sm font-medium mt-1 whitespace-pre-wrap">
                        {body || <span className="text-muted-foreground italic">(空)</span>}
                      </div>
                      {s.why ? (
                        <div className="text-sm text-muted-foreground mt-1">
                          <span className="font-medium text-foreground/80">why: </span>
                          {s.why}
                        </div>
                      ) : null}
                      {s.how ? (
                        <div className="text-sm text-muted-foreground mt-1">
                          <span className="font-medium text-foreground/80">how: </span>
                          {s.how}
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ol>
            )}
          </CardContent>
        </Card>

        {experience.outcome ? (
          <Card>
            <CardHeader>
              <CardTitle>结果 / outcome</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm leading-relaxed whitespace-pre-wrap">
                {experience.outcome}
              </p>
            </CardContent>
          </Card>
        ) : null}

        <ToolUsageCard usage={toolUsage} />


        {lite ? (
          <div className="rounded-md border border-amber-300/50 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            这条是 <span className="font-mono">lite</span> 上传，仅含 query / intent / steps / outcome 四字段。
            前置条件、工具能力、关键决策、风险点这些字段需要走完整 extractor pipeline 才会有，
            目前未启用。可以点上方"轨迹"tab 看原始多轮内容（如果上传时附带了 trajectory）。
          </div>
        ) : (
          <>
            <Card>
              <CardHeader>
                <CardTitle>前置条件</CardTitle>
              </CardHeader>
              <CardContent>
                {preconditions.length === 0 ? (
                  <p className="text-sm text-muted-foreground">无。</p>
                ) : (
                  <ul className="text-sm list-disc pl-5 space-y-1">
                    {preconditions.map((p, i) => (
                      <li key={i}>{String(p)}</li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>工具能力</CardTitle>
              </CardHeader>
              <CardContent>
                {tools.length === 0 ? (
                  <p className="text-sm text-muted-foreground">无。</p>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {tools.map((t, i) => (
                      <span
                        key={i}
                        className="text-xs font-mono px-2 py-1 rounded-md bg-muted border"
                      >
                        {String(t)}
                      </span>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>关键决策</CardTitle>
              </CardHeader>
              <CardContent>
                {decisions.length === 0 ? (
                  <p className="text-sm text-muted-foreground">无。</p>
                ) : (
                  <ul className="text-sm list-disc pl-5 space-y-1">
                    {decisions.map((d, i) => {
                      if (typeof d === "string") return <li key={i}>{d}</li>;
                      return (
                        <li key={i}>
                          <span className="font-medium">{d.decision ?? ""}</span>
                          {d.rationale ? (
                            <span className="text-muted-foreground"> — {d.rationale}</span>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>风险点</CardTitle>
              </CardHeader>
              <CardContent>
                {pitfalls.length === 0 ? (
                  <p className="text-sm text-muted-foreground">无。</p>
                ) : (
                  <ul className="text-sm list-disc pl-5 space-y-1">
                    {pitfalls.map((p, i) => (
                      <li key={i}>{String(p)}</li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          </>
        )}
      </div>

      <div className="space-y-6">
        <RewardCard reward={reward} />
        <QStateCard experience={experience} />
        <QHistoryCard updates={updates} />
      </div>
    </div>
  );
}

function ToolUsageCard({ usage }: { usage?: Record<string, number> }) {
  const entries = Object.entries(usage ?? {}).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return null;
  const total = entries.reduce((s, [, n]) => s + n, 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          调用工具
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            共 {total} 次 · {entries.length} 种
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap gap-2">
          {entries.map(([name, n]) => (
            <span
              key={name}
              className="inline-flex items-center gap-1 rounded-md border bg-muted/40 px-2 py-1 text-xs font-mono"
            >
              {name}
              <span className="text-muted-foreground">×{n}</span>
            </span>
          ))}
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          仅展示工具名与次数。完整入参 / 返回请到上方"对话"tab 查看。
        </p>
      </CardContent>
    </Card>
  );
}

type KeyDecision = { decision?: string; rationale?: string };

function RewardCard({ reward }: { reward: RewardRow | null }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>最新评分</CardTitle>
      </CardHeader>
      <CardContent>
        {!reward ? (
          <p className="text-sm text-muted-foreground">暂无 judge 评分。</p>
        ) : (
          <>
            <RewardRow5
              label="outcome"
              value={reward.r_outcome}
            />
            <RewardRow5 label="intent" value={reward.r_intent} />
            <RewardRow5 label="execution" value={reward.r_execution} />
            <RewardRow5 label="orchestration" value={reward.r_orchestration} />
            <RewardRow5 label="expression" value={reward.r_expression} />
            <div className="mt-3 pt-3 border-t text-xs text-muted-foreground space-y-1">
              <div>
                judge: <span className="font-mono">{reward.judge_model}</span>{" "}
                <span className="font-mono">{reward.judge_version}</span>
              </div>
              <div>confidence: {reward.confidence.toFixed(2)}</div>
              {reward.is_unstable ? (
                <div className="text-amber-700 dark:text-amber-300">unstable</div>
              ) : null}
              <div>at: {formatDate(reward.created_at)}</div>
            </div>
            {reward.rationale ? (
              <div className="mt-3 pt-3 border-t">
                <div className="mb-1 text-xs text-muted-foreground">
                  理由
                </div>
                <p className="text-sm whitespace-pre-wrap leading-relaxed">{reward.rationale}</p>
              </div>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function RewardRow5({ label, value }: { label: string; value: number }) {
  // Map [-1, 1] to [0%, 100%] for the bar.
  const pct = ((value + 1) / 2) * 100;
  const positive = value >= 0;
  return (
    <div className="mb-2">
      <div className="flex justify-between text-xs mb-1">
        <span className="text-muted-foreground">{label}</span>
        <span className="tabular-nums">{value.toFixed(2)}</span>
      </div>
      <div className="h-1.5 rounded bg-muted overflow-hidden relative">
        <div
          className="absolute top-0 bottom-0 w-px bg-border left-1/2"
          aria-hidden
        />
        <div
          className={positive ? "h-full bg-green-500/70" : "h-full bg-red-500/70"}
          style={{
            position: "absolute",
            top: 0,
            bottom: 0,
            left: positive ? "50%" : `${pct}%`,
            right: positive ? `${100 - pct}%` : "50%",
          }}
        />
      </div>
    </div>
  );
}

function QStateCard({ experience }: { experience: ExperienceListItem }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Q 状态</CardTitle>
      </CardHeader>
      <CardContent>
        <RewardRow5 label="q_outcome" value={experience.q_outcome} />
        <RewardRow5 label="q_intent" value={experience.q_intent} />
        <RewardRow5 label="q_execution" value={experience.q_execution} />
        <RewardRow5 label="q_orchestration" value={experience.q_orchestration} />
        <RewardRow5 label="q_expression" value={experience.q_expression} />
        <div className="mt-3 pt-3 border-t text-xs text-muted-foreground">
          q_update_count: {experience.q_update_count}
        </div>
      </CardContent>
    </Card>
  );
}

function QHistoryCard({ updates }: { updates: QUpdateRow[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Q 历史</CardTitle>
      </CardHeader>
      <CardContent>
        {updates.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无 q 更新。</p>
        ) : (
          <ul className="space-y-3 text-xs">
            {updates.map((u) => (
              <li key={u.update_id} className="border-l-2 border-foreground/30 pl-3">
                <div className="text-muted-foreground">{formatDate(u.created_at)}</div>
                <div className="font-mono">
                  α={u.alpha.toFixed(2)} · c={u.confidence.toFixed(2)}
                </div>
                <div className="grid grid-cols-5 gap-1 mt-1 tabular-nums">
                  <Delta label="o" v={u.delta_outcome} />
                  <Delta label="i" v={u.delta_intent} />
                  <Delta label="x" v={u.delta_execution} />
                  <Delta label="r" v={u.delta_orchestration} />
                  <Delta label="e" v={u.delta_expression} />
                </div>
                {u.triggered_by_child ? (
                  <div className="text-muted-foreground mt-1">
                    by child{" "}
                    <span className="font-mono">{u.triggered_by_child.slice(0, 8)}</span>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function Delta({ label, v }: { label: string; v: number | null }) {
  if (v == null) {
    return (
      <div className="text-center text-muted-foreground/50">
        <div className="text-[10px]">{label}</div>
        <div>-</div>
      </div>
    );
  }
  const positive = v >= 0;
  return (
    <div className="text-center">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className={positive ? "text-green-700 dark:text-green-400" : "text-red-700 dark:text-red-400"}>
        {positive ? "+" : ""}
        {v.toFixed(2)}
      </div>
    </div>
  );
}
