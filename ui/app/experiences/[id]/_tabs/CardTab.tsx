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

export function CardTab({
  experience,
  reward,
  updates,
}: {
  experience: ExperienceListItem;
  reward: RewardRow | null;
  updates: QUpdateRow[];
}) {
  const steps = tryParseJson<ScriptStep[]>(experience.script_steps) ?? [];
  const tools = tryParseJson<string[]>(experience.tool_capabilities) ?? [];
  const decisions = tryParseJson<string[] | KeyDecision[]>(experience.key_decisions) ?? [];
  const pitfalls = tryParseJson<string[]>(experience.pitfalls) ?? [];
  const preconditions = tryParseJson<string[]>(experience.preconditions) ?? [];

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div className="lg:col-span-2 space-y-6">
        <Card>
          <CardHeader>
            <CardTitle>Intent</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm leading-relaxed">
              {experience.intent_text ?? <span className="text-muted-foreground">No intent extracted.</span>}
            </p>
            {experience.summary ? (
              <p className="text-sm text-muted-foreground mt-3 leading-relaxed">
                {experience.summary}
              </p>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Preconditions</CardTitle>
          </CardHeader>
          <CardContent>
            {preconditions.length === 0 ? (
              <p className="text-sm text-muted-foreground">None.</p>
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
            <CardTitle>Script steps</CardTitle>
          </CardHeader>
          <CardContent>
            {steps.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No script steps were extracted.
              </p>
            ) : (
              <ol className="space-y-4">
                {steps.map((s, i) => (
                  <li key={i} className="border-l-2 border-foreground/30 pl-4">
                    <div className="text-xs uppercase tracking-wide text-muted-foreground">
                      Step {s.step ?? i + 1}
                    </div>
                    <div className="text-sm font-medium mt-1">{s.what ?? ""}</div>
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
                ))}
              </ol>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Tool capabilities</CardTitle>
          </CardHeader>
          <CardContent>
            {tools.length === 0 ? (
              <p className="text-sm text-muted-foreground">None.</p>
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
            <CardTitle>Key decisions</CardTitle>
          </CardHeader>
          <CardContent>
            {decisions.length === 0 ? (
              <p className="text-sm text-muted-foreground">None.</p>
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
            <CardTitle>Pitfalls</CardTitle>
          </CardHeader>
          <CardContent>
            {pitfalls.length === 0 ? (
              <p className="text-sm text-muted-foreground">None.</p>
            ) : (
              <ul className="text-sm list-disc pl-5 space-y-1">
                {pitfalls.map((p, i) => (
                  <li key={i}>{String(p)}</li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="space-y-6">
        <RewardCard reward={reward} />
        <QStateCard experience={experience} />
        <QHistoryCard updates={updates} />
      </div>
    </div>
  );
}

type KeyDecision = { decision?: string; rationale?: string };

function RewardCard({ reward }: { reward: RewardRow | null }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Latest reward</CardTitle>
      </CardHeader>
      <CardContent>
        {!reward ? (
          <p className="text-sm text-muted-foreground">No judge reward yet.</p>
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
                <div className="text-xs uppercase tracking-wide text-muted-foreground mb-1">
                  Rationale
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
        <CardTitle>Q state</CardTitle>
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
        <CardTitle>Q history</CardTitle>
      </CardHeader>
      <CardContent>
        {updates.length === 0 ? (
          <p className="text-sm text-muted-foreground">No q updates yet.</p>
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
        <div className="text-[10px] uppercase">{label}</div>
        <div>-</div>
      </div>
    );
  }
  const positive = v >= 0;
  return (
    <div className="text-center">
      <div className="text-[10px] uppercase text-muted-foreground">{label}</div>
      <div className={positive ? "text-green-700 dark:text-green-400" : "text-red-700 dark:text-red-400"}>
        {positive ? "+" : ""}
        {v.toFixed(2)}
      </div>
    </div>
  );
}
