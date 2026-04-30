/**
 * v0 lite: client-side sanitize + rule-based structuring.
 *
 * Same shape as core/exp_core/lite.py but in TypeScript so the npm CLI is
 * self-contained. Keep the regex set small and high-confidence here — the
 * server runs the full sanitizer again as defense-in-depth.
 */

export interface LiteCard {
  query: string;
  intent: string;
  steps: string[];
  outcome: string;
  task_type: string;
  source_model: string;
  sensitivity: "low" | "medium" | "high";
  acl: string;
  tags: string[];
  redactions: Record<string, number>;
}

interface RuleSpec {
  name: string;
  pattern: RegExp;
  placeholder: string;
}

// High-confidence rules only. Server applies the full set.
const RULES: RuleSpec[] = [
  { name: "anthropic_key", pattern: /sk-ant-[A-Za-z0-9_\-]{16,}/g, placeholder: "<SECRET>" },
  { name: "openai_key", pattern: /\bsk-(?!ant-)[A-Za-z0-9]{20,}\b/g, placeholder: "<SECRET>" },
  { name: "stripe_secret", pattern: /\bsk_live_[0-9a-zA-Z]{16,}\b/g, placeholder: "<SECRET>" },
  { name: "github_token", pattern: /ghp_[0-9a-zA-Z]{16,}/g, placeholder: "<SECRET>" },
  { name: "aws_access_key", pattern: /\bAKIA[0-9A-Z]{16}\b/g, placeholder: "<KEY>" },
  { name: "url_credentials", pattern: /https?:\/\/([^\s/:@]+):([^\s/@]+)@/g, placeholder: "https://<USER>:<PASS>@" },
  { name: "email", pattern: /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/g, placeholder: "<EMAIL>" },
  { name: "ipv4", pattern: /\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b/g, placeholder: "<IP>" },
];

function sanitizeText(text: string): { out: string; counts: Record<string, number> } {
  let out = text;
  const counts: Record<string, number> = {};
  for (const rule of RULES) {
    const matches = out.match(rule.pattern);
    if (matches && matches.length > 0) {
      counts[rule.name] = (counts[rule.name] ?? 0) + matches.length;
      out = out.replace(rule.pattern, rule.placeholder);
    }
  }
  return { out, counts };
}

interface PrepareOptions {
  task_type: string;
  source_model: string;
  sensitivity: "low" | "medium" | "high";
  acl: string;
  tags: string[];
}

interface Turn {
  role?: string;
  content?: string;
  [k: string]: unknown;
}

export function prepareLocalRuleBased(
  trajectory: Turn[],
  opts: PrepareOptions
): LiteCard {
  let query = "";
  const steps: string[] = [];
  let outcome = "";
  const totals: Record<string, number> = {};

  for (const turn of trajectory) {
    const role = turn.role;
    let content = (typeof turn.content === "string" ? turn.content : "").trim();
    if (!content) continue;
    const { out, counts } = sanitizeText(content);
    content = out;
    for (const [k, v] of Object.entries(counts)) {
      totals[k] = (totals[k] ?? 0) + v;
    }
    if (role === "user" && !query) {
      query = content;
    } else if (role === "assistant") {
      steps.push(content.slice(0, 280));
      outcome = content;
    }
  }

  const intent = (query.slice(0, 120) || "unspecified task").trim();
  return {
    query: query || "(no user turn)",
    intent,
    steps,
    outcome: outcome.slice(0, 500) || "(no assistant turn)",
    task_type: opts.task_type,
    source_model: opts.source_model,
    sensitivity: opts.sensitivity,
    acl: opts.acl,
    tags: opts.tags,
    redactions: totals,
  };
}
