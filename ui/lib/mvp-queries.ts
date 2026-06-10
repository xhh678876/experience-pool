import { createHash } from "node:crypto";
import { getDb } from "./db";

const DIM = 256;

export type AgentOption = {
  name: string;
  team: string;
  agent_id: string;
};

export type MvpExperienceHit = {
  experience_id: string;
  agent_name: string;
  team: string;
  query: string;
  intent: string;
  steps: string[];
  outcome: string;
  task_type: string;
  source_model: string;
  acl: string;
  review_status: string;
  sensitivity: string;
  created_at: string;
  visit_count: number;
  similarity: number | null;
};

export type MvpStats = {
  agents: number;
  teams: number;
  liteExperiences: number;
  searchable: number;
  privateRows: number;
  teamRows: number;
  publicRows: number;
  redactions: number;
  topTasks: { task_type: string; count: number }[];
};

type DbRow = {
  experience_id: string;
  agent_id: string;
  agent_name: string | null;
  team: string | null;
  query: string | null;
  intent_text: string | null;
  script_steps: string | null;
  outcome: string | null;
  summary: string | null;
  task_type: string;
  source_model: string;
  acl: string;
  review_status: string;
  sensitivity: string;
  created_at: string;
  visit_count: number | null;
  vector?: Buffer;
};

type CountRow = { count: number };

export function listAgents(): AgentOption[] {
  return safeAll<AgentOption>(
    "SELECT agent_id, name, team FROM agents ORDER BY name ASC",
  );
}

export function getMvpStats(): MvpStats {
  const liteWhere = "WHERE COALESCE(ingest_path, 'full') = 'lite'";
  const searchableSql = `
    SELECT COUNT(*) AS count FROM experiences
    ${liteWhere}
      AND review_status IN ('approved', 'auto_approved', 'edited')
      AND extraction_status = 'done'
  `;
  const topTasks = safeAll<{ task_type: string; count: number }>(`
    SELECT task_type, COUNT(*) AS count
    FROM experiences
    ${liteWhere}
    GROUP BY task_type
    ORDER BY count DESC, task_type ASC
    LIMIT 5
  `);

  return {
    agents: safeCount("SELECT COUNT(*) AS count FROM agents"),
    teams: safeCount("SELECT COUNT(DISTINCT team) AS count FROM agents"),
    liteExperiences: safeCount(`SELECT COUNT(*) AS count FROM experiences ${liteWhere}`),
    searchable: safeCount(searchableSql),
    privateRows: safeCount(
      `SELECT COUNT(*) AS count FROM experiences ${liteWhere} AND acl = 'private'`,
    ),
    teamRows: safeCount(
      `SELECT COUNT(*) AS count FROM experiences ${liteWhere} AND acl LIKE 'team:%'`,
    ),
    publicRows: safeCount(
      `SELECT COUNT(*) AS count FROM experiences ${liteWhere} AND acl IN ('public', 'org')`,
    ),
    redactions: countLiteRedactions(),
    topTasks,
  };
}

export async function searchMvpExperiences({
  viewerName,
  query,
  taskType,
  topK = 6,
}: {
  viewerName: string;
  query: string;
  taskType?: string;
  topK?: number;
}): Promise<MvpExperienceHit[]> {
  const viewer = getViewer(viewerName);
  const params: unknown[] = [];
  let taskClause = "";
  if (taskType && taskType !== "all") {
    taskClause = "AND e.task_type = ?";
    params.push(taskType);
  }

  // SQL-level ACL filter — public OR mine. Belt-and-suspenders with the
  // canRead() filter below (which only saw `viewer` from a stale cookie).
  const { aclVisibilityClause } = await import("./acl-filter");
  const acl = await aclVisibilityClause();

  const rows = safeAll<DbRow>(
    `
    SELECT v.vector, e.experience_id, e.agent_id, a.name AS agent_name, a.team,
           e.query, e.intent_text, e.script_steps, e.outcome, e.summary,
           e.task_type, e.source_model, e.acl, e.review_status, e.sensitivity,
           e.created_at, e.visit_count
    FROM vectors v
    JOIN experiences e ON e.experience_id = v.experience_id
    JOIN agents a ON a.agent_id = e.agent_id
    WHERE v.kind = 'intent'
      AND COALESCE(e.ingest_path, 'full') = 'lite'
      AND COALESCE(e.revoked, 0) = 0
      AND e.review_status IN ('approved', 'auto_approved', 'edited')
      AND e.extraction_status = 'done'
      AND ${acl.sql}
      ${taskClause}
    `,
    [...acl.params, ...params],
  ).filter((row) => canRead(viewer, row));

  const trimmedQuery = query.trim();
  if (!trimmedQuery) {
    return rows
      .sort((a, b) => b.created_at.localeCompare(a.created_at))
      .slice(0, topK)
      .map((row) => rowToHit(row, null));
  }

  const qvec = embed(trimmedQuery);
  return rows
    .map((row) => ({ row, similarity: cosine(qvec, vectorFromBlob(row.vector)) }))
    .sort((a, b) => b.similarity - a.similarity)
    .slice(0, topK)
    .map(({ row, similarity }) => rowToHit(row, similarity));
}

export async function listRecentMvpExperiences(limit = 8): Promise<MvpExperienceHit[]> {
  const { aclVisibilityClause } = await import("./acl-filter");
  const acl = await aclVisibilityClause();
  return safeAll<DbRow>(
    `
    SELECT e.experience_id, e.agent_id, a.name AS agent_name, a.team,
           e.query, e.intent_text, e.script_steps, e.outcome, e.summary,
           e.task_type, e.source_model, e.acl, e.review_status, e.sensitivity,
           e.created_at, e.visit_count
    FROM experiences e
    JOIN agents a ON a.agent_id = e.agent_id
    WHERE COALESCE(e.ingest_path, 'full') = 'lite'
      AND COALESCE(e.revoked, 0) = 0
      AND ${acl.sql}
    ORDER BY e.created_at DESC
    LIMIT ?
    `,
    [...acl.params, limit],
  ).map((row) => rowToHit(row, null));
}

function getViewer(viewerName: string): AgentOption | null {
  if (!viewerName) return null;
  return safeGet<AgentOption>(
    "SELECT agent_id, name, team FROM agents WHERE name = ?",
    [viewerName],
  );
}

function canRead(viewer: AgentOption | null, row: DbRow): boolean {
  if (row.acl === "public" || row.acl === "org") return true;
  if (!viewer) return false;
  if (row.acl === "private") return viewer.agent_id === row.agent_id;
  if (row.acl.startsWith("team:")) return row.acl.slice("team:".length) === viewer.team;
  return false;
}

function rowToHit(row: DbRow, similarity: number | null): MvpExperienceHit {
  return {
    experience_id: row.experience_id,
    agent_name: row.agent_name ?? "unknown",
    team: row.team ?? "-",
    query: row.query ?? row.intent_text ?? "",
    intent: row.intent_text ?? row.query ?? "",
    steps: parseSteps(row.script_steps),
    outcome: row.outcome ?? row.summary ?? "",
    task_type: row.task_type,
    source_model: row.source_model,
    acl: row.acl,
    review_status: row.review_status,
    sensitivity: row.sensitivity,
    created_at: row.created_at,
    visit_count: row.visit_count ?? 0,
    similarity,
  };
}

function parseSteps(raw: string | null): string[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.map((item) => {
      if (typeof item === "string") return item;
      if (item && typeof item === "object") {
        const obj = item as Record<string, unknown>;
        return String(obj.step ?? obj.how ?? JSON.stringify(obj));
      }
      return String(item);
    });
  } catch {
    return [];
  }
}

function countLiteRedactions(): number {
  const rows = safeAll<{ payload: string | null }>(
    "SELECT payload FROM audit_log WHERE action = 'push_lite' ORDER BY audit_id DESC LIMIT 500",
  );
  let total = 0;
  for (const row of rows) {
    try {
      const payload = JSON.parse(row.payload ?? "{}") as {
        redactions?: Record<string, number>;
      };
      for (const n of Object.values(payload.redactions ?? {})) total += Number(n) || 0;
    } catch {
      // Ignore malformed audit payloads; the audit row itself is still useful.
    }
  }
  return total;
}

function safeCount(sql: string, params: unknown[] = []): number {
  return safeGet<CountRow>(sql, params)?.count ?? 0;
}

function safeGet<T>(sql: string, params: unknown[] = []): T | null {
  try {
    return (getDb().prepare(sql).get(...params) as T | undefined) ?? null;
  } catch {
    return null;
  }
}

function safeAll<T>(sql: string, params: unknown[] = []): T[] {
  try {
    return getDb().prepare(sql).all(...params) as T[];
  } catch {
    return [];
  }
}

function embed(text: string): Float32Array {
  const vec = new Float32Array(DIM);
  const normalized = (text || "<empty>").toLowerCase().trim();
  const source = normalized.length < 3 ? normalized + "  " : normalized;
  for (let i = 0; i < source.length - 2; i++) {
    const token = source.slice(i, i + 3);
    const hash = createHash("sha256").update(token, "utf8").digest();
    const idx = hash.readUInt32LE(0) % DIM;
    const sign = hash[4] & 1 ? 1 : -1;
    vec[idx] += sign;
  }
  let norm = 0;
  for (const v of vec) norm += v * v;
  norm = Math.sqrt(norm) || 1;
  for (let i = 0; i < DIM; i++) vec[i] = vec[i] / norm;
  return vec;
}

function vectorFromBlob(blob: Buffer | undefined): Float32Array {
  const vec = new Float32Array(DIM);
  if (!blob) return vec;
  const count = Math.min(DIM, Math.floor(blob.length / 4));
  for (let i = 0; i < count; i++) vec[i] = blob.readFloatLE(i * 4);
  return vec;
}

function cosine(a: Float32Array, b: Float32Array): number {
  let total = 0;
  for (let i = 0; i < Math.min(a.length, b.length); i++) total += a[i] * b[i];
  return total;
}
