import { getDb } from "./db";
import { getCurrentUser } from "./auth";
import { qScalar, type SkillRow, type SkillUseRow, type SkillQUpdateRow } from "./types";

export interface SkillListItem extends SkillRow {
  q_scalar: number;
}

export interface SkillListFilters {
  reviewStatus?: string;
  search?: string;
  limit?: number;
  offset?: number;
}

async function skillVisibility(skillAlias = "s", agentAlias = "a") {
  const me = await getCurrentUser();
  const conditions = [`COALESCE(${skillAlias}.acl, 'private') IN ('public', 'org')`];
  const params: unknown[] = [];
  if (me) {
    const viewer = getDb()
      .prepare("SELECT agent_id, team, COALESCE(owner, name) AS owner FROM agents WHERE name = ?")
      .get(me.default_agent_name) as
      | { agent_id: string; team: string; owner: string }
      | undefined;
    if (viewer) {
      conditions.push(
        `${agentAlias}.agent_id = ?`,
        `COALESCE(${agentAlias}.owner, ${agentAlias}.name) = ?`,
      );
      params.push(viewer.agent_id, viewer.owner);
      if (viewer.team) {
        conditions.push(`${skillAlias}.acl = ?`);
        params.push(`team:${viewer.team}`);
      }
    }
  }
  return { sql: `(${conditions.join(" OR ")})`, params };
}

export async function listSkills(filters: SkillListFilters = {}): Promise<SkillListItem[]> {
  const db = getDb();
  const where: string[] = [];
  const params: unknown[] = [];

  if (filters.reviewStatus && filters.reviewStatus !== "all") {
    where.push("s.review_status = ?");
    params.push(filters.reviewStatus);
  }
  if (filters.search && filters.search.trim().length > 0) {
    where.push("(s.name LIKE ? OR s.description LIKE ? OR s.skill_id LIKE ?)");
    const term = `%${filters.search.trim()}%`;
    params.push(term, term, term);
  }

  const visibility = await skillVisibility();
  where.push(visibility.sql);
  params.push(...visibility.params);
  const sql = `
    SELECT s.* FROM skills s
    JOIN agents a ON a.agent_id = s.agent_id
    WHERE ${where.join(" AND ")}
    ORDER BY s.created_at DESC
    LIMIT ? OFFSET ?
  `;
  params.push(filters.limit ?? 100);
  params.push(filters.offset ?? 0);

  const rows = db.prepare(sql).all(...params) as SkillRow[];
  return rows.map((r) => ({ ...r, q_scalar: qScalar(r) }));
}

export async function getSkill(skillId: string): Promise<SkillRow | null> {
  const db = getDb();
  const visibility = await skillVisibility();
  return (
    (db
      .prepare(
        `SELECT s.* FROM skills s
         JOIN agents a ON a.agent_id = s.agent_id
         WHERE s.skill_id = ? AND ${visibility.sql}`,
      )
      .get(skillId, ...visibility.params) as SkillRow | undefined) ?? null
  );
}

export async function listExperiencesUsingSkill(skillId: string, limit = 50): Promise<SkillUseRow[]> {
  const db = getDb();
  const me = await getCurrentUser();
  const conditions = [
    "(e.acl = 'public' AND COALESCE(e.publish_status, 'private') = 'published')",
  ];
  const params: unknown[] = [skillId];
  if (me) {
    conditions.push("ea.owner = ?", "ea.name = ?");
    params.push(me.email, me.default_agent_name);
  }
  const rows = db
    .prepare(
      `
      SELECT u.*, e.intent_text, e.task_type
      FROM experience_skill_uses u
      JOIN experiences e ON e.experience_id = u.experience_id
      JOIN agents ea ON ea.agent_id = e.agent_id
      WHERE u.skill_id = ?
        AND (${conditions.join(" OR ")})
      ORDER BY u.created_at DESC
      LIMIT ?
      `
    )
    .all(...params, limit) as SkillUseRow[];
  return rows;
}

export function listSkillQUpdates(skillId: string, limit = 50): SkillQUpdateRow[] {
  const db = getDb();
  return db
    .prepare(
      "SELECT * FROM skill_q_updates WHERE skill_id = ? ORDER BY created_at DESC LIMIT ?"
    )
    .all(skillId, limit) as SkillQUpdateRow[];
}

export async function listSkillsUsedByExperience(experienceId: string): Promise<SkillUseRow[]> {
  const db = getDb();
  const visibility = await skillVisibility();
  return db
    .prepare(
      `
      SELECT u.*, s.name, s.version, s.description
      FROM experience_skill_uses u
      JOIN skills s ON s.skill_id = u.skill_id
      JOIN agents a ON a.agent_id = s.agent_id
      WHERE u.experience_id = ?
        AND ${visibility.sql}
      ORDER BY u.created_at DESC
      `
    )
    .all(experienceId, ...visibility.params) as SkillUseRow[];
}

export async function skillStats(): Promise<{
  total: number;
  byReviewStatus: Record<string, number>;
  totalInvocations: number;
  topReused: SkillListItem[];
}> {
  const db = getDb();
  const visibility = await skillVisibility();
  const total = (
    db
      .prepare(
        `SELECT COUNT(*) AS n FROM skills s JOIN agents a ON a.agent_id = s.agent_id
         WHERE ${visibility.sql}`,
      )
      .get(...visibility.params) as { n: number }
  ).n;
  const byStatusRows = db
    .prepare(
      `SELECT s.review_status, COUNT(*) AS n
       FROM skills s JOIN agents a ON a.agent_id = s.agent_id
       WHERE ${visibility.sql} GROUP BY s.review_status`,
    )
    .all(...visibility.params) as { review_status: string; n: number }[];
  const byReviewStatus: Record<string, number> = {};
  byStatusRows.forEach((r) => {
    byReviewStatus[r.review_status] = r.n;
  });
  const totalInvocations = (
    db
      .prepare(
        `SELECT COALESCE(SUM(s.invoke_count), 0) AS n
         FROM skills s JOIN agents a ON a.agent_id = s.agent_id WHERE ${visibility.sql}`,
      )
      .get(...visibility.params) as { n: number }
  ).n;
  const topReusedRows = db
    .prepare(
      `SELECT s.* FROM skills s JOIN agents a ON a.agent_id = s.agent_id
       WHERE s.invoke_count > 0 AND ${visibility.sql}
       ORDER BY s.invoke_count DESC, s.q_update_count DESC LIMIT 10`
    )
    .all(...visibility.params) as SkillRow[];
  const topReused = topReusedRows.map((r) => ({ ...r, q_scalar: qScalar(r) }));
  return { total, byReviewStatus, totalInvocations, topReused };
}
