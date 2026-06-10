import { getDb } from "./db";
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

export function listSkills(filters: SkillListFilters = {}): SkillListItem[] {
  const db = getDb();
  const where: string[] = [];
  const params: unknown[] = [];

  if (filters.reviewStatus && filters.reviewStatus !== "all") {
    where.push("review_status = ?");
    params.push(filters.reviewStatus);
  }
  if (filters.search && filters.search.trim().length > 0) {
    where.push("(name LIKE ? OR description LIKE ? OR skill_id LIKE ?)");
    const term = `%${filters.search.trim()}%`;
    params.push(term, term, term);
  }

  const sql = `
    SELECT * FROM skills
    ${where.length ? "WHERE " + where.join(" AND ") : ""}
    ORDER BY created_at DESC
    LIMIT ? OFFSET ?
  `;
  params.push(filters.limit ?? 100);
  params.push(filters.offset ?? 0);

  const rows = db.prepare(sql).all(...params) as SkillRow[];
  return rows.map((r) => ({ ...r, q_scalar: qScalar(r) }));
}

export function getSkill(skillId: string): SkillRow | null {
  const db = getDb();
  return (db.prepare("SELECT * FROM skills WHERE skill_id = ?").get(skillId) as SkillRow | undefined) ?? null;
}

export function listExperiencesUsingSkill(skillId: string, limit = 50): SkillUseRow[] {
  const db = getDb();
  const rows = db
    .prepare(
      `
      SELECT u.*, e.intent_text, e.task_type
      FROM experience_skill_uses u
      JOIN experiences e ON e.experience_id = u.experience_id
      WHERE u.skill_id = ?
      ORDER BY u.created_at DESC
      LIMIT ?
      `
    )
    .all(skillId, limit) as SkillUseRow[];
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

export function listSkillsUsedByExperience(experienceId: string): SkillUseRow[] {
  const db = getDb();
  return db
    .prepare(
      `
      SELECT u.*, s.name, s.version, s.description
      FROM experience_skill_uses u
      JOIN skills s ON s.skill_id = u.skill_id
      WHERE u.experience_id = ?
      ORDER BY u.created_at DESC
      `
    )
    .all(experienceId) as SkillUseRow[];
}

export function skillStats(): {
  total: number;
  byReviewStatus: Record<string, number>;
  totalInvocations: number;
  topReused: SkillListItem[];
} {
  const db = getDb();
  const total = (db.prepare("SELECT COUNT(*) AS n FROM skills").get() as { n: number }).n;
  const byStatusRows = db
    .prepare("SELECT review_status, COUNT(*) AS n FROM skills GROUP BY review_status")
    .all() as { review_status: string; n: number }[];
  const byReviewStatus: Record<string, number> = {};
  byStatusRows.forEach((r) => {
    byReviewStatus[r.review_status] = r.n;
  });
  const totalInvocations = (
    db.prepare("SELECT COALESCE(SUM(invoke_count), 0) AS n FROM skills").get() as { n: number }
  ).n;
  const topReusedRows = db
    .prepare(
      "SELECT * FROM skills WHERE invoke_count > 0 ORDER BY invoke_count DESC, q_update_count DESC LIMIT 10"
    )
    .all() as SkillRow[];
  const topReused = topReusedRows.map((r) => ({ ...r, q_scalar: qScalar(r) }));
  return { total, byReviewStatus, totalInvocations, topReused };
}
