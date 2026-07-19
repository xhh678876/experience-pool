"use server";

import fs from "node:fs";
import { getDb } from "./db";

const COMMUNITY_THRESHOLD = 3;

// --------------------------------------------------------------------------
// Strict-public sanitize rules — kept in sync with Python's
// core/exp_core/sanitize_public.py. Any change there must be mirrored
// here so the UI publish action and the HTTP /v1/lite/publish endpoint
// reach the same verdict.
// --------------------------------------------------------------------------

interface StrictRule {
  rule: string;
  reason: string;
  pattern: RegExp;
}

const STRICT_PUBLIC_RULES: StrictRule[] = [
  { rule: "file_uri", reason: "leaks local filesystem path",
    pattern: /file:\/\/[^\s'"]*/gi },
  { rule: "local_app_resource", reason: "leaks local IM-app resource path",
    pattern: /\b[A-Za-z]+Shell\/sdk_storage\/[^\s'"]+/gi },
  { rule: "vscode_resource", reason: "leaks IDE-internal resource",
    pattern: /\bvscode-(?:resource|webview|file):\/\/[^\s'"]*/gi },
  { rule: "browser_extension_url", reason: "leaks installed browser extension ID",
    pattern: /\b(?:chrome|moz|edge|safari-web)-extension:\/\/[^\s'"]*/gi },
  { rule: "localhost_url", reason: "leaks localhost endpoint",
    pattern: /\bhttps?:\/\/(?:localhost|127\.\d{1,3}\.\d{1,3}\.\d{1,3}|0\.0\.0\.0|\[::1\])(?::\d+)?(?:\/[^\s'"]*)?/gi },
  { rule: "private_ip_url", reason: "leaks private-network endpoint",
    pattern: /\bhttps?:\/\/(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})(?::\d+)?(?:\/[^\s'"]*)?/gi },
  { rule: "absolute_system_path", reason: "leaks absolute system path",
    pattern: /(?<![\w/])(?:\/(?:etc|var|opt|tmp|private|System|Library|Applications)\/[^\s'":]+)/g },
  { rule: "windows_path", reason: "leaks Windows absolute path",
    pattern: /\b[A-Za-z]:\\(?:Users|Windows|Program Files|Documents and Settings)\\[^\s'"]+/gi },
  { rule: "session_uuid", reason: "leaks session/experience UUID",
    pattern: /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi },
];

const SKIP_KEYS = new Set([
  "id", "type", "role", "tool_use_id", "tool_call_id",
  "name", "subtype", "model", "stop_reason", "stop_sequence",
  "usage", "index", "ts", "tool_result_for",
]);

export interface PublishHit {
  rule: string;
  reason: string;
  location: string;
  preview: string;
}

interface InternalHit extends PublishHit {
  fullSnippet: string;
}

function scanString(text: string, location: string, hits: InternalHit[]): void {
  if (!text) return;
  for (const r of STRICT_PUBLIC_RULES) {
    // Reset stateful regex
    r.pattern.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = r.pattern.exec(text)) !== null) {
      hits.push({
        rule: r.rule,
        reason: r.reason,
        location,
        preview: m[0].slice(0, 120),
        fullSnippet: m[0],
      });
      // Avoid infinite loop on zero-width match
      if (m.index === r.pattern.lastIndex) r.pattern.lastIndex++;
    }
  }
}

function walk(node: unknown, path: string, hits: InternalHit[]): void {
  if (typeof node === "string") {
    scanString(node, path, hits);
    return;
  }
  if (Array.isArray(node)) {
    node.forEach((item, i) => walk(item, `${path}[${i}]`, hits));
    return;
  }
  if (node && typeof node === "object") {
    for (const [k, v] of Object.entries(node)) {
      if (SKIP_KEYS.has(k)) continue;
      if (typeof v !== "string" && !Array.isArray(v) && (v === null || typeof v !== "object")) {
        continue;
      }
      walk(v, path ? `${path}.${k}` : k, hits);
    }
  }
}

interface ExperienceRow {
  experience_id: string;
  agent_id: string;
  agent_name: string;
  agent_owner: string;
  acl: string;
  publish_status: string | null;
  trajectory_path: string | null;
  query: string | null;
  intent_text: string | null;
  outcome: string | null;
  summary: string | null;
  script_steps: string | null;
  revoked: number;
}

function loadExperience(experienceId: string): ExperienceRow | null {
  const db = getDb();
  const row = db
    .prepare(
      `
      SELECT e.experience_id, e.agent_id, e.acl,
             COALESCE(e.publish_status, 'private') AS publish_status,
             e.trajectory_path, e.query, e.intent_text, e.outcome, e.summary,
             e.script_steps,
             COALESCE(e.revoked, 0) AS revoked,
             a.name AS agent_name,
             COALESCE(a.owner, a.name) AS agent_owner
      FROM experiences e JOIN agents a USING(agent_id)
      WHERE e.experience_id = ?
      `
    )
    .get(experienceId) as ExperienceRow | undefined;
  return row ?? null;
}

function ownerOf(viewer: string): string {
  const db = getDb();
  const row = db
    .prepare(
      "SELECT COALESCE(owner, name) AS owner FROM agents WHERE name = ?"
    )
    .get(viewer) as { owner: string } | undefined;
  return row?.owner ?? viewer;
}

export interface PublishResult {
  ok: boolean;
  status:
    | "published"
    | "already_public"
    | "already_private"
    | "blocked"
    | "not_found"
    | "forbidden"
    | "unpublished";
  experience_id: string;
  blocking_hits: PublishHit[];
  publish_count?: number;
  community_unlocked?: boolean;
  error?: string;
}

export async function publishExperience(
  viewer: string,
  experienceId: string
): Promise<PublishResult> {
  const db = getDb();
  const row = loadExperience(experienceId);
  if (!row) {
    return {
      ok: false,
      status: "not_found",
      experience_id: experienceId,
      blocking_hits: [],
      error: "experience not found",
    };
  }
  // Multi-agent ownership: viewer must share the same `owner` as the
  // experience's agent.
  const viewerOwner = ownerOf(viewer);
  if (viewerOwner !== row.agent_owner) {
    return {
      ok: false,
      status: "forbidden",
      experience_id: experienceId,
      blocking_hits: [],
      error: `viewer "${viewer}" (owner=${viewerOwner}) does not own this experience`,
    };
  }
  if (row.revoked) {
    return {
      ok: false,
      status: "forbidden",
      experience_id: experienceId,
      blocking_hits: [],
      error: "cannot publish a revoked experience",
    };
  }
  if (row.publish_status === "published") {
    return {
      ok: true,
      status: "already_public",
      experience_id: experienceId,
      blocking_hits: [],
    };
  }

  // Run strict-public scan on card fields + trajectory sidecar.
  const hits: InternalHit[] = [];
  scanString(row.query ?? "", "card.query", hits);
  scanString(row.intent_text ?? "", "card.intent", hits);
  scanString(row.outcome ?? "", "card.outcome", hits);
  scanString(row.summary ?? "", "card.summary", hits);
  try {
    const steps: unknown[] = JSON.parse(row.script_steps ?? "[]");
    steps.forEach((s, i) => {
      if (typeof s === "string") scanString(s, `card.steps[${i}]`, hits);
    });
  } catch {
    /* ignore malformed steps */
  }
  if (row.trajectory_path) {
    try {
      const sidecar = JSON.parse(
        fs.readFileSync(/* turbopackIgnore: true */ row.trajectory_path, "utf-8")
      );
      walk(sidecar.trajectory ?? null, "trajectory", hits);
      walk(sidecar.system ?? null, "system", hits);
      walk(sidecar.tools ?? null, "tools", hits);
      walk(sidecar.meta ?? null, "meta", hits);
    } catch {
      /* trajectory file missing/unreadable — proceed with card-only check */
    }
  }

  const nowIso = new Date().toISOString();

  if (hits.length > 0) {
    // Reject — record the strict_redactions summary, audit, do NOT publish.
    const summary: Record<string, number> = {};
    for (const h of hits) summary[h.rule] = (summary[h.rule] ?? 0) + 1;

    const tx = db.transaction(() => {
      db.prepare(
        `
        UPDATE experiences
        SET publish_status = 'rejected',
            strict_redactions = ?
        WHERE experience_id = ?
        `
      ).run(
        JSON.stringify({ summary, blocked_at: nowIso }),
        experienceId
      );
      db.prepare(
        "INSERT INTO audit_log (actor, actor_kind, action, target_id, payload) VALUES (?, ?, ?, ?, ?)"
      ).run(
        viewer,
        "ui",
        "publish_rejected",
        experienceId,
        JSON.stringify({
          hit_count: hits.length,
          summary,
          blocking_hits: hits.slice(0, 50).map(({ fullSnippet, ...rest }) => rest),
        })
      );
    });
    tx();

    const quotaRow = db
      .prepare("SELECT publish_count FROM owner_quotas WHERE owner = ?")
      .get(row.agent_owner) as { publish_count: number } | undefined;
    const publishCount = quotaRow?.publish_count ?? 0;
    return {
      ok: false,
      status: "blocked",
      experience_id: experienceId,
      blocking_hits: hits.slice(0, 50).map(({ fullSnippet, ...rest }) => rest),
      publish_count: publishCount,
      community_unlocked: publishCount >= COMMUNITY_THRESHOLD,
      error: "strict_public_sanitize blocked publication",
    };
  }

  // Pass — flip ACL + bump quota in one transaction.
  const tx = db.transaction(() => {
    db.prepare(
      `
      UPDATE experiences
      SET acl = 'public',
          publish_status = 'published',
          published_at = ?
      WHERE experience_id = ?
      `
    ).run(nowIso, experienceId);
    db.prepare(
      "INSERT OR IGNORE INTO owner_quotas (owner) VALUES (?)"
    ).run(row.agent_owner);
    db.prepare(
      `
      UPDATE owner_quotas
      SET publish_count = publish_count + 1,
          last_publish_at = ?,
          updated_at = ?
      WHERE owner = ?
      `
    ).run(nowIso, nowIso, row.agent_owner);
    db.prepare(
      "INSERT INTO audit_log (actor, actor_kind, action, target_id, payload) VALUES (?, ?, ?, ?, ?)"
    ).run(
      viewer,
      "ui",
      "publish",
      experienceId,
      JSON.stringify({ owner: row.agent_owner, ts: nowIso })
    );
  });
  tx();

  const quotaRow = db
    .prepare("SELECT publish_count FROM owner_quotas WHERE owner = ?")
    .get(row.agent_owner) as { publish_count: number };
  return {
    ok: true,
    status: "published",
    experience_id: experienceId,
    blocking_hits: [],
    publish_count: quotaRow.publish_count,
    community_unlocked: quotaRow.publish_count >= COMMUNITY_THRESHOLD,
  };
}

export async function unpublishExperience(
  viewer: string,
  experienceId: string
): Promise<PublishResult> {
  const db = getDb();
  const row = loadExperience(experienceId);
  if (!row) {
    return {
      ok: false,
      status: "not_found",
      experience_id: experienceId,
      blocking_hits: [],
      error: "experience not found",
    };
  }
  const viewerOwner = ownerOf(viewer);
  if (viewerOwner !== row.agent_owner) {
    return {
      ok: false,
      status: "forbidden",
      experience_id: experienceId,
      blocking_hits: [],
      error: "you do not own this experience",
    };
  }
  if (row.publish_status !== "published") {
    return {
      ok: true,
      status: "already_private",
      experience_id: experienceId,
      blocking_hits: [],
    };
  }
  const nowIso = new Date().toISOString();
  const tx = db.transaction(() => {
    db.prepare(
      `
      UPDATE experiences
      SET acl = 'private',
          publish_status = 'private',
          published_at = NULL
      WHERE experience_id = ?
      `
    ).run(experienceId);
    db.prepare(
      `
      UPDATE owner_quotas
      SET unpublished_count = unpublished_count + 1,
          updated_at = ?
      WHERE owner = ?
      `
    ).run(nowIso, row.agent_owner);
    db.prepare(
      "INSERT INTO audit_log (actor, actor_kind, action, target_id, payload) VALUES (?, ?, ?, ?, ?)"
    ).run(
      viewer,
      "ui",
      "unpublish",
      experienceId,
      JSON.stringify({ owner: row.agent_owner, ts: nowIso })
    );
  });
  tx();

  const quotaRow = db
    .prepare("SELECT publish_count FROM owner_quotas WHERE owner = ?")
    .get(row.agent_owner) as { publish_count: number };
  return {
    ok: true,
    status: "unpublished",
    experience_id: experienceId,
    blocking_hits: [],
    publish_count: quotaRow.publish_count,
    community_unlocked: quotaRow.publish_count >= COMMUNITY_THRESHOLD,
  };
}
