"use server";

import fs from "node:fs";
import path from "node:path";
import { getDb } from "./db";

export interface MyExperienceRow {
  experience_id: string;
  task_type: string;
  source_model: string;
  query: string | null;
  intent_text: string | null;
  outcome: string | null;
  acl: string;
  sensitivity: string;
  review_status: string;
  ingest_path: string | null;
  trajectory_path: string | null;
  trajectory_score: number | null;
  is_memory_eligible: number | null;
  created_at: string;
  redactions_summary: string | null;
  revoked: number | null;
  revoked_at: string | null;
  publish_status: string | null;
  published_at: string | null;
  strict_redactions: string | null;
}

export interface OwnerQuota {
  owner: string;
  publish_count: number;
  threshold: number;
  community_unlocked: boolean;
  hint: string;
}

export interface MyExperienceStats {
  total: number;
  live: number;
  revoked: number;
  published: number;
}

const COMMUNITY_THRESHOLD = 3;

function resolveOwner(db: ReturnType<typeof getDb>, viewerName: string): string {
  return (
    (db
      .prepare("SELECT COALESCE(owner, name) AS owner FROM agents WHERE name = ?")
      .get(viewerName) as { owner: string } | undefined)?.owner ?? viewerName
  );
}

export async function getOwnerQuota(viewerName: string): Promise<OwnerQuota> {
  const db = getDb();
  const owner = resolveOwner(db, viewerName);

  // Lazy-create quota row. The FastAPI server (port 8081) writes the same
  // pool.db; if it holds the writer lock, our INSERT can SQLITE_BUSY-fail.
  // Treat that as benign: the row will get created server-side at first
  // publish anyway, and the SELECT below will return undefined → 0 quota.
  try {
    db.prepare("INSERT OR IGNORE INTO owner_quotas (owner) VALUES (?)").run(owner);
  } catch (e: unknown) {
    const code = (e as { code?: string } | null)?.code;
    if (code !== "SQLITE_BUSY" && code !== "SQLITE_LOCKED") throw e;
    // else: skip — read-only fallback
  }
  const row = db
    .prepare(
      "SELECT publish_count FROM owner_quotas WHERE owner = ?"
    )
    .get(owner) as { publish_count: number } | undefined;
  const publish_count = row?.publish_count ?? 0;
  const unlocked = publish_count >= COMMUNITY_THRESHOLD;
  return {
    owner,
    publish_count,
    threshold: COMMUNITY_THRESHOLD,
    community_unlocked: unlocked,
    hint: unlocked
      ? "社区池已解锁"
      : `再发布 ${COMMUNITY_THRESHOLD - publish_count} 条经验解锁社区池`,
  };
}

export async function getMyExperienceStats(
  viewerName: string
): Promise<MyExperienceStats> {
  const db = getDb();
  const owner = resolveOwner(db, viewerName);
  const row = db
    .prepare(
      `
      SELECT
        COUNT(*) AS total,
        SUM(CASE WHEN COALESCE(e.revoked, 0) = 0 THEN 1 ELSE 0 END) AS live,
        SUM(CASE WHEN COALESCE(e.revoked, 0) = 1 THEN 1 ELSE 0 END) AS revoked,
        SUM(CASE
          WHEN COALESCE(e.revoked, 0) = 0
           AND COALESCE(e.publish_status, 'private') = 'published'
          THEN 1 ELSE 0 END) AS published
      FROM experiences e
      JOIN agents a USING(agent_id)
      WHERE (a.owner = ? OR (a.owner IS NULL AND a.name = ?))
      `
    )
    .get(owner, owner) as
    | {
        total: number | null;
        live: number | null;
        revoked: number | null;
        published: number | null;
      }
    | undefined;
  return {
    total: row?.total ?? 0,
    live: row?.live ?? 0,
    revoked: row?.revoked ?? 0,
    published: row?.published ?? 0,
  };
}

export interface RevokeResult {
  ok: boolean;
  experience_id: string;
  status: "revoked" | "already_revoked" | "not_found" | "forbidden";
  error?: string;
  deleted_files: string[];
}

/**
 * List experiences owned by the given viewer (agent name). Optionally
 * include revoked rows for transparency.
 */
export async function listMyExperiences(
  viewerName: string,
  options: { includeRevoked?: boolean; limit?: number; offset?: number } = {}
): Promise<MyExperienceRow[]> {
  const db = getDb();
  const includeRevoked = options.includeRevoked ?? false;
  const limit = Math.max(1, Math.min(options.limit ?? 200, 1000));
  const offset = Math.max(0, options.offset ?? 0);

  // Multi-agent personal pool: list every experience whose agent.owner
  // matches the viewer's owner.
  const owner = resolveOwner(db, viewerName);

  const rows = db
    .prepare(
      `
      SELECT
        e.experience_id,
        e.task_type,
        e.source_model,
        e.query,
        e.intent_text,
        e.outcome,
        e.acl,
        e.sensitivity,
        e.review_status,
        e.ingest_path,
        e.trajectory_path,
        e.trajectory_score,
        e.is_memory_eligible,
        e.created_at,
        COALESCE(e.revoked, 0) AS revoked,
        e.revoked_at,
        COALESCE(e.publish_status, 'private') AS publish_status,
        e.published_at,
        e.strict_redactions,
        (
          SELECT json_extract(payload, '$.redactions')
          FROM audit_log
          WHERE target_id = e.experience_id AND action = 'push_lite'
          ORDER BY rowid DESC LIMIT 1
        ) AS redactions_summary
      FROM experiences e
      JOIN agents a USING(agent_id)
      WHERE (a.owner = ? OR (a.owner IS NULL AND a.name = ?))
        ${includeRevoked ? "" : "AND COALESCE(e.revoked, 0) = 0"}
      ORDER BY e.created_at DESC
      LIMIT ? OFFSET ?
      `
    )
    .all(owner, owner, limit, offset) as MyExperienceRow[];
  return rows;
}

/**
 * Revoke (right-to-be-forgotten) an experience.
 *
 * Mirrors the server-side /v1/lite/revoke flow exactly: marks the row
 * revoked=1, hard-deletes the trajectory sidecar file, drops vector +
 * cluster_membership + turn_rewards rows, appends an audit_log entry.
 *
 * Ownership is verified — viewers can only revoke their own rows.
 */
export async function revokeExperience(
  viewerName: string,
  experienceId: string,
  reason: string = "user_request"
): Promise<RevokeResult> {
  const db = getDb();
  const row = db
    .prepare(
      `
      SELECT e.experience_id, e.agent_id, e.trajectory_path,
             COALESCE(e.revoked, 0) AS revoked, a.name AS agent_name
      FROM experiences e JOIN agents a USING(agent_id)
      WHERE e.experience_id = ?
      `
    )
    .get(experienceId) as
    | {
        experience_id: string;
        agent_id: string;
        trajectory_path: string | null;
        revoked: number;
        agent_name: string;
      }
    | undefined;

  if (!row) {
    return {
      ok: false,
      experience_id: experienceId,
      status: "not_found",
      error: `experience not found: ${experienceId}`,
      deleted_files: [],
    };
  }
  if (row.agent_name !== viewerName) {
    return {
      ok: false,
      experience_id: experienceId,
      status: "forbidden",
      error: `viewer "${viewerName}" does not own experience (owner: ${row.agent_name})`,
      deleted_files: [],
    };
  }
  if (row.revoked) {
    return {
      ok: true,
      experience_id: experienceId,
      status: "already_revoked",
      deleted_files: [],
    };
  }

  // 1. Hard-delete the trajectory file.
  const deleted: string[] = [];
  if (row.trajectory_path) {
    const trustedRoot = process.env.EXP_TRAJECTORIES_DIR ||
      path.join(process.env.HOME || "/", ".experience-pool", "trajectories");
    const resolved = path.resolve(row.trajectory_path);
    // Defense-in-depth: only delete files inside the configured trajectory
    // directory, even if the DB row claims an absolute path elsewhere.
    if (resolved.startsWith(path.resolve(trustedRoot)) && fs.existsSync(resolved)) {
      try {
        fs.unlinkSync(resolved);
        deleted.push(resolved);
      } catch {
        // Best-effort. The DB row still gets marked revoked even if
        // unlink fails (e.g. read-only volume) — the audit_log entry
        // captures the failure for operator follow-up.
      }
    }
  }

  // 2. Mark revoked + drop dependent rows (transactional).
  const nowIso = new Date().toISOString();
  const tx = db.transaction(() => {
    db.prepare(
      `
      UPDATE experiences
      SET revoked = 1,
          revoked_at = ?,
          revoke_reason = ?,
          review_status = 'revoked',
          trajectory_path = NULL
      WHERE experience_id = ?
      `
    ).run(nowIso, reason.slice(0, 200), experienceId);
    db.prepare("DELETE FROM vectors WHERE experience_id = ?").run(experienceId);
    try {
      db.prepare(
        "DELETE FROM cluster_membership WHERE experience_id = ?"
      ).run(experienceId);
    } catch {
      // table may not exist on older deployments
    }
    try {
      db.prepare(
        "DELETE FROM turn_rewards WHERE experience_id = ?"
      ).run(experienceId);
    } catch {
      // ditto
    }
    db.prepare(
      `
      INSERT INTO audit_log (actor, actor_kind, action, target_id, payload)
      VALUES (?, ?, ?, ?, ?)
      `
    ).run(
      viewerName,
      "ui",
      "revoke",
      experienceId,
      JSON.stringify({ reason: reason.slice(0, 200), deleted_files: deleted, ts: nowIso })
    );
  });
  tx();

  return {
    ok: true,
    experience_id: experienceId,
    status: "revoked",
    deleted_files: deleted,
  };
}
