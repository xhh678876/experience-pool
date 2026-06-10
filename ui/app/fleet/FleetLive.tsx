"use client";

import { useEffect, useState } from "react";

export interface FleetWindow {
  pid: number;
  session_id: string;
  project_name?: string | null;
  cwd?: string | null;
  status?: string | null;
  triage?: string | null;
  triage_reason?: string | null;
  current_task?: string | null;
  waiting_for?: string | null;
  permission_msg?: string | null;
  idle_seconds?: number | null;
  alive?: boolean;
  version?: string | null;
}

export interface FleetData {
  windows?: FleetWindow[];
  error?: string;
}

interface TimelineEvent {
  ts?: string;
  kind?: string;
  role?: string;
  text?: string;
  tool?: string | null;
}
interface Timeline {
  events?: TimelineEvent[];
  skills_used?: unknown[];
  memory_ops?: unknown[];
}

const TRIAGE: Record<string, { label: string; cls: string }> = {
  working: { label: "运行中", cls: "bg-cyan-100 text-cyan-900" },
  waiting: { label: "等待确认", cls: "bg-amber-100 text-amber-900" },
  stalled: { label: "卡住", cls: "bg-rose-100 text-rose-900" },
  completed: { label: "已完成", cls: "bg-emerald-100 text-emerald-900" },
  closeable: { label: "可关闭", cls: "bg-muted text-muted-foreground" },
};

const ROLE: Record<string, string> = {
  user: "bg-cyan-100 text-cyan-900",
  assistant: "bg-violet-100 text-violet-900",
  system: "bg-muted text-muted-foreground",
  tool: "bg-emerald-100 text-emerald-900",
};

function idle(sec?: number | null): string {
  if (sec == null) return "—";
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  return `${Math.round(sec / 3600)}h`;
}

export default function FleetLive({ initial, pollUrl }: { initial: FleetData; pollUrl: string }) {
  const [data, setData] = useState<FleetData>(initial);
  const [live, setLive] = useState(false);
  const [openPid, setOpenPid] = useState<number | null>(null);
  const [timelines, setTimelines] = useState<Record<number, Timeline>>({});
  const [tlLoading, setTlLoading] = useState<number | null>(null);

  useEffect(() => {
    let stopped = false;
    const tick = async () => {
      try {
        const r = await fetch(pollUrl, { cache: "no-store" });
        if (!r.ok || stopped) return;
        setData((await r.json()) as FleetData);
        setLive(true);
      } catch {
        if (!stopped) setLive(false);
      }
    };
    const id = setInterval(tick, 3000);
    return () => {
      stopped = true;
      clearInterval(id);
    };
  }, [pollUrl]);

  // 展开会话 → 拉 timeline（按 pid 缓存；展开后每 5s 刷新一次当前打开的会话）。
  async function loadTimeline(pid: number) {
    setTlLoading(pid);
    try {
      const r = await fetch(`${pollUrl}/${pid}/timeline`, { cache: "no-store" });
      if (r.ok) {
        const j = (await r.json()) as Timeline;
        setTimelines((t) => ({ ...t, [pid]: j }));
      }
    } catch {
      /* 忽略，UI 显示空 */
    } finally {
      setTlLoading(null);
    }
  }

  function toggle(pid: number) {
    if (openPid === pid) {
      setOpenPid(null);
      return;
    }
    setOpenPid(pid);
    if (!timelines[pid]) void loadTimeline(pid);
  }

  useEffect(() => {
    if (openPid == null) return;
    const id = setInterval(() => void loadTimeline(openPid), 5000);
    return () => clearInterval(id);
  }, [openPid]); // eslint-disable-line react-hooks/exhaustive-deps

  const windows = data.windows ?? [];
  const counts = windows.reduce<Record<string, number>>((acc, w) => {
    const t = w.triage ?? "other";
    acc[t] = (acc[t] ?? 0) + 1;
    return acc;
  }, {});
  const waiting = counts.waiting ?? 0;

  return (
    <div className="flex flex-col gap-4 pb-12">
      <section className="rounded-2xl border border-border/60 bg-white/85">
        <div className="flex flex-wrap items-center gap-2 border-b border-border/60 px-5 py-3">
          <h1 className="text-sm font-semibold">claude-fleet · 会话编队</h1>
          <span className="text-xs text-muted-foreground">监控本机 claude-code / codex 会话 · 点会话展开内容</span>
          <span
            className={`ml-auto inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] ${
              live ? "bg-emerald-100 text-emerald-800" : "bg-muted text-muted-foreground"
            }`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${live ? "bg-emerald-500" : "bg-muted-foreground/50"}`} />
            {live ? "实时（3s）" : "静态快照"}
          </span>
        </div>

        {data.error ? (
          <div className="px-5 py-6 text-sm text-rose-700">
            {data.error}
            <p className="mt-1 text-xs text-muted-foreground">
              确认 claude-fleet 已在本机运行（:7878）。
            </p>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap gap-2 px-5 py-3 text-[11px]">
              <span className="rounded-md bg-muted/60 px-2 py-1">总计 {windows.length}</span>
              {Object.entries(TRIAGE).map(([k, v]) =>
                counts[k] ? (
                  <span key={k} className={`rounded-md px-2 py-1 ${v.cls}`}>
                    {v.label} {counts[k]}
                  </span>
                ) : null,
              )}
              {waiting > 0 ? (
                <span className="rounded-md bg-amber-500 px-2 py-1 font-medium text-white">
                  ⚠ {waiting} 个会话在等你确认权限
                </span>
              ) : null}
            </div>

            {windows.length === 0 ? (
              <div className="px-5 py-10 text-center text-sm text-muted-foreground">
                本机暂无活跃的 claude-code / codex 会话。
              </div>
            ) : (
              <ul className="divide-y divide-border/60">
                {windows.map((w) => {
                  const t = TRIAGE[w.triage ?? ""] ?? {
                    label: w.triage ?? w.status ?? "—",
                    cls: "bg-muted text-muted-foreground",
                  };
                  const open = openPid === w.pid;
                  const tl = timelines[w.pid];
                  return (
                    <li key={w.pid} className="px-5 py-3">
                      <button
                        type="button"
                        onClick={() => toggle(w.pid)}
                        className="flex w-full flex-col gap-1 text-left"
                      >
                        <div className="flex flex-wrap items-center gap-2 text-[11px]">
                          <span className="text-muted-foreground">{open ? "▾" : "▸"}</span>
                          <span className={`rounded px-1.5 py-0.5 font-medium ${t.cls}`}>{t.label}</span>
                          <span className="font-medium text-foreground">{w.project_name || w.cwd || "(unknown)"}</span>
                          <span className="text-muted-foreground">pid {w.pid}</span>
                          {w.version ? <span className="text-muted-foreground">v{w.version}</span> : null}
                          <span className="ml-auto font-mono text-[10px] text-muted-foreground">
                            idle {idle(w.idle_seconds)} · {w.session_id.slice(0, 8)}
                          </span>
                        </div>
                        {w.waiting_for || w.permission_msg ? (
                          <p className="text-xs text-amber-800">等待：{w.permission_msg || w.waiting_for}</p>
                        ) : null}
                        {w.current_task ? (
                          <p className="line-clamp-2 text-sm text-foreground/90">{w.current_task}</p>
                        ) : null}
                      </button>

                      {open ? (
                        <div className="mt-2 rounded-lg border border-border/60 bg-muted/20 p-2">
                          {tlLoading === w.pid && !tl ? (
                            <p className="px-2 py-3 text-xs text-muted-foreground">加载会话内容…</p>
                          ) : !tl?.events?.length ? (
                            <p className="px-2 py-3 text-xs text-muted-foreground">该会话暂无可显示内容。</p>
                          ) : (
                            <div className="max-h-96 space-y-1.5 overflow-y-auto px-1 py-1">
                              {tl.events.slice(-60).map((e, i) => (
                                <div key={i} className="text-xs">
                                  <span
                                    className={`mr-1.5 inline-block rounded px-1 py-0.5 text-[9px] font-medium ${
                                      ROLE[e.role ?? ""] ?? "bg-muted text-muted-foreground"
                                    }`}
                                  >
                                    {e.role ?? e.kind ?? "?"}
                                    {e.tool ? `·${e.tool}` : ""}
                                  </span>
                                  <span className="whitespace-pre-wrap break-words text-foreground/80">
                                    {(e.text ?? "").slice(0, 600)}
                                    {(e.text ?? "").length > 600 ? "…" : ""}
                                  </span>
                                </div>
                              ))}
                            </div>
                          )}
                          {tl?.events?.length ? (
                            <p className="px-1 pt-1 text-[10px] text-muted-foreground/70">
                              显示最近 {Math.min(60, tl.events.length)} / {tl.events.length} 条 · 每 5s 刷新
                            </p>
                          ) : null}
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </>
        )}
      </section>
    </div>
  );
}
