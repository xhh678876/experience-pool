"use client";

import { useMemo, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  ChevronDown,
  ChevronRight,
  MessageSquare,
  Sparkles,
  Terminal,
  Wrench,
} from "lucide-react";
import type { TrajectoryRead } from "./readTrajectory";

type Block =
  | { type: "text"; text: string }
  | { type: "thinking"; thinking?: string; text?: string }
  | {
      type: "tool_use";
      id?: string;
      name?: string;
      input?: unknown;
    }
  | {
      type: "tool_result";
      tool_use_id?: string;
      content?: unknown;
      is_error?: boolean;
    }
  | { type: string; [k: string]: unknown };

type Turn = {
  role?: string;
  content?: string | Block[] | unknown;
  tool_calls?: unknown[];
  tool_call_id?: string;
  name?: string;
  [k: string]: unknown;
};

type ParsedTrajectory =
  | { ok: true; turns: Turn[] }
  | { ok: false; reason: string };

function parseTrajectory(text: string | null): ParsedTrajectory {
  if (!text) return { ok: false, reason: "empty" };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (e) {
    return { ok: false, reason: "JSON 无法解析" };
  }
  let arr: unknown;
  if (Array.isArray(parsed)) arr = parsed;
  else if (parsed && typeof parsed === "object") {
    const obj = parsed as Record<string, unknown>;
    if (Array.isArray(obj.trajectory)) arr = obj.trajectory;
    else if (Array.isArray(obj.messages)) arr = obj.messages;
    else if (Array.isArray(obj.events)) arr = obj.events;
  }
  if (!Array.isArray(arr)) return { ok: false, reason: "找不到 trajectory/messages 数组" };
  return {
    ok: true,
    turns: arr.filter((t): t is Turn => t !== null && typeof t === "object"),
  };
}

export function TrajectoryTab({
  trajectory,
  path,
}: {
  trajectory: TrajectoryRead;
  path: string | null;
}) {
  const [showRaw, setShowRaw] = useState(false);
  const [viewMode, setViewMode] = useState<"chat" | "json">("chat");

  const parsed = useMemo(() => parseTrajectory(trajectory.bodyText), [trajectory.bodyText]);

  if (!trajectory.exists) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>轨迹</CardTitle>
        </CardHeader>
        <CardContent>
          {!path ? (
            <p className="text-sm text-muted-foreground">这条经验没有 trajectory_path。</p>
          ) : (
            <>
              <p className="text-sm text-muted-foreground">无法读取轨迹文件：</p>
              <pre className="text-xs font-mono mt-2 p-3 bg-muted rounded">{path}</pre>
              {trajectory.error ? (
                <p className="text-sm text-rose-700 mt-2">{trajectory.error}</p>
              ) : null}
            </>
          )}
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border/60 bg-white/85 px-3 py-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-[11px] text-muted-foreground truncate">
            {trajectory.path}
          </span>
          {parsed.ok ? (
            <span className="rounded-full border border-cyan-600/25 bg-cyan-50 px-2 py-0.5 text-[11px] text-cyan-800">
              {parsed.turns.length} turn
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-1">
          <SegBtn active={viewMode === "chat"} onClick={() => setViewMode("chat")}>
            <MessageSquare className="h-3.5 w-3.5" />
            气泡
          </SegBtn>
          <SegBtn active={viewMode === "json"} onClick={() => setViewMode("json")}>
            <Terminal className="h-3.5 w-3.5" />
            JSON
          </SegBtn>
        </div>
      </div>

      {trajectory.redactedDiffers ? (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-amber-900 text-sm flex items-center justify-between">
          <span>与磁盘原始轨迹相比，部分内容已脱敏。</span>
          <Button type="button" size="sm" variant="outline" onClick={() => setShowRaw((v) => !v)}>
            {showRaw ? "只看脱敏版" : "并排查看原始版"}
          </Button>
        </div>
      ) : null}

      {viewMode === "json" ? (
        <Card>
          <CardContent className="p-0">
            {showRaw && trajectory.rawBodyText ? (
              <div className="grid grid-cols-2 divide-x">
                <pre className="text-xs font-mono p-4 max-h-[70vh] overflow-auto whitespace-pre-wrap break-all">
                  {trajectory.bodyText}
                </pre>
                <pre className="text-xs font-mono p-4 max-h-[70vh] overflow-auto whitespace-pre-wrap break-all bg-muted/30">
                  {trajectory.rawBodyText}
                </pre>
              </div>
            ) : (
              <pre className="text-xs font-mono p-4 max-h-[70vh] overflow-auto whitespace-pre-wrap break-all">
                {trajectory.bodyText}
              </pre>
            )}
          </CardContent>
        </Card>
      ) : !parsed.ok ? (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            无法以气泡视图渲染（{parsed.reason}）。请切到 JSON 视图。
          </CardContent>
        </Card>
      ) : (
        <ChatView turns={parsed.turns} />
      )}
    </div>
  );
}

function SegBtn({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "inline-flex h-7 items-center gap-1 rounded-md border px-2 text-xs transition " +
        (active
          ? "border-cyan-700 bg-cyan-700 text-white"
          : "border-border/60 bg-white/70 text-muted-foreground hover:border-cyan-600/40 hover:text-cyan-800")
      }
    >
      {children}
    </button>
  );
}

function ChatView({ turns }: { turns: Turn[] }) {
  return (
    <div className="flex flex-col gap-3">
      {turns.map((turn, i) => (
        <TurnRow key={i} turn={turn} index={i} />
      ))}
    </div>
  );
}

function TurnRow({ turn, index }: { turn: Turn; index: number }) {
  const role = String(turn.role ?? "unknown");
  const blocks = normalizeContent(turn);
  const isUser = role === "user";
  const isTool = role === "tool" || hasOnlyToolResults(blocks);
  const isAssistant = role === "assistant" || role === "model";

  // user with only tool_result blocks (Anthropic style) → render as tool channel
  const renderAsTool = isTool || (isUser && hasOnlyToolResults(blocks));

  if (renderAsTool) {
    return (
      <div className="flex flex-col gap-2">
        {blocks.map((b, j) => (
          <BlockRender key={j} block={b} channel="tool" />
        ))}
      </div>
    );
  }

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-tr-sm border border-cyan-600/25 bg-cyan-50 px-4 py-2.5 text-sm leading-6 text-cyan-950">
          <RoleHeader role="user" index={index} />
          {blocks.map((b, j) => (
            <BlockRender key={j} block={b} channel="user" />
          ))}
        </div>
      </div>
    );
  }

  if (isAssistant) {
    return (
      <div className="flex justify-start">
        <div className="max-w-[88%] rounded-2xl rounded-tl-sm border border-border/70 bg-white px-4 py-2.5 text-sm leading-6">
          <RoleHeader role="assistant" index={index} />
          {blocks.map((b, j) => (
            <BlockRender key={j} block={b} channel="assistant" />
          ))}
        </div>
      </div>
    );
  }

  // system / unknown
  return (
    <div className="flex justify-center">
      <div className="max-w-[80%] rounded-md border border-dashed border-border/70 bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
        <RoleHeader role={role} index={index} />
        {blocks.map((b, j) => (
          <BlockRender key={j} block={b} channel="system" />
        ))}
      </div>
    </div>
  );
}

function RoleHeader({ role, index }: { role: string; index: number }) {
  const label =
    role === "user" ? "用户" : role === "assistant" || role === "model" ? "助手" : role;
  const Icon = role === "user" ? null : role === "assistant" || role === "model" ? Sparkles : null;
  return (
    <div className="mb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground">
      {Icon ? <Icon className="h-3 w-3" /> : null}
      <span>{label}</span>
      <span className="font-mono text-muted-foreground/70">#{index + 1}</span>
    </div>
  );
}

function BlockRender({
  block,
  channel,
}: {
  block: Block;
  channel: "user" | "assistant" | "tool" | "system";
}) {
  if (block.type === "text") {
    const text = (block as { text?: string }).text ?? "";
    return <p className="whitespace-pre-wrap break-words">{text}</p>;
  }
  if (block.type === "thinking") {
    const text =
      (block as { thinking?: string; text?: string }).thinking ??
      (block as { text?: string }).text ??
      "";
    return (
      <div className="mt-1 rounded-md border border-dashed border-amber-400/40 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-900">
        <div className="mb-0.5 text-[10px] uppercase tracking-wider">thinking</div>
        <p className="whitespace-pre-wrap break-words">{text}</p>
      </div>
    );
  }
  if (block.type === "tool_use") {
    const b = block as {
      id?: string;
      name?: string;
      input?: unknown;
    };
    return (
      <ToolCallCard name={b.name ?? "tool"} id={b.id} input={b.input} />
    );
  }
  if (block.type === "tool_result") {
    const b = block as {
      tool_use_id?: string;
      content?: unknown;
      is_error?: boolean;
    };
    return (
      <ToolResultCard
        toolUseId={b.tool_use_id}
        content={b.content}
        isError={Boolean(b.is_error)}
      />
    );
  }
  // unknown block
  return (
    <pre className="mt-1 max-h-40 overflow-auto rounded-md border border-border/60 bg-muted/30 px-2.5 py-1.5 font-mono text-[11px]">
      {JSON.stringify(block, null, 2)}
    </pre>
  );
}

function ToolCallCard({
  name,
  id,
  input,
}: {
  name: string;
  id?: string;
  input: unknown;
}) {
  const [open, setOpen] = useState(false);
  const inputStr =
    typeof input === "string" ? input : JSON.stringify(input ?? {}, null, 2);
  return (
    <div className="my-1.5 rounded-lg border border-cyan-600/25 bg-cyan-50/50 text-[12px]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 hover:bg-cyan-50"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 text-cyan-800" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-cyan-800" />
        )}
        <Wrench className="h-3.5 w-3.5 text-cyan-800" />
        <span className="font-mono font-semibold text-cyan-900">{name}</span>
        {id ? (
          <span className="font-mono text-[10px] text-muted-foreground">{id}</span>
        ) : null}
        {!open ? (
          <span className="ml-auto truncate font-mono text-[11px] text-cyan-800/70">
            {inputStr.slice(0, 80).replace(/\s+/g, " ")}
          </span>
        ) : null}
      </button>
      {open ? (
        <pre className="border-t border-cyan-600/20 bg-white/60 px-2.5 py-2 font-mono text-[11px] leading-5 max-h-72 overflow-auto whitespace-pre-wrap break-all">
          {inputStr}
        </pre>
      ) : null}
    </div>
  );
}

function ToolResultCard({
  toolUseId,
  content,
  isError,
}: {
  toolUseId?: string;
  content: unknown;
  isError: boolean;
}) {
  const [open, setOpen] = useState(false);

  // content can be: string | array of { type:'text', text:string } | array of strings
  let display: string;
  if (typeof content === "string") {
    display = content;
  } else if (Array.isArray(content)) {
    display = content
      .map((c) => {
        if (typeof c === "string") return c;
        if (c && typeof c === "object" && "text" in c) return String((c as { text?: string }).text ?? "");
        return JSON.stringify(c);
      })
      .join("\n");
  } else {
    display = JSON.stringify(content ?? "", null, 2);
  }

  // If the content looks like a single-line JSON dump, pretty-print it for
  // the expanded view (keep collapsed preview as the original 1-line form).
  const trimmed = display.trim();
  let pretty = display;
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      pretty = JSON.stringify(JSON.parse(trimmed), null, 2);
    } catch {
      // not valid JSON — leave as-is
    }
  }

  const lines = pretty.split("\n");
  const preview = display.replace(/\s+/g, " ").slice(0, 90);

  const tone = isError
    ? "border-rose-500/30 bg-rose-50/60 text-rose-900"
    : "border-amber-500/25 bg-amber-50/40 text-amber-950";

  return (
    <div className={`my-1.5 rounded-lg border text-[12px] ${tone}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 hover:bg-amber-50/60"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5" />
        )}
        <span className="font-mono font-semibold">
          {isError ? "tool_error" : "tool_result"}
        </span>
        {toolUseId ? (
          <span className="font-mono text-[10px] opacity-70">→ {toolUseId}</span>
        ) : null}
        <span className="font-mono text-[10px] opacity-70">
          {lines.length} line / {display.length} char
        </span>
        {!open ? (
          <span className="ml-auto truncate font-mono text-[11px] opacity-80">
            {preview}
          </span>
        ) : null}
      </button>
      {open ? (
        <pre className="border-t border-current/15 bg-white/55 px-2.5 py-2 font-mono text-[11px] leading-5 max-h-72 overflow-auto whitespace-pre-wrap break-all">
          {pretty}
        </pre>
      ) : null}
    </div>
  );
}

function normalizeContent(turn: Turn): Block[] {
  const blocks: Block[] = [];
  const c = turn.content;

  // Short-circuit: a flat `role:"tool"` turn (Hermes / OpenAI / our uploader
  // shape) means the entire content IS the tool output. Do NOT also emit a
  // text block — that's what was making the raw JSON dump leak out of the
  // tool_result card and render as floating page text.
  if (turn.role === "tool") {
    const linkId =
      (typeof turn.tool_call_id === "string" && turn.tool_call_id) ||
      (turn as { tool_result_for?: string }).tool_result_for ||
      undefined;
    return [
      {
        type: "tool_result",
        tool_use_id: linkId,
        content: c ?? "",
      },
    ];
  }

  // 1. Coerce content into text/array blocks first.
  if (typeof c === "string") {
    if (c.trim()) blocks.push({ type: "text", text: c });
  } else if (Array.isArray(c)) {
    for (const b of c) {
      if (typeof b === "string") {
        if (b.trim()) blocks.push({ type: "text", text: b });
      } else if (b && typeof b === "object") {
        blocks.push(b as Block);
      } else if (b != null) {
        blocks.push({ type: "text", text: String(b) });
      }
    }
  } else if (c != null) {
    blocks.push({ type: "text", text: JSON.stringify(c) });
  }

  // 2. Append OpenAI / uploader-shape tool_calls (assistant turns).
  if (Array.isArray(turn.tool_calls)) {
    for (const tc of turn.tool_calls as Array<Record<string, unknown>>) {
      // OpenAI nests args under .function.arguments; our uploader sends them
      // flat as .input. Support both.
      const fn = (tc.function ?? {}) as Record<string, unknown>;
      const name =
        (fn.name as string | undefined) ??
        (tc.name as string | undefined) ??
        "function";
      const input =
        tc.input ??
        fn.arguments ??
        (tc as { arguments?: unknown }).arguments;
      blocks.push({
        type: "tool_use",
        id: tc.id as string | undefined,
        name,
        input,
      });
    }
  }

  // (role:"tool" handled by short-circuit above.)
  return blocks;
}

function hasOnlyToolResults(blocks: Block[]): boolean {
  return blocks.length > 0 && blocks.every((b) => b.type === "tool_result");
}
