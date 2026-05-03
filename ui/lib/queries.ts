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

// Session grouping — collapse the N segments of one upload session into a row.

export type SessionGroup = {
  session_id: string;          // either parent_session_id (segmented) or just session_id
  agent_type: string;
  segments: {
    experience_id: string;
    intent_text: string;
    seg_index: number | null;
    total_segments: number | null;
    task_type: string;
    created_at: string;
    sensitivity: string;
    review_status: string;
  }[];
  started_at: string;
  ended_at: string;
  agent_name: string | null;
};

export function listRecentSessions(limit = 20): SessionGroup[] {
  try {
    const db = getDb();
    // Pull recent experiences with their meta (which contains segment / agent_type info)
    const rows = db
      .prepare(
        `SELECT e.experience_id, e.intent_text, e.task_type, e.sensitivity,
                e.review_status, e.created_at,
                a.name AS agent_name,
                tp.body AS traj_body
         FROM experiences e
         JOIN agents a ON a.agent_id = e.agent_id
         LEFT JOIN (
            SELECT experience_id, NULL AS body FROM experiences
         ) tp ON tp.experience_id = e.experience_id
         WHERE COALESCE(e.ingest_path, 'full') = 'lite'
         ORDER BY e.created_at DESC
         LIMIT ?`,
      )
      .all(limit * 6) as Array<{
        experience_id: string;
        intent_text: string;
        task_type: string;
        sensitivity: string;
        review_status: string;
        created_at: string;
        agent_name: string | null;
        traj_body: string | null;
      }>;

    // Read each trajectory file's meta to get segment info. Fast enough for ≤120 rows.
    const fs = require("node:fs") as typeof import("node:fs");
    const path = require("node:path") as typeof import("node:path");
    const root = process.env.EXP_ROOT || "/var/lib/expool";
    const trajDir = path.join(root, "trajectories");

    const groups = new Map<string, SessionGroup>();
    for (const r of rows) {
      let segIndex: number | null = null;
      let totalSegs: number | null = null;
      let parentSessionId: string | null = null;
      let agentType = "unknown";
      try {
        const file = path.join(trajDir, `${r.experience_id}.json`);
        const text = fs.readFileSync(file, "utf-8");
        const parsed = JSON.parse(text);
        const meta = parsed.meta || {};
        agentType = meta.agent_type || "unknown";
        const seg = meta.extra?.segment;
        if (seg) {
          segIndex = seg.seg_index ?? null;
          totalSegs = seg.total_segments ?? null;
          parentSessionId = seg.parent_session_id ?? null;
        } else {
          parentSessionId = meta.session_id || null;
        }
      } catch {
        // fall through
      }
      const key = parentSessionId
        ? `${r.agent_name}::${parentSessionId}`
        : `solo::${r.experience_id}`;
      const existing = groups.get(key);
      if (existing) {
        existing.segments.push({
          experience_id: r.experience_id,
          intent_text: r.intent_text,
          seg_index: segIndex,
          total_segments: totalSegs,
          task_type: r.task_type,
          created_at: r.created_at,
          sensitivity: r.sensitivity,
          review_status: r.review_status,
        });
        if (r.created_at < existing.started_at) existing.started_at = r.created_at;
        if (r.created_at > existing.ended_at) existing.ended_at = r.created_at;
      } else {
        groups.set(key, {
          session_id: parentSessionId || r.experience_id,
          agent_type: agentType,
          agent_name: r.agent_name,
          started_at: r.created_at,
          ended_at: r.created_at,
          segments: [
            {
              experience_id: r.experience_id,
              intent_text: r.intent_text,
              seg_index: segIndex,
              total_segments: totalSegs,
              task_type: r.task_type,
              created_at: r.created_at,
              sensitivity: r.sensitivity,
              review_status: r.review_status,
            },
          ],
        });
      }
    }
    // Sort segments inside each group by seg_index (or by created_at), and groups by ended_at desc.
    const out = [...groups.values()];
    for (const g of out) {
      g.segments.sort((a, b) => {
        if (a.seg_index != null && b.seg_index != null) return a.seg_index - b.seg_index;
        return a.created_at.localeCompare(b.created_at);
      });
    }
    out.sort((a, b) => b.ended_at.localeCompare(a.ended_at));
    return out.slice(0, limit);
  } catch {
    return [];
  }
}

export function topAgentsByContribution(limit = 10): {
  agent_id: string;
  agent_name: string;
  team: string;
  experiences: number;
  last_active: string;
}[] {
  try {
    const db = getDb();
    return db
      .prepare(
        `SELECT a.agent_id, a.name AS agent_name, a.team,
                COUNT(e.experience_id) AS experiences,
                MAX(e.created_at) AS last_active
         FROM agents a
         LEFT JOIN experiences e ON e.agent_id = a.agent_id
         GROUP BY a.agent_id
         HAVING experiences > 0
         ORDER BY experiences DESC, last_active DESC
         LIMIT ?`,
      )
      .all(limit) as Array<{
        agent_id: string;
        agent_name: string;
        team: string;
        experiences: number;
        last_active: string;
      }>;
  } catch {
    return [];
  }
}

// Knowledge clusters detail (browsable)

export type ClusterRow = {
  cluster_id: string;
  label: string | null;
  member_count: number;
  new_since_crystallize: number;
  crystallized_skill_id: string | null;
  last_crystallized_at: string | null;
  last_structure_score: number | null;
  created_at: string;
  updated_at: string;
};

export type ClusterMember = {
  experience_id: string;
  similarity: number;
  added_at: string;
  intent_text: string;
  trajectory_score: number | null;
  task_type: string;
};

export function listClusters(limit = 100): ClusterRow[] {
  try {
    const db = getDb();
    return db
      .prepare(
        `SELECT cluster_id, label, member_count, new_since_crystallize,
                crystallized_skill_id, last_crystallized_at, last_structure_score,
                created_at, updated_at
         FROM knowledge_clusters
         ORDER BY member_count DESC, updated_at DESC
         LIMIT ?`,
      )
      .all(limit) as ClusterRow[];
  } catch {
    return [];
  }
}

export function getCluster(clusterId: string): ClusterRow | null {
  try {
    const db = getDb();
    const row = db
      .prepare(
        `SELECT cluster_id, label, member_count, new_since_crystallize,
                crystallized_skill_id, last_crystallized_at, last_structure_score,
                created_at, updated_at
         FROM knowledge_clusters WHERE cluster_id = ?`,
      )
      .get(clusterId) as ClusterRow | undefined;
    return row ?? null;
  } catch {
    return null;
  }
}

export function getClusterMembers(clusterId: string): ClusterMember[] {
  try {
    const db = getDb();
    return db
      .prepare(
        `SELECT m.experience_id, m.similarity, m.added_at,
                e.intent_text, e.trajectory_score, e.task_type
         FROM cluster_membership m
         JOIN experiences e ON e.experience_id = m.experience_id
         WHERE m.cluster_id = ?
         ORDER BY m.similarity DESC, m.added_at ASC`,
      )
      .all(clusterId) as ClusterMember[];
  } catch {
    return [];
  }
}

export type CrystallizedSkillFull = {
  skill_id: string;
  cluster_id: string;
  name: string;
  version: number;
  content: string;
  structure_score: number;
  member_count: number;
  created_at: string;
  superseded_by: string | null;
};

export function getCrystallizedSkillByCluster(clusterId: string): CrystallizedSkillFull | null {
  try {
    const db = getDb();
    const row = db
      .prepare(
        `SELECT skill_id, cluster_id, name, version, content,
                structure_score, member_count, created_at, superseded_by
         FROM crystallized_skills
         WHERE cluster_id = ? AND superseded_by IS NULL
         ORDER BY version DESC LIMIT 1`,
      )
      .get(clusterId) as CrystallizedSkillFull | undefined;
    return row ?? null;
  } catch {
    return null;
  }
}

export function getCrystallizedSkill(skillId: string): CrystallizedSkillFull | null {
  try {
    const db = getDb();
    const row = db
      .prepare(
        `SELECT skill_id, cluster_id, name, version, content,
                structure_score, member_count, created_at, superseded_by
         FROM crystallized_skills WHERE skill_id = ?`,
      )
      .get(skillId) as CrystallizedSkillFull | undefined;
    return row ?? null;
  } catch {
    return null;
  }
}

// Crystallized skills + cluster stats

export type CrystalStats = {
  clusters: number;
  crystallized: number;
  avg_members: number;
  max_members: number;
  skills: number;
};

export type CrystallizedSkill = {
  skill_id: string;
  cluster_id: string;
  name: string;
  version: number;
  structure_score: number;
  member_count: number;
  created_at: string;
};

export function getCrystalStats(): CrystalStats {
  try {
    const db = getDb();
    const c = db.prepare(`
      SELECT COUNT(*) AS n,
             SUM(CASE WHEN crystallized_skill_id IS NOT NULL THEN 1 ELSE 0 END) AS crystallized,
             AVG(member_count) AS avg_m,
             MAX(member_count) AS max_m
      FROM knowledge_clusters
    `).get() as { n: number; crystallized: number; avg_m: number; max_m: number };
    let s = 0;
    try {
      s = (db.prepare("SELECT COUNT(*) AS n FROM crystallized_skills").get() as { n: number }).n;
    } catch {}
    return {
      clusters: c.n || 0,
      crystallized: c.crystallized || 0,
      avg_members: Math.round((c.avg_m || 0) * 100) / 100,
      max_members: c.max_m || 0,
      skills: s,
    };
  } catch {
    return { clusters: 0, crystallized: 0, avg_members: 0, max_members: 0, skills: 0 };
  }
}

export function listCrystallizedSkills(limit = 20): CrystallizedSkill[] {
  try {
    const db = getDb();
    return db.prepare(
      `SELECT skill_id, cluster_id, name, version, structure_score,
              member_count, created_at
       FROM crystallized_skills
       WHERE superseded_by IS NULL
       ORDER BY structure_score DESC, created_at DESC
       LIMIT ?`,
    ).all(limit) as CrystallizedSkill[];
  } catch {
    return [];
  }
}

// LLM usage (auto-label accounting)

export type UsageStats = {
  total_calls: number;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_cost_usd: number;
  ok: number;
  errors: number;
  by_kind: Record<string, { calls: number; tokens: number; cost_usd: number }>;
  by_model: { model: string; calls: number; tokens: number; cost_usd: number }[];
};

export function getUsageStats(): UsageStats {
  const empty: UsageStats = {
    total_calls: 0, total_tokens: 0, prompt_tokens: 0, completion_tokens: 0,
    total_cost_usd: 0, ok: 0, errors: 0, by_kind: {}, by_model: [],
  };
  try {
    const db = getDb();
    // Ensure table exists (schema migration may not have run yet for older
    // DBs). The table is created by the API container's auto_label module.
    const row = db
      .prepare(
        `SELECT
           COUNT(*) AS n,
           COALESCE(SUM(prompt_tokens),0) AS prompt,
           COALESCE(SUM(completion_tokens),0) AS completion,
           COALESCE(SUM(total_tokens),0) AS total,
           COALESCE(SUM(cost_usd),0) AS cost,
           COALESCE(SUM(CASE WHEN ok=1 THEN 1 ELSE 0 END),0) AS ok_count,
           COALESCE(SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END),0) AS err_count
         FROM llm_usage`,
      )
      .get() as {
        n: number; prompt: number; completion: number; total: number;
        cost: number; ok_count: number; err_count: number;
      };
    const byKind: Record<string, { calls: number; tokens: number; cost_usd: number }> = {};
    const kindRows = db
      .prepare(
        `SELECT kind, COUNT(*) AS calls, SUM(total_tokens) AS tok, SUM(cost_usd) AS cost
         FROM llm_usage GROUP BY kind`,
      )
      .all() as Array<{ kind: string; calls: number; tok: number; cost: number }>;
    for (const r of kindRows) {
      byKind[r.kind] = { calls: r.calls, tokens: r.tok || 0, cost_usd: r.cost || 0 };
    }
    const byModel = db
      .prepare(
        `SELECT model, COUNT(*) AS calls, SUM(total_tokens) AS tok, SUM(cost_usd) AS cost
         FROM llm_usage GROUP BY model ORDER BY tok DESC`,
      )
      .all() as Array<{ model: string; calls: number; tok: number; cost: number }>;
    return {
      total_calls: row.n, total_tokens: row.total,
      prompt_tokens: row.prompt, completion_tokens: row.completion,
      total_cost_usd: row.cost, ok: row.ok_count, errors: row.err_count,
      by_kind: byKind,
      by_model: byModel.map((r) => ({
        model: r.model, calls: r.calls, tokens: r.tok || 0, cost_usd: r.cost || 0,
      })),
    };
  } catch {
    return empty;
  }
}

// Per-turn rewards (synergy schema).

export type TurnRewardRow = {
  experience_id: string;
  turn_index: number;
  user_turn_index: number | null;
  r_outcome: number;
  r_intent: number;
  r_execution: number;
  r_orchestration: number;
  r_expression: number;
  confidence: number;
  reason: string | null;
  judge_model: string;
  judge_backend: string;
  annotated_at: string;
  annotated_by: string;
};

export function getTurnRewards(experienceId: string, judgeModel?: string): TurnRewardRow[] {
  try {
    const db = getDb();
    if (judgeModel) {
      return db
        .prepare(
          `SELECT * FROM turn_rewards WHERE experience_id = ? AND judge_model = ?
           ORDER BY turn_index ASC`,
        )
        .all(experienceId, judgeModel) as TurnRewardRow[];
    }
    return db
      .prepare("SELECT * FROM turn_rewards WHERE experience_id = ? ORDER BY turn_index ASC")
      .all(experienceId) as TurnRewardRow[];
  } catch {
    // turn_rewards table may not exist on older deployments
    return [];
  }
}

export function listExperiencesWithRewards(limit = 50): {
  experience_id: string;
  intent_text: string;
  task_type: string;
  agent_id: string;
  created_at: string;
  reward_count: number;
  judge_models: string;
  trajectory_score: number | null;
}[] {
  try {
    const db = getDb();
    return db
      .prepare(
        `SELECT
           e.experience_id, e.intent_text, e.task_type, e.agent_id, e.created_at,
           COUNT(*) AS reward_count,
           GROUP_CONCAT(DISTINCT tr.judge_model) AS judge_models,
           ROUND(AVG(tr.r_outcome) * 0.35
                + AVG(tr.r_intent) * 0.20
                + AVG(tr.r_execution) * 0.20
                + AVG(tr.r_orchestration) * 0.10
                + AVG(tr.r_expression) * 0.15, 3) AS trajectory_score
         FROM turn_rewards tr
         JOIN experiences e ON e.experience_id = tr.experience_id
         GROUP BY tr.experience_id
         ORDER BY MAX(tr.annotated_at) DESC
         LIMIT ?`,
      )
      .all(limit) as Array<{
        experience_id: string;
        intent_text: string;
        task_type: string;
        agent_id: string;
        created_at: string;
        reward_count: number;
        judge_models: string;
        trajectory_score: number | null;
      }>;
  } catch {
    return [];
  }
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
