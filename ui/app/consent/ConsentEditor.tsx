"use client";

import { useMemo, useState } from "react";
import {
  Check,
  Download,
  Plus,
  RotateCcw,
  Trash2,
  Upload,
  Copy,
} from "lucide-react";

type Mode = "always" | "never" | "ask" | "prompt-on-start" | "dry-run";
type AgentRule = { mode: Mode; default_acl?: string; comment?: string };
type CwdRule = { glob: string; mode: Mode; reason?: string };

interface ConsentDoc {
  mode: Mode;
  default_acl: string;
  save_pending_on_skip: boolean;
  agents: Record<string, AgentRule>;
  cwd_rules: CwdRule[];
  session_overrides: Record<string, { mode: Mode; expires_at?: string }>;
  version: number;
}

const DEFAULT_DOC: ConsentDoc = {
  mode: "ask",
  default_acl: "private",
  save_pending_on_skip: true,
  agents: {},
  cwd_rules: [],
  session_overrides: {},
  version: 1,
};

const MODES: Mode[] = ["always", "never", "ask", "prompt-on-start", "dry-run"];

const MODE_DESCRIPTIONS: Record<Mode, string> = {
  always: "every session uploads silently",
  never: "never upload (hard stop)",
  ask: "prompt before each upload (default)",
  "prompt-on-start": "ask at session start, not at the end",
  "dry-run": "save preview locally; do not transmit",
};

function modeBadgeClass(mode: Mode): string {
  switch (mode) {
    case "always":
      return "bg-emerald-100 text-emerald-900 border-emerald-500/30";
    case "never":
      return "bg-rose-100 text-rose-900 border-rose-500/30";
    case "dry-run":
      return "bg-amber-100 text-amber-900 border-amber-500/30";
    case "prompt-on-start":
      return "bg-cyan-100 text-cyan-900 border-cyan-500/30";
    default:
      return "bg-slate-100 text-slate-900 border-slate-500/30";
  }
}

function tryParseDoc(raw: string): ConsentDoc | { error: string } {
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") {
      return { error: "consent.json must be a JSON object" };
    }
    const merged: ConsentDoc = {
      ...DEFAULT_DOC,
      ...parsed,
      agents: parsed.agents ?? {},
      cwd_rules: Array.isArray(parsed.cwd_rules) ? parsed.cwd_rules : [],
      session_overrides: parsed.session_overrides ?? {},
    };
    if (!MODES.includes(merged.mode)) {
      return { error: `invalid global mode: ${merged.mode}` };
    }
    return merged;
  } catch (err) {
    return {
      error: err instanceof Error ? err.message : "failed to parse JSON",
    };
  }
}

export default function ConsentEditor() {
  const [doc, setDoc] = useState<ConsentDoc>(DEFAULT_DOC);
  const [parseError, setParseError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [newAgent, setNewAgent] = useState("");
  const [newAgentMode, setNewAgentMode] = useState<Mode>("ask");
  const [newCwd, setNewCwd] = useState("");
  const [newCwdMode, setNewCwdMode] = useState<Mode>("never");
  const [newCwdReason, setNewCwdReason] = useState("");

  const json = useMemo(
    () => JSON.stringify(doc, null, 2),
    [doc]
  );

  function loadFile(file: File) {
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result || "");
      const parsed = tryParseDoc(text);
      if ("error" in parsed) {
        setParseError(parsed.error);
        return;
      }
      setDoc(parsed);
      setParseError(null);
    };
    reader.readAsText(file);
  }

  function downloadConsent() {
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "consent.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  function copyJson() {
    navigator.clipboard.writeText(json).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  function addAgent() {
    const name = newAgent.trim();
    if (!name) return;
    setDoc({
      ...doc,
      agents: { ...doc.agents, [name]: { mode: newAgentMode } },
    });
    setNewAgent("");
  }

  function removeAgent(name: string) {
    const next = { ...doc.agents };
    delete next[name];
    setDoc({ ...doc, agents: next });
  }

  function setAgentMode(name: string, mode: Mode) {
    setDoc({
      ...doc,
      agents: {
        ...doc.agents,
        [name]: { ...doc.agents[name], mode },
      },
    });
  }

  function addCwd() {
    const glob = newCwd.trim();
    if (!glob) return;
    setDoc({
      ...doc,
      cwd_rules: [
        ...doc.cwd_rules.filter((r) => r.glob !== glob),
        { glob, mode: newCwdMode, reason: newCwdReason.trim() || undefined },
      ],
    });
    setNewCwd("");
    setNewCwdReason("");
  }

  function removeCwd(glob: string) {
    setDoc({
      ...doc,
      cwd_rules: doc.cwd_rules.filter((r) => r.glob !== glob),
    });
  }

  function reset() {
    setDoc(DEFAULT_DOC);
    setParseError(null);
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Action bar */}
      <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-border/60 bg-white/85 px-4 py-3">
        <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-cyan-500/40 bg-cyan-50 px-3 py-1.5 text-xs font-medium text-cyan-900 hover:bg-cyan-100">
          <Upload className="h-3.5 w-3.5" />
          Load consent.json
          <input
            type="file"
            accept=".json,application/json"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) loadFile(f);
            }}
          />
        </label>
        <button
          type="button"
          onClick={downloadConsent}
          className="inline-flex items-center gap-1.5 rounded-md border border-emerald-500/40 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-900 hover:bg-emerald-100"
        >
          <Download className="h-3.5 w-3.5" />
          Download
        </button>
        <button
          type="button"
          onClick={copyJson}
          className="inline-flex items-center gap-1.5 rounded-md border border-border/60 bg-white px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted/40"
        >
          {copied ? (
            <Check className="h-3.5 w-3.5" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
          {copied ? "copied" : "Copy JSON"}
        </button>
        <button
          type="button"
          onClick={reset}
          className="ml-auto inline-flex items-center gap-1.5 rounded-md border border-border/60 bg-white px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted/40"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          reset to defaults
        </button>
      </div>

      {parseError ? (
        <div className="rounded-md border border-rose-500/40 bg-rose-50 px-4 py-2 text-sm text-rose-900">
          parse error: {parseError}
        </div>
      ) : null}

      {/* Global */}
      <section className="rounded-2xl border border-border/60 bg-white/85 px-5 py-4">
        <h2 className="text-sm font-semibold">Global default</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Used when no agent/cwd rule matches.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {MODES.map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setDoc({ ...doc, mode: m })}
              className={`inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-xs font-medium ${
                doc.mode === m
                  ? modeBadgeClass(m)
                  : "border-border/60 bg-white text-muted-foreground hover:bg-muted/30"
              }`}
              title={MODE_DESCRIPTIONS[m]}
            >
              {doc.mode === m ? <Check className="h-3 w-3" /> : null}
              {m}
            </button>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3 text-xs">
          <label className="inline-flex items-center gap-2 text-muted-foreground">
            default ACL:
            <select
              value={doc.default_acl}
              onChange={(e) => setDoc({ ...doc, default_acl: e.target.value })}
              className="rounded-md border border-border/60 bg-white px-2 py-1 font-mono"
            >
              <option value="private">private</option>
              <option value="team:default">team:default</option>
              <option value="public">public</option>
            </select>
          </label>
          <label className="inline-flex items-center gap-2 text-muted-foreground">
            <input
              type="checkbox"
              checked={doc.save_pending_on_skip}
              onChange={(e) =>
                setDoc({ ...doc, save_pending_on_skip: e.target.checked })
              }
            />
            save skipped sessions to ~/.experience-pool/pending/
          </label>
        </div>
      </section>

      {/* Per-agent rules */}
      <section className="rounded-2xl border border-border/60 bg-white/85 px-5 py-4">
        <h2 className="text-sm font-semibold">Per-agent rules</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Override the global default for a specific agent (claude-code,
          cursor, hermes, agents-chat, …)
        </p>
        <div className="mt-3 space-y-2">
          {Object.keys(doc.agents).length === 0 ? (
            <p className="text-xs italic text-muted-foreground">
              (no agent overrides; all agents inherit the global mode)
            </p>
          ) : (
            Object.entries(doc.agents).map(([name, rule]) => (
              <div
                key={name}
                className="flex flex-wrap items-center gap-2 rounded-md border border-border/60 bg-muted/20 px-3 py-2"
              >
                <code className="font-mono text-xs">{name}</code>
                <select
                  value={rule.mode}
                  onChange={(e) => setAgentMode(name, e.target.value as Mode)}
                  className={`rounded-md border px-2 py-0.5 text-xs ${modeBadgeClass(rule.mode)}`}
                >
                  {MODES.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
                <span className="text-xs text-muted-foreground">
                  {MODE_DESCRIPTIONS[rule.mode]}
                </span>
                <button
                  type="button"
                  onClick={() => removeAgent(name)}
                  className="ml-auto inline-flex items-center gap-1 rounded-md border border-rose-500/30 bg-rose-50 px-2 py-1 text-[11px] text-rose-700 hover:bg-rose-100"
                >
                  <Trash2 className="h-3 w-3" />
                  remove
                </button>
              </div>
            ))
          )}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border/40 pt-3">
          <input
            type="text"
            placeholder="agent name (e.g. cursor)"
            value={newAgent}
            onChange={(e) => setNewAgent(e.target.value)}
            className="rounded-md border border-border/60 bg-white px-3 py-1.5 text-xs"
          />
          <select
            value={newAgentMode}
            onChange={(e) => setNewAgentMode(e.target.value as Mode)}
            className="rounded-md border border-border/60 bg-white px-2 py-1.5 text-xs"
          >
            {MODES.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={addAgent}
            disabled={!newAgent.trim()}
            className="inline-flex items-center gap-1 rounded-md border border-cyan-500/40 bg-cyan-50 px-3 py-1.5 text-xs font-medium text-cyan-900 hover:bg-cyan-100 disabled:opacity-40"
          >
            <Plus className="h-3 w-3" />
            add agent rule
          </button>
        </div>
      </section>

      {/* Cwd rules */}
      <section className="rounded-2xl border border-border/60 bg-white/85 px-5 py-4">
        <h2 className="text-sm font-semibold">Cwd glob rules</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Match the working directory of the session. Higher priority than
          agent rules. <code className="font-mono">**</code> matches any depth.
        </p>
        <div className="mt-3 space-y-2">
          {doc.cwd_rules.length === 0 ? (
            <p className="text-xs italic text-muted-foreground">
              (no cwd rules)
            </p>
          ) : (
            doc.cwd_rules.map((rule) => (
              <div
                key={rule.glob}
                className="flex flex-wrap items-center gap-2 rounded-md border border-border/60 bg-muted/20 px-3 py-2"
              >
                <code className="break-all font-mono text-xs">{rule.glob}</code>
                <span
                  className={`rounded-md border px-2 py-0.5 text-[11px] font-medium ${modeBadgeClass(rule.mode)}`}
                >
                  {rule.mode}
                </span>
                {rule.reason ? (
                  <span className="text-xs text-muted-foreground">
                    — {rule.reason}
                  </span>
                ) : null}
                <button
                  type="button"
                  onClick={() => removeCwd(rule.glob)}
                  className="ml-auto inline-flex items-center gap-1 rounded-md border border-rose-500/30 bg-rose-50 px-2 py-1 text-[11px] text-rose-700 hover:bg-rose-100"
                >
                  <Trash2 className="h-3 w-3" />
                  remove
                </button>
              </div>
            ))
          )}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border/40 pt-3">
          <input
            type="text"
            placeholder="glob (e.g. ~/work/clients/**)"
            value={newCwd}
            onChange={(e) => setNewCwd(e.target.value)}
            className="min-w-[260px] flex-1 rounded-md border border-border/60 bg-white px-3 py-1.5 text-xs font-mono"
          />
          <select
            value={newCwdMode}
            onChange={(e) => setNewCwdMode(e.target.value as Mode)}
            className="rounded-md border border-border/60 bg-white px-2 py-1.5 text-xs"
          >
            {MODES.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
          <input
            type="text"
            placeholder="reason (optional)"
            value={newCwdReason}
            onChange={(e) => setNewCwdReason(e.target.value)}
            className="rounded-md border border-border/60 bg-white px-3 py-1.5 text-xs"
          />
          <button
            type="button"
            onClick={addCwd}
            disabled={!newCwd.trim()}
            className="inline-flex items-center gap-1 rounded-md border border-cyan-500/40 bg-cyan-50 px-3 py-1.5 text-xs font-medium text-cyan-900 hover:bg-cyan-100 disabled:opacity-40"
          >
            <Plus className="h-3 w-3" />
            add cwd rule
          </button>
        </div>
      </section>

      {/* JSON preview */}
      <section className="rounded-2xl border border-border/60 bg-white/85 px-5 py-4">
        <h2 className="text-sm font-semibold">Preview · consent.json</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Click <strong>Download</strong> above to save to your machine, then
          replace <code className="font-mono">~/.experience-pool/consent.json</code>.
        </p>
        <pre className="mt-3 max-h-96 overflow-auto rounded-md border border-border/40 bg-slate-50 p-3 font-mono text-[11px] leading-5">
          {json}
        </pre>
      </section>
    </div>
  );
}
