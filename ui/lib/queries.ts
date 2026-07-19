import { getDb } from "./db";
import { aclVisibilityClause, aclVisibilityForDetail } from "./acl-filter";
import { getCurrentUser } from "./auth";
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

function tableExists(db: ReturnType<typeof getDb>, table: string): boolean {
  const row = db
    .prepare("SELECT 1 AS present FROM sqlite_master WHERE type = 'table' AND name = ?")
    .get(table) as { present: number } | undefined;
  return Boolean(row);
}

export function canManageExperienceAs(viewerName: string, experienceId: string): boolean {
  if (!viewerName || !experienceId) return false;
  const row = getDb()
    .prepare(
      `SELECT
         COALESCE(target.owner, target.name) AS target_owner,
         COALESCE(viewer.owner, viewer.name) AS viewer_owner
       FROM experiences e
       JOIN agents target ON target.agent_id = e.agent_id
       JOIN agents viewer ON viewer.name = ?
       WHERE e.experience_id = ?`,
    )
    .get(viewerName, experienceId) as
    | { target_owner: string; viewer_owner: string }
    | undefined;
  return Boolean(row && row.target_owner === row.viewer_owner);
}

export async function canManageExperience(experienceId: string): Promise<boolean> {
  const me = await getCurrentUser();
  return me ? canManageExperienceAs(me.default_agent_name, experienceId) : false;
}

async function rewardVisibilityClause(eventAlias?: string) {
  const me = await getCurrentUser();
  const conds = [
    "(e.acl = 'public' AND COALESCE(e.publish_status, 'private') = 'published')",
  ];
  const params: unknown[] = [];
  if (me) {
    conds.push("a.owner = ?");
    params.push(me.email);
    conds.push("a.name = ?");
    params.push(me.default_agent_name);
    if (eventAlias) {
      conds.push(`${eventAlias}.viewer_name = ?`);
      params.push(me.default_agent_name);
    }
  }
  return {
    sql: `(${conds.join(" OR ")})`,
    params,
  };
}

export async function listExperiences(filters: ListFilters = {}): Promise<ExperienceListItem[]> {
  const db = getDb();
  const where: string[] = [];
  const params: unknown[] = [];

  if (filters.reviewStatus && filters.reviewStatus !== "all") {
    where.push("e.review_status = ?");
    params.push(filters.reviewStatus);
  }
  if (filters.taskType && filters.taskType !== "all") {
    where.push("e.task_type = ?");
    params.push(filters.taskType);
  }
  if (filters.sensitivity && filters.sensitivity !== "all") {
    where.push("e.sensitivity = ?");
    params.push(filters.sensitivity);
  }
  if (filters.search && filters.search.trim().length > 0) {
    where.push("(e.intent_text LIKE ? OR e.experience_id LIKE ?)");
    const term = `%${filters.search.trim()}%`;
    params.push(term, term);
  }

  // ACL: global browse pages only show shared/community-visible rows.
  const acl = await aclVisibilityClause();
  where.push(acl.sql);
  params.push(...acl.params);
  // Don't list revoked rows in the global browse.
  where.push("COALESCE(e.revoked, 0) = 0");

  const sql = `
    SELECT e.* FROM experiences e
    LEFT JOIN agents a ON a.agent_id = e.agent_id
    WHERE ${where.join(" AND ")}
    ORDER BY e.created_at DESC
    LIMIT ? OFFSET ?
  `;
  params.push(filters.limit ?? 100);
  params.push(filters.offset ?? 0);

  const rows = db.prepare(sql).all(...params) as ExperienceRow[];
  return rows.map((r) => ({ ...r, q_scalar: qScalar(r) }));
}

export function distinctValues(
  column: "task_type" | "sensitivity" | "review_status",
  viewerName?: string | null,
): string[] {
  const db = getDb();
  const viewer = viewerName
    ? (db
        .prepare("SELECT agent_id, name, team, COALESCE(owner, name) AS owner FROM agents WHERE name = ?")
        .get(viewerName) as
        | { agent_id: string; name: string; team: string; owner: string }
        | undefined)
    : undefined;
  const conditions = [
    "(e.acl = 'public' AND COALESCE(e.publish_status, 'private') = 'published')",
  ];
  const params: unknown[] = [];
  if (viewer) {
    conditions.push("a.agent_id = ?", "a.owner = ?", "(a.owner IS NULL AND a.name = ?)");
    params.push(viewer.agent_id, viewer.owner, viewer.owner);
    if (viewer.team) {
      conditions.push("e.acl = ?");
      params.push(`team:${viewer.team}`);
    }
  }
  const rows = db
    .prepare(
      `SELECT DISTINCT e.${column} AS v
       FROM experiences e
       LEFT JOIN agents a ON a.agent_id = e.agent_id
       WHERE COALESCE(e.revoked, 0) = 0
         AND (${conditions.join(" OR ")})
       ORDER BY e.${column} ASC`,
    )
    .all(...params) as { v: string }[];
  return rows.map((r) => r.v).filter((v) => v != null);
}

export async function getExperience(id: string): Promise<ExperienceListItem | null> {
  const db = getDb();
  // Detail-page ACL: public OR owner's own. (Listing pages use the
  // stricter `aclVisibilityClause()` which hides private from everyone
  // including the owner; click-throughs from /me must work though.)
  const acl = await aclVisibilityForDetail();
  const row = db
    .prepare(
      `SELECT e.* FROM experiences e
       LEFT JOIN agents a ON a.agent_id = e.agent_id
       WHERE e.experience_id = ? AND ${acl.sql}`,
    )
    .get(id, ...acl.params) as ExperienceRow | undefined;
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
    turn_count: number;
    source_byte_start: number | null;
    source_byte_end: number | null;
    task_status: string | null;
  }[];
  started_at: string;
  ended_at: string;
  agent_name: string | null;
};

export type RecentSessionScope = "public" | "personal";

function resolveAgentOwner(db: ReturnType<typeof getDb>, viewerName: string): string {
  return (
    (db
      .prepare("SELECT COALESCE(owner, name) AS owner FROM agents WHERE name = ?")
      .get(viewerName) as { owner: string } | undefined)?.owner ?? viewerName
  );
}

export async function listRecentSessions(
  limit = 20,
  options: { scope?: RecentSessionScope; viewerName?: string | null } = {},
): Promise<SessionGroup[]> {
  try {
    const db = getDb();
    const wantsPersonal = options.scope === "personal" && Boolean(options.viewerName);
    const personalOwner = wantsPersonal
      ? resolveAgentOwner(db, options.viewerName as string)
      : null;
    const visibility = wantsPersonal
      ? {
          sql: "(a.owner = ? OR a.name = ? OR (a.owner IS NULL AND a.name = ?))",
          params: [personalOwner, options.viewerName, personalOwner],
        }
      : await aclVisibilityClause();
    // Pick recent parent sessions first, then return all of their child task
    // segments. Reading provenance from SQL avoids opening up to 120 JSON
    // sidecars on every homepage request and keeps long Codex rollouts grouped.
    const rows = db
      .prepare(
        `WITH visible AS (
           SELECT e.experience_id, e.agent_id, e.intent_text, e.task_type,
                  e.sensitivity, e.review_status, e.created_at,
                  COALESCE(e.turn_count, 0) AS turn_count,
                  e.source_byte_start, e.source_byte_end, e.task_status,
                  COALESCE(NULLIF(e.parent_session_id, ''), NULLIF(e.session_id, ''), e.experience_id)
                    AS group_session_id,
                  COALESCE(
                    NULLIF(e.source_agent_type, ''),
                    CASE
                      WHEN e.task_type LIKE 'claude-code%' THEN 'claude-code'
                      WHEN e.task_type LIKE 'codex%' THEN 'codex'
                      ELSE 'unknown'
                    END
                  ) AS agent_type,
                  a.name AS agent_name
           FROM experiences e
           JOIN agents a ON a.agent_id = e.agent_id
           WHERE COALESCE(e.ingest_path, 'full') = 'lite'
             AND COALESCE(e.revoked, 0) = 0
             AND ${visibility.sql}
         ), recent_groups AS (
           SELECT agent_id, group_session_id, MAX(created_at) AS ended_at
           FROM visible
           GROUP BY agent_id, group_session_id
           ORDER BY ended_at DESC
           LIMIT ?
         )
         SELECT v.*
         FROM visible v
         JOIN recent_groups g
           ON g.agent_id = v.agent_id AND g.group_session_id = v.group_session_id
         ORDER BY g.ended_at DESC,
                  COALESCE(v.source_byte_start, 9223372036854775807),
                  v.created_at ASC`,
      )
      .all(...visibility.params, limit) as Array<{
        experience_id: string;
        intent_text: string;
        task_type: string;
        sensitivity: string;
        review_status: string;
        created_at: string;
        agent_name: string | null;
        group_session_id: string;
        agent_type: string;
        turn_count: number;
        source_byte_start: number | null;
        source_byte_end: number | null;
        task_status: string | null;
      }>;

    const groups = new Map<string, SessionGroup>();
    for (const r of rows) {
      const key = `${r.agent_name}::${r.group_session_id}`;
      const existing = groups.get(key);
      if (existing) {
        existing.segments.push({
          experience_id: r.experience_id,
          intent_text: r.intent_text,
          seg_index: null,
          total_segments: null,
          task_type: r.task_type,
          created_at: r.created_at,
          sensitivity: r.sensitivity,
          review_status: r.review_status,
          turn_count: r.turn_count,
          source_byte_start: r.source_byte_start,
          source_byte_end: r.source_byte_end,
          task_status: r.task_status,
        });
        if (r.created_at < existing.started_at) existing.started_at = r.created_at;
        if (r.created_at > existing.ended_at) existing.ended_at = r.created_at;
      } else {
        groups.set(key, {
          session_id: r.group_session_id,
          agent_type: r.agent_type,
          agent_name: r.agent_name,
          started_at: r.created_at,
          ended_at: r.created_at,
          segments: [
            {
              experience_id: r.experience_id,
              intent_text: r.intent_text,
              seg_index: null,
              total_segments: null,
              task_type: r.task_type,
              created_at: r.created_at,
              sensitivity: r.sensitivity,
              review_status: r.review_status,
              turn_count: r.turn_count,
              source_byte_start: r.source_byte_start,
              source_byte_end: r.source_byte_end,
              task_status: r.task_status,
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
        if (a.source_byte_start != null && b.source_byte_start != null) {
          return a.source_byte_start - b.source_byte_start;
        }
        return a.created_at.localeCompare(b.created_at);
      });
      g.segments.forEach((segment, index) => {
        if (segment.seg_index == null) segment.seg_index = index;
        if (segment.total_segments == null) segment.total_segments = g.segments.length;
      });
    }
    out.sort((a, b) => b.ended_at.localeCompare(a.ended_at));
    return out.slice(0, limit);
  } catch {
    return [];
  }
}

export async function topAgentsByContribution(limit = 10): Promise<{
  agent_id: string;
  agent_name: string;
  team: string;
  experiences: number;
  last_active: string;
}[]> {
  try {
    const db = getDb();
    // Only count experiences the current viewer can see.
    const acl = await aclVisibilityClause();
    return db
      .prepare(
        `SELECT a.agent_id, a.name AS agent_name, a.team,
                COUNT(e.experience_id) AS experiences,
                MAX(e.created_at) AS last_active
         FROM agents a
         LEFT JOIN experiences e ON e.agent_id = a.agent_id
              AND COALESCE(e.revoked, 0) = 0
              AND ${acl.sql}
         GROUP BY a.agent_id
         HAVING experiences > 0
         ORDER BY experiences DESC, last_active DESC
         LIMIT ?`,
      )
      .all(...acl.params, limit) as Array<{
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

async function clusterExperienceVisibility() {
  const me = await getCurrentUser();
  const conditions = [
    "(e.acl = 'public' AND COALESCE(e.publish_status, 'private') = 'published')",
  ];
  const params: unknown[] = [];
  if (me) {
    const viewer = getDb()
      .prepare("SELECT agent_id, team, COALESCE(owner, name) AS owner FROM agents WHERE name = ?")
      .get(me.default_agent_name) as
      | { agent_id: string; team: string; owner: string }
      | undefined;
    if (viewer) {
      conditions.push("a.agent_id = ?", "COALESCE(a.owner, a.name) = ?");
      params.push(viewer.agent_id, viewer.owner);
      if (viewer.team) {
        conditions.push("e.acl = ?");
        params.push(`team:${viewer.team}`);
      }
    }
  }
  return { sql: `(${conditions.join(" OR ")})`, params };
}

export async function listClusters(limit = 100): Promise<ClusterRow[]> {
  try {
    const db = getDb();
    const visibility = await clusterExperienceVisibility();
    return db
      .prepare(
        `SELECT k.cluster_id,
                CASE WHEN COUNT(m.experience_id) = k.member_count
                     THEN k.label ELSE '部分可见经验簇' END AS label,
                COUNT(m.experience_id) AS member_count,
                CASE WHEN COUNT(m.experience_id) = k.member_count
                     THEN k.new_since_crystallize ELSE 0 END AS new_since_crystallize,
                CASE WHEN COUNT(m.experience_id) = k.member_count
                     THEN k.crystallized_skill_id ELSE NULL END AS crystallized_skill_id,
                CASE WHEN COUNT(m.experience_id) = k.member_count
                     THEN k.last_crystallized_at ELSE NULL END AS last_crystallized_at,
                CASE WHEN COUNT(m.experience_id) = k.member_count
                     THEN k.last_structure_score ELSE NULL END AS last_structure_score,
                k.created_at, k.updated_at
         FROM knowledge_clusters k
         JOIN cluster_membership m ON m.cluster_id = k.cluster_id
         JOIN experiences e ON e.experience_id = m.experience_id
         JOIN agents a ON a.agent_id = e.agent_id
         WHERE COALESCE(e.revoked, 0) = 0 AND ${visibility.sql}
         GROUP BY k.cluster_id
         ORDER BY COUNT(m.experience_id) DESC, k.updated_at DESC
         LIMIT ?`,
      )
      .all(...visibility.params, limit) as ClusterRow[];
  } catch {
    return [];
  }
}

export async function getCluster(clusterId: string): Promise<ClusterRow | null> {
  try {
    const db = getDb();
    const visibility = await clusterExperienceVisibility();
    const row = db
      .prepare(
        `SELECT k.cluster_id,
                CASE WHEN COUNT(m.experience_id) = k.member_count
                     THEN k.label ELSE '部分可见经验簇' END AS label,
                COUNT(m.experience_id) AS member_count,
                CASE WHEN COUNT(m.experience_id) = k.member_count
                     THEN k.new_since_crystallize ELSE 0 END AS new_since_crystallize,
                CASE WHEN COUNT(m.experience_id) = k.member_count
                     THEN k.crystallized_skill_id ELSE NULL END AS crystallized_skill_id,
                CASE WHEN COUNT(m.experience_id) = k.member_count
                     THEN k.last_crystallized_at ELSE NULL END AS last_crystallized_at,
                CASE WHEN COUNT(m.experience_id) = k.member_count
                     THEN k.last_structure_score ELSE NULL END AS last_structure_score,
                k.created_at, k.updated_at
         FROM knowledge_clusters k
         JOIN cluster_membership m ON m.cluster_id = k.cluster_id
         JOIN experiences e ON e.experience_id = m.experience_id
         JOIN agents a ON a.agent_id = e.agent_id
         WHERE k.cluster_id = ? AND COALESCE(e.revoked, 0) = 0
           AND ${visibility.sql}
         GROUP BY k.cluster_id`,
      )
      .get(clusterId, ...visibility.params) as ClusterRow | undefined;
    return row ?? null;
  } catch {
    return null;
  }
}

export async function getClusterMembers(clusterId: string): Promise<ClusterMember[]> {
  try {
    const db = getDb();
    const visibility = await clusterExperienceVisibility();
    return db
      .prepare(
        `SELECT m.experience_id, m.similarity, m.added_at,
                e.intent_text, e.trajectory_score, e.task_type
         FROM cluster_membership m
         JOIN experiences e ON e.experience_id = m.experience_id
         JOIN agents a ON a.agent_id = e.agent_id
         WHERE m.cluster_id = ?
           AND COALESCE(e.revoked, 0) = 0
           AND ${visibility.sql}
         ORDER BY m.similarity DESC, m.added_at ASC`,
      )
      .all(clusterId, ...visibility.params) as ClusterMember[];
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

export async function getCrystallizedSkillByCluster(
  clusterId: string,
): Promise<CrystallizedSkillFull | null> {
  try {
    const visibleCluster = await getCluster(clusterId);
    if (!visibleCluster?.crystallized_skill_id) return null;
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

export async function listExperiencesWithRewards(limit = 50): Promise<{
  experience_id: string;
  intent_text: string;
  task_type: string;
  agent_id: string;
  created_at: string;
  reward_count: number;
  judge_models: string;
  trajectory_score: number | null;
}[]> {
  try {
    const db = getDb();
    if (!tableExists(db, "turn_rewards")) return [];
    const visibility = await rewardVisibilityClause();
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
         LEFT JOIN agents a ON a.agent_id = e.agent_id
         WHERE ${visibility.sql}
         GROUP BY tr.experience_id
         ORDER BY MAX(tr.annotated_at) DESC
         LIMIT ?`,
      )
      .all(...visibility.params, limit) as Array<{
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

export type ReuseRewardStats = {
  recall_events: number;
  injected_items: number;
  feedback_items: number;
  used_items: number;
  not_used_items: number;
  positive_items: number;
  negative_items: number;
  neutral_items: number;
  experiences_touched: number;
  q_updates: number;
  avg_reward: number | null;
  avg_confidence: number | null;
  last_feedback_at: string | null;
};

export type ReuseRewardEventRow = {
  event_id: string;
  viewer_name: string;
  query_text: string;
  scope: string;
  task_type: string | null;
  top_k: number;
  final_status: string;
  created_at: string;
  feedback_at: string | null;
  item_count: number;
  feedback_count: number;
  used_count: number;
  not_used_count: number;
  avg_reward: number | null;
  avg_confidence: number | null;
};

export type ReuseRewardExperienceRow = {
  experience_id: string;
  intent_text: string | null;
  task_type: string;
  acl: string;
  created_at: string;
  reuse_count: number;
  q_update_count: number;
  q_scalar: number;
  reuse_rate: number;
  feedback_evidence: number;
  rank_score: number;
  event_count: number;
  feedback_count: number;
  used_count: number;
  not_used_count: number;
  positive_count: number;
  negative_count: number;
  avg_reward: number | null;
  avg_confidence: number | null;
  latest_feedback_at: string | null;
};

export type ReuseRewardItemRow = {
  event_id: string;
  experience_id: string;
  chunk_id: string;
  rank: number;
  score: number;
  similarity: number;
  chunk_type: string | null;
  was_used_by_agent: number | null;
  reward: number | null;
  confidence: number | null;
  feedback_source: string | null;
  feedback_reason: string | null;
  feedback_at: string | null;
  viewer_name: string;
  query_text: string;
  final_status: string;
  intent_text: string | null;
  task_type: string;
  acl: string;
  q_scalar: number;
};

export type ReuseRewardTimelinePoint = {
  day: string;
  feedback_count: number;
  avg_reward: number | null;
  q_updates: number;
};

export type ReuseRewardDashboard = {
  stats: ReuseRewardStats;
  recentEvents: ReuseRewardEventRow[];
  topExperiences: ReuseRewardExperienceRow[];
  recentItems: ReuseRewardItemRow[];
  timeline: ReuseRewardTimelinePoint[];
  qDistribution: { bucket: string; count: number }[];
};

function emptyReuseRewardDashboard(): ReuseRewardDashboard {
  return {
    stats: {
      recall_events: 0,
      injected_items: 0,
      feedback_items: 0,
      used_items: 0,
      not_used_items: 0,
      positive_items: 0,
      negative_items: 0,
      neutral_items: 0,
      experiences_touched: 0,
      q_updates: 0,
      avg_reward: null,
      avg_confidence: null,
      last_feedback_at: null,
    },
    recentEvents: [],
    topExperiences: [],
    recentItems: [],
    timeline: [],
    qDistribution: [],
  };
}

export async function getReuseRewardDashboard(limit = 20): Promise<ReuseRewardDashboard> {
  try {
    const db = getDb();
    const hasReuse = tableExists(db, "reuse_events") && tableExists(db, "reuse_items");

    const visibility = await rewardVisibilityClause("re");
    const expVisibility = await rewardVisibilityClause();

    const statsRow = hasReuse
      ? (db
          .prepare(
            `SELECT
           COUNT(DISTINCT re.event_id) AS recall_events,
           COUNT(*) AS injected_items,
           COALESCE(SUM(CASE WHEN ri.feedback_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS feedback_items,
           COALESCE(SUM(CASE WHEN ri.was_used_by_agent = 1 THEN 1 ELSE 0 END), 0) AS used_items,
           COALESCE(SUM(CASE WHEN ri.was_used_by_agent = 0 THEN 1 ELSE 0 END), 0) AS not_used_items,
           COALESCE(SUM(CASE WHEN ri.feedback_at IS NOT NULL AND ri.reward > 0 THEN 1 ELSE 0 END), 0) AS positive_items,
           COALESCE(SUM(CASE WHEN ri.feedback_at IS NOT NULL AND ri.reward < 0 THEN 1 ELSE 0 END), 0) AS negative_items,
           COALESCE(SUM(CASE WHEN ri.feedback_at IS NOT NULL AND ri.reward = 0 THEN 1 ELSE 0 END), 0) AS neutral_items,
           COUNT(DISTINCT CASE WHEN ri.feedback_at IS NOT NULL THEN ri.experience_id END) AS experiences_touched,
           AVG(CASE WHEN ri.feedback_at IS NOT NULL THEN ri.reward END) AS avg_reward,
           AVG(CASE WHEN ri.feedback_at IS NOT NULL THEN ri.confidence END) AS avg_confidence,
           MAX(ri.feedback_at) AS last_feedback_at
         FROM reuse_items ri
         JOIN reuse_events re ON re.event_id = ri.event_id
         JOIN experiences e ON e.experience_id = ri.experience_id
         LEFT JOIN agents a ON a.agent_id = e.agent_id
         WHERE ${visibility.sql}
           AND COALESCE(e.revoked, 0) = 0`,
          )
          .get(...visibility.params) as Omit<ReuseRewardStats, "q_updates"> | undefined)
      : undefined;

    const qUpdates = tableExists(db, "q_updates")
      ? ((db
          .prepare(
            `SELECT COUNT(*) AS q_updates
             FROM q_updates qu
             JOIN experiences e ON e.experience_id = qu.experience_id
             LEFT JOIN agents a ON a.agent_id = e.agent_id
             WHERE ${expVisibility.sql}
               AND COALESCE(e.revoked, 0) = 0
               AND COALESCE(qu.triggered_by_child, '') LIKE 'reuse:%'`,
          )
          .get(...expVisibility.params) as { q_updates: number } | undefined)?.q_updates ?? 0)
      : 0;

    const stats: ReuseRewardStats = {
      recall_events: statsRow?.recall_events ?? 0,
      injected_items: statsRow?.injected_items ?? 0,
      feedback_items: statsRow?.feedback_items ?? 0,
      used_items: statsRow?.used_items ?? 0,
      not_used_items: statsRow?.not_used_items ?? 0,
      positive_items: statsRow?.positive_items ?? 0,
      negative_items: statsRow?.negative_items ?? 0,
      neutral_items: statsRow?.neutral_items ?? 0,
      experiences_touched: statsRow?.experiences_touched ?? 0,
      q_updates: qUpdates,
      avg_reward: statsRow?.avg_reward ?? null,
      avg_confidence: statsRow?.avg_confidence ?? null,
      last_feedback_at: statsRow?.last_feedback_at ?? null,
    };

    const recentEvents = hasReuse
      ? (db
          .prepare(
            `SELECT
           re.event_id, re.viewer_name, re.query_text, re.scope, re.task_type,
           re.top_k, re.final_status, re.created_at, re.feedback_at,
           COUNT(*) AS item_count,
           COALESCE(SUM(CASE WHEN ri.feedback_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS feedback_count,
           COALESCE(SUM(CASE WHEN ri.was_used_by_agent = 1 THEN 1 ELSE 0 END), 0) AS used_count,
           COALESCE(SUM(CASE WHEN ri.was_used_by_agent = 0 THEN 1 ELSE 0 END), 0) AS not_used_count,
           AVG(CASE WHEN ri.feedback_at IS NOT NULL THEN ri.reward END) AS avg_reward,
           AVG(CASE WHEN ri.feedback_at IS NOT NULL THEN ri.confidence END) AS avg_confidence
         FROM reuse_events re
         JOIN reuse_items ri ON ri.event_id = re.event_id
         JOIN experiences e ON e.experience_id = ri.experience_id
         LEFT JOIN agents a ON a.agent_id = e.agent_id
         WHERE ${visibility.sql}
           AND COALESCE(e.revoked, 0) = 0
         GROUP BY re.event_id
         ORDER BY COALESCE(re.feedback_at, re.created_at) DESC
         LIMIT ?`,
          )
          .all(...visibility.params, limit) as ReuseRewardEventRow[])
      : [];

    // Online behavior is useful only after enough observations. Gate the
    // reuse/reward terms with n/(n+5), and shrink both rates toward a neutral
    // prior so a single positive click cannot jump to the top of the board.
    const feedbackCountSql = hasReuse ? "COALESCE(f.feedback_count, 0)" : "0";
    const usedCountSql = hasReuse ? "COALESCE(f.used_count, 0)" : "0";
    const avgRewardSql = hasReuse ? "f.avg_reward" : "0";
    const feedbackEvidenceSql =
      `(CAST(${feedbackCountSql} AS REAL) / (${feedbackCountSql} + 5.0))`;
    const smoothedReuseSql =
      `((CAST(${usedCountSql} AS REAL) + 2.0) / (${feedbackCountSql} + 4.0))`;
    const smoothedRewardSql =
      `((${feedbackCountSql} * ((COALESCE(${avgRewardSql}, 0) + 1.0) / 2.0) + 2.0) ` +
      `/ (${feedbackCountSql} + 4.0))`;

    const topRows = db
      .prepare(
        `${hasReuse ? `
         WITH feedback AS (
           SELECT
             ri.experience_id,
             COUNT(DISTINCT ri.event_id) AS event_count,
             COUNT(*) AS feedback_count,
             COALESCE(SUM(CASE WHEN ri.was_used_by_agent = 1 THEN 1 ELSE 0 END), 0) AS used_count,
             COALESCE(SUM(CASE WHEN ri.was_used_by_agent = 0 THEN 1 ELSE 0 END), 0) AS not_used_count,
             COALESCE(SUM(CASE WHEN ri.reward > 0 THEN 1 ELSE 0 END), 0) AS positive_count,
             COALESCE(SUM(CASE WHEN ri.reward < 0 THEN 1 ELSE 0 END), 0) AS negative_count,
             AVG(ri.reward) AS avg_reward,
             AVG(ri.confidence) AS avg_confidence,
             MAX(ri.feedback_at) AS latest_feedback_at
           FROM reuse_items ri
           WHERE ri.feedback_at IS NOT NULL
           GROUP BY ri.experience_id
         )
         ` : ""}
         SELECT
           e.experience_id, e.intent_text, e.task_type, e.acl, e.created_at,
           COALESCE(e.reuse_count, 0) AS reuse_count,
           COALESCE(e.q_update_count, 0) AS q_update_count,
           e.q_outcome, e.q_intent, e.q_execution, e.q_orchestration, e.q_expression,
           e.trajectory_score,
           ${hasReuse ? "COALESCE(f.event_count, 0)" : "0"} AS event_count,
           ${hasReuse ? "COALESCE(f.feedback_count, 0)" : "0"} AS feedback_count,
           ${hasReuse ? "COALESCE(f.used_count, 0)" : "0"} AS used_count,
           ${hasReuse ? "COALESCE(f.not_used_count, 0)" : "0"} AS not_used_count,
           ${hasReuse ? "COALESCE(f.positive_count, 0)" : "0"} AS positive_count,
           ${hasReuse ? "COALESCE(f.negative_count, 0)" : "0"} AS negative_count,
           ${hasReuse ? "f.avg_reward" : "NULL"} AS avg_reward,
           ${hasReuse ? "f.avg_confidence" : "NULL"} AS avg_confidence,
           ${hasReuse ? "f.latest_feedback_at" : "NULL"} AS latest_feedback_at,
           CASE
             WHEN ${hasReuse ? "COALESCE(f.feedback_count, 0)" : "0"} > 0
             THEN CAST(${hasReuse ? "COALESCE(f.used_count, 0)" : "0"} AS REAL) / ${hasReuse ? "COALESCE(f.feedback_count, 0)" : "1"}
             ELSE 0.0
           END AS reuse_rate,
           ${feedbackEvidenceSql} AS feedback_evidence,
           CASE
             WHEN COALESCE(e.q_update_count, 0) > 0
               OR ABS(0.30 * COALESCE(e.q_outcome, 0)
                 + 0.20 * COALESCE(e.q_intent, 0)
                 + 0.20 * COALESCE(e.q_execution, 0)
                 + 0.15 * COALESCE(e.q_orchestration, 0)
                 + 0.15 * COALESCE(e.q_expression, 0)) > 0.000000001
             THEN 0.30 * COALESCE(e.q_outcome, 0)
               + 0.20 * COALESCE(e.q_intent, 0)
               + 0.20 * COALESCE(e.q_execution, 0)
               + 0.15 * COALESCE(e.q_orchestration, 0)
               + 0.15 * COALESCE(e.q_expression, 0)
             ELSE COALESCE(e.trajectory_score, 0)
           END AS q_scalar_sql,
           (
             0.45 * ${feedbackEvidenceSql} * ${smoothedReuseSql}
             + 0.25 * ${feedbackEvidenceSql} * ${smoothedRewardSql}
             + 0.20 * ((CASE
               WHEN COALESCE(e.q_update_count, 0) > 0
                 OR ABS(0.30 * COALESCE(e.q_outcome, 0)
                   + 0.20 * COALESCE(e.q_intent, 0)
                   + 0.20 * COALESCE(e.q_execution, 0)
                   + 0.15 * COALESCE(e.q_orchestration, 0)
                   + 0.15 * COALESCE(e.q_expression, 0)) > 0.000000001
               THEN 0.30 * COALESCE(e.q_outcome, 0)
                 + 0.20 * COALESCE(e.q_intent, 0)
                 + 0.20 * COALESCE(e.q_execution, 0)
                 + 0.15 * COALESCE(e.q_orchestration, 0)
                 + 0.15 * COALESCE(e.q_expression, 0)
               ELSE COALESCE(e.trajectory_score, 0)
             END + 1.0) / 2.0)
             + 0.10 * (CAST(COALESCE(e.reuse_count, 0) AS REAL) / (COALESCE(e.reuse_count, 0) + 5.0))
           ) AS rank_score
         FROM experiences e
         LEFT JOIN agents a ON a.agent_id = e.agent_id
         ${hasReuse ? "LEFT JOIN feedback f ON f.experience_id = e.experience_id" : ""}
         WHERE ${expVisibility.sql}
           AND COALESCE(e.revoked, 0) = 0
         ORDER BY rank_score DESC,
                  reuse_rate DESC,
                  COALESCE(avg_reward, -2) DESC,
                  COALESCE(e.reuse_count, 0) DESC,
                  q_scalar_sql DESC,
                  e.created_at DESC
         LIMIT ?`,
      )
      .all(...expVisibility.params, Math.min(Math.max(limit, 20), 50)) as Array<
      Omit<ReuseRewardExperienceRow, "q_scalar"> &
        Pick<
          ExperienceRow,
          "q_outcome" | "q_intent" | "q_execution" | "q_orchestration" | "q_expression" | "trajectory_score"
        >
    >;
    const topExperiences = topRows.map((r) => ({
      experience_id: r.experience_id,
      intent_text: r.intent_text,
      task_type: r.task_type,
      acl: r.acl,
      created_at: r.created_at,
      reuse_count: r.reuse_count,
      q_update_count: r.q_update_count,
      q_scalar: qScalar(r),
      reuse_rate: r.reuse_rate,
      feedback_evidence: r.feedback_evidence,
      rank_score: r.rank_score,
      event_count: r.event_count,
      feedback_count: r.feedback_count,
      used_count: r.used_count,
      not_used_count: r.not_used_count,
      positive_count: r.positive_count,
      negative_count: r.negative_count,
      avg_reward: r.avg_reward,
      avg_confidence: r.avg_confidence,
      latest_feedback_at: r.latest_feedback_at,
    }));

    const itemRows = hasReuse
      ? (db
          .prepare(
            `SELECT
           ri.event_id, ri.experience_id, ri.chunk_id, ri.rank, ri.score,
           ri.similarity, ri.chunk_type, ri.was_used_by_agent, ri.reward,
           ri.confidence, ri.feedback_source, ri.feedback_reason, ri.feedback_at,
           re.viewer_name, re.query_text, re.final_status,
           e.intent_text, e.task_type, e.acl,
           e.q_update_count, e.trajectory_score,
           e.q_outcome, e.q_intent, e.q_execution, e.q_orchestration, e.q_expression
         FROM reuse_items ri
         JOIN reuse_events re ON re.event_id = ri.event_id
         JOIN experiences e ON e.experience_id = ri.experience_id
         LEFT JOIN agents a ON a.agent_id = e.agent_id
         WHERE ${visibility.sql}
           AND COALESCE(e.revoked, 0) = 0
           AND ri.feedback_at IS NOT NULL
         ORDER BY ri.feedback_at DESC
         LIMIT ?`,
          )
          .all(...visibility.params, limit) as Array<
          Omit<ReuseRewardItemRow, "q_scalar"> &
            Pick<
              ExperienceRow,
              "q_outcome" | "q_intent" | "q_execution" | "q_orchestration" | "q_expression" | "q_update_count" | "trajectory_score"
            >
        >)
      : [];
    const recentItems = itemRows.map((r) => ({
      event_id: r.event_id,
      experience_id: r.experience_id,
      chunk_id: r.chunk_id,
      rank: r.rank,
      score: r.score,
      similarity: r.similarity,
      chunk_type: r.chunk_type,
      was_used_by_agent: r.was_used_by_agent,
      reward: r.reward,
      confidence: r.confidence,
      feedback_source: r.feedback_source,
      feedback_reason: r.feedback_reason,
      feedback_at: r.feedback_at,
      viewer_name: r.viewer_name,
      query_text: r.query_text,
      final_status: r.final_status,
      intent_text: r.intent_text,
      task_type: r.task_type,
      acl: r.acl,
      q_scalar: qScalar(r),
    }));

    const feedbackDays = hasReuse
      ? (db
          .prepare(
            `SELECT date(ri.feedback_at) AS day,
                COUNT(*) AS feedback_count,
                AVG(ri.reward) AS avg_reward
         FROM reuse_items ri
         JOIN reuse_events re ON re.event_id = ri.event_id
         JOIN experiences e ON e.experience_id = ri.experience_id
         LEFT JOIN agents a ON a.agent_id = e.agent_id
         WHERE ${visibility.sql}
           AND COALESCE(e.revoked, 0) = 0
           AND ri.feedback_at IS NOT NULL
           AND date(ri.feedback_at) >= date('now', '-13 days')
         GROUP BY date(ri.feedback_at)`,
          )
          .all(...visibility.params) as Array<{
          day: string;
          feedback_count: number;
          avg_reward: number | null;
        }>)
      : [];
    const updateDays = tableExists(db, "q_updates")
      ? (db
          .prepare(
            `SELECT date(qu.created_at) AS day, COUNT(*) AS q_updates
             FROM q_updates qu
             JOIN experiences e ON e.experience_id = qu.experience_id
             LEFT JOIN agents a ON a.agent_id = e.agent_id
             WHERE ${expVisibility.sql}
               AND COALESCE(e.revoked, 0) = 0
               AND COALESCE(qu.triggered_by_child, '') LIKE 'reuse:%'
               AND date(qu.created_at) >= date('now', '-13 days')
             GROUP BY date(qu.created_at)`,
          )
          .all(...expVisibility.params) as Array<{ day: string; q_updates: number }>)
      : [];
    const updateByDay = new Map(updateDays.map((r) => [r.day, r.q_updates]));
    const feedbackByDay = new Map(feedbackDays.map((r) => [r.day, r]));
    const timeline: ReuseRewardTimelinePoint[] = [];
    const today = new Date();
    for (let i = 13; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(today.getDate() - i);
      const day = d.toISOString().slice(0, 10);
      const f = feedbackByDay.get(day);
      timeline.push({
        day,
        feedback_count: f?.feedback_count ?? 0,
        avg_reward: f?.avg_reward ?? null,
        q_updates: updateByDay.get(day) ?? 0,
      });
    }

    const qRows = db
      .prepare(
        `SELECT q_outcome, q_intent, q_execution, q_orchestration, q_expression,
                q_update_count, trajectory_score
         FROM experiences e
         LEFT JOIN agents a ON a.agent_id = e.agent_id
         WHERE ${expVisibility.sql}
           AND COALESCE(e.revoked, 0) = 0`,
      )
      .all(...expVisibility.params) as Pick<
      ExperienceRow,
      "q_outcome" | "q_intent" | "q_execution" | "q_orchestration" | "q_expression" | "q_update_count" | "trajectory_score"
    >[];
    const qBuckets = new Map<string, number>([
      ["<-0.5", 0],
      ["-0.5..0", 0],
      ["0..0.25", 0],
      ["0.25..0.5", 0],
      [">=0.5", 0],
    ]);
    for (const row of qRows) {
      const q = qScalar(row);
      const key = q < -0.5 ? "<-0.5" : q < 0 ? "-0.5..0" : q < 0.25 ? "0..0.25" : q < 0.5 ? "0.25..0.5" : ">=0.5";
      qBuckets.set(key, (qBuckets.get(key) ?? 0) + 1);
    }
    const qDistribution = [...qBuckets.entries()].map(([bucket, count]) => ({ bucket, count }));

    return { stats, recentEvents, topExperiences, recentItems, timeline, qDistribution };
  } catch {
    return emptyReuseRewardDashboard();
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
      "SELECT q_outcome, q_intent, q_execution, q_orchestration, q_expression, q_update_count, trajectory_score FROM experiences",
    )
    .all() as Pick<
      ExperienceRow,
      "q_outcome" | "q_intent" | "q_execution" | "q_orchestration" | "q_expression" | "q_update_count" | "trajectory_score"
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
