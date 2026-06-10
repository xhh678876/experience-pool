"use server";

import { getDb } from "./db";

export interface CommunityRow {
  experience_id: string;
  task_type: string;
  source_model: string;
  query: string | null;
  intent_text: string | null;
  outcome: string | null;
  acl: string;
  trajectory_score: number | null;
  is_memory_eligible: number | null;
  created_at: string;
  published_at: string | null;
  agent_owner: string;  // who contributed
}

/**
 * List published rows in the community pool. Anyone can call this; the
 * UI page is the one that gates display behind the quota.
 */
export async function listCommunityExperiences(
  limit: number = 200
): Promise<CommunityRow[]> {
  const db = getDb();
  return db
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
        e.trajectory_score,
        e.is_memory_eligible,
        e.created_at,
        e.published_at,
        COALESCE(a.owner, a.name) AS agent_owner
      FROM experiences e JOIN agents a USING(agent_id)
      WHERE COALESCE(e.publish_status, 'private') = 'published'
        AND e.acl = 'public'
        AND COALESCE(e.revoked, 0) = 0
      ORDER BY e.published_at DESC, e.created_at DESC
      LIMIT ?
      `
    )
    .all(limit) as CommunityRow[];
}

export async function communityStats(): Promise<{
  total_published: number;
  contributors: number;
  recent_7d: number;
}> {
  const db = getDb();
  const total =
    (db
      .prepare(
        `SELECT COUNT(*) AS n FROM experiences
         WHERE COALESCE(publish_status, 'private') = 'published'
           AND acl = 'public'
           AND COALESCE(revoked, 0) = 0`
      )
      .get() as { n: number }).n ?? 0;
  const contributors =
    (db
      .prepare(
        `SELECT COUNT(DISTINCT COALESCE(a.owner, a.name)) AS n
         FROM experiences e JOIN agents a USING(agent_id)
         WHERE COALESCE(e.publish_status, 'private') = 'published'
           AND e.acl = 'public'
           AND COALESCE(e.revoked, 0) = 0`
      )
      .get() as { n: number }).n ?? 0;
  const recent =
    (db
      .prepare(
        `SELECT COUNT(*) AS n FROM experiences
         WHERE COALESCE(publish_status, 'private') = 'published'
           AND acl = 'public'
           AND COALESCE(revoked, 0) = 0
           AND published_at >= datetime('now', '-7 days')`
      )
      .get() as { n: number }).n ?? 0;
  return { total_published: total, contributors, recent_7d: recent };
}
