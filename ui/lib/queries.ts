import { getDb } from "./db";
import type {
  AuditRow,
  EdgeRow,
  ExperienceListItem,
  ExperienceRow,
  QUpdateRow,
  RewardRow,
} from "./types";
import { qScalar } from "./types";

export type ListFilters = {
  reviewStatus?: string;
  taskType?: string;
  sensitivity?: string;
  search?: string;
  limit?: number;
  offset?: number;
};

export function listExperiences(filters: ListFilters = {}): ExperienceListItem[] {
  const db = getDb();
  const where: string[] = [];
  const params: unknown[] = [];

  if (filters.reviewStatus && filters.reviewStatus !== "all") {
    where.push("review_status = ?");
    params.push(filters.reviewStatus);
  }
  if (filters.taskType && filters.taskType !== "all") {
    where.push("task_type = ?");
    params.push(filters.taskType);
  }
  if (filters.sensitivity && filters.sensitivity !== "all") {
    where.push("sensitivity = ?");
    params.push(filters.sensitivity);
  }
  if (filters.search && filters.search.trim().length > 0) {
    where.push("(intent_text LIKE ? OR experience_id LIKE ?)");
    const term = `%${filters.search.trim()}%`;
    params.push(term, term);
  }

  const sql = `
    SELECT * FROM experiences
    ${where.length ? "WHERE " + where.join(" AND ") : ""}
    ORDER BY created_at DESC
    LIMIT ? OFFSET ?
  `;
  params.push(filters.limit ?? 100);
  params.push(filters.offset ?? 0);

  const rows = db.prepare(sql).all(...params) as ExperienceRow[];
  return rows.map((r) => ({ ...r, q_scalar: qScalar(r) }));
}

export function distinctValues(column: "task_type" | "sensitivity" | "review_status"): string[] {
  const db = getDb();
  const rows = db
    .prepare(`SELECT DISTINCT ${column} AS v FROM experiences ORDER BY ${column} ASC`)
    .all() as { v: string }[];
  return rows.map((r) => r.v).filter((v) => v != null);
}

export function getExperience(id: string): ExperienceListItem | null {
  const db = getDb();
  const row = db
    .prepare("SELECT * FROM experiences WHERE experience_id = ?")
    .get(id) as ExperienceRow | undefined;
  if (!row) return null;
  return { ...row, q_scalar: qScalar(row) };
}

export function getLatestReward(id: string): RewardRow | null {
  const db = getDb();
  const row = db
    .prepare(
      "SELECT * FROM rewards WHERE experience_id = ? ORDER BY created_at DESC LIMIT 1",
    )
    .get(id) as RewardRow | undefined;
  return row ?? null;
}

export function getQUpdates(id: string): QUpdateRow[] {
  const db = getDb();
  return db
    .prepare(
      "SELECT * FROM q_updates WHERE experience_id = ? ORDER BY created_at ASC",
    )
    .all(id) as QUpdateRow[];
}

export function getParents(id: string): EdgeRow[] {
  const db = getDb();
  return db
    .prepare("SELECT * FROM experience_edges WHERE child_id = ?")
    .all(id) as EdgeRow[];
}

export function getChildren(id: string): EdgeRow[] {
  const db = getDb();
  return db
    .prepare("SELECT * FROM experience_edges WHERE parent_id = ?")
    .all(id) as EdgeRow[];
}

export function getExperiencesByIds(ids: string[]): ExperienceListItem[] {
  if (ids.length === 0) return [];
  const db = getDb();
  const placeholders = ids.map(() => "?").join(",");
  const rows = db
    .prepare(`SELECT * FROM experiences WHERE experience_id IN (${placeholders})`)
    .all(...ids) as ExperienceRow[];
  return rows.map((r) => ({ ...r, q_scalar: qScalar(r) }));
}

export function getAuditsForTarget(id: string): AuditRow[] {
  const db = getDb();
  return db
    .prepare(
      "SELECT * FROM audit_log WHERE target_id = ? ORDER BY created_at DESC, audit_id DESC",
    )
    .all(id) as AuditRow[];
}

// Dashboard helpers.

export type DashboardStats = {
  total: number;
  pending: number;
  approved: number;
  rejected: number;
  edited: number;
  byTaskType: { task_type: string; count: number }[];
  bySensitivity: { sensitivity: string; count: number }[];
  last7Days: { day: string; count: number }[];
  qDistribution: { bucket: number; count: number }[];
  topReused: ExperienceListItem[];
};

export function getDashboardStats(): DashboardStats {
  const db = getDb();
  const total = (db.prepare("SELECT COUNT(*) AS c FROM experiences").get() as { c: number }).c;

  const statusRows = db
    .prepare("SELECT review_status, COUNT(*) AS c FROM experiences GROUP BY review_status")
    .all() as { review_status: string; c: number }[];
  const statusMap = Object.fromEntries(statusRows.map((r) => [r.review_status, r.c]));

  const byTaskType = db
    .prepare(
      "SELECT task_type, COUNT(*) AS count FROM experiences GROUP BY task_type ORDER BY count DESC",
    )
    .all() as { task_type: string; count: number }[];

  const bySensitivity = db
    .prepare(
      "SELECT sensitivity, COUNT(*) AS count FROM experiences GROUP BY sensitivity ORDER BY count DESC",
    )
    .all() as { sensitivity: string; count: number }[];

  const last7DaysRaw = db
    .prepare(
      `SELECT date(created_at) AS day, COUNT(*) AS count
       FROM experiences
       WHERE date(created_at) >= date('now', '-6 days')
       GROUP BY date(created_at)
       ORDER BY day ASC`,
    )
    .all() as { day: string; count: number }[];

  // Fill missing days with 0 so the chart is contiguous.
  const last7Days: { day: string; count: number }[] = [];
  const today = new Date();
  for (let i = 6; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    const found = last7DaysRaw.find((r) => r.day === key);
    last7Days.push({ day: key, count: found ? found.count : 0 });
  }

  // Q distribution: buckets in steps of 0.2 from -1 to 1.
  const expRows = db
    .prepare(
      "SELECT q_outcome, q_intent, q_execution, q_orchestration, q_expression FROM experiences",
    )
    .all() as Pick<
      ExperienceRow,
      "q_outcome" | "q_intent" | "q_execution" | "q_orchestration" | "q_expression"
    >[];
  const buckets = new Map<number, number>();
  for (let b = -1; b <= 0.81; b += 0.2) {
    buckets.set(Number(b.toFixed(1)), 0);
  }
  for (const r of expRows) {
    const q = qScalar(r);
    const clamped = Math.max(-1, Math.min(0.99, q));
    const bucket = Number((Math.floor((clamped + 1) / 0.2) * 0.2 - 1).toFixed(1));
    buckets.set(bucket, (buckets.get(bucket) ?? 0) + 1);
  }
  const qDistribution = [...buckets.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([bucket, count]) => ({ bucket, count }));

  const topReused = (db
    .prepare("SELECT * FROM experiences ORDER BY reuse_count DESC, created_at DESC LIMIT 10")
    .all() as ExperienceRow[]).map((r) => ({ ...r, q_scalar: qScalar(r) }));

  return {
    total,
    pending: statusMap["pending"] ?? 0,
    approved: statusMap["approved"] ?? 0,
    rejected: statusMap["rejected"] ?? 0,
    edited: statusMap["edited"] ?? 0,
    byTaskType,
    bySensitivity,
    last7Days,
    qDistribution,
    topReused,
  };
}
