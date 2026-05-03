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
  options: { includeRevoked?: boolean; limit?: number } = {}
): Promise<MyExperienceRow[]> {
  const db = getDb();
  const includeRevoked = options.includeRevoked ?? false;
  const limit = Math.max(1, Math.min(options.limit ?? 200, 1000));

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
        (
          SELECT json_extract(payload, '$.redactions')
          FROM audit_log
          WHERE target_id = e.experience_id AND action = 'push_lite'
          ORDER BY rowid DESC LIMIT 1
        ) AS redactions_summary
      FROM experiences e
      JOIN agents a USING(agent_id)
      WHERE a.name = ?
        ${includeRevoked ? "" : "AND COALESCE(e.revoked, 0) = 0"}
      ORDER BY e.created_at DESC
      LIMIT ?
      `
    )
    .all(viewerName, limit) as MyExperienceRow[];
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
