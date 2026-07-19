"""SQLite schema. Mirrors the postgres schema with type adjustments.

Heavier columns (q_*, q_update_count, etc.) are kept here even when only the
lite v0 path is active; the lite path simply doesn't write to them. This lets
us turn on judge / credit / dedup later without a schema migration.
"""

# Columns introduced after the original v1 schema. We add them at startup with
# `PRAGMA table_info` checks so existing pool.db files keep working without a
# manual migration.
LITE_MIGRATIONS: list[tuple[str, str]] = [
    ("experiences", "query TEXT"),
    ("experiences", "outcome TEXT"),
    ("experiences", "ingest_path TEXT NOT NULL DEFAULT 'full'"),  # 'full' | 'lite'
]


SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    team TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS experiences (
    experience_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    source_model TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),

    intent_text TEXT,
    preconditions TEXT,
    script_steps TEXT,
    tool_capabilities TEXT,
    key_decisions TEXT,
    pitfalls TEXT,

    summary TEXT,
    trajectory_path TEXT,
    tool_calls_count INTEGER DEFAULT 0,
    token_count INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,

    q_outcome REAL DEFAULT 0,
    q_intent REAL DEFAULT 0,
    q_execution REAL DEFAULT 0,
    q_orchestration REAL DEFAULT 0,
    q_expression REAL DEFAULT 0,
    q_update_count INTEGER DEFAULT 0,

    visit_count INTEGER DEFAULT 0,
    reuse_count INTEGER DEFAULT 0,

    sanitization_status TEXT NOT NULL DEFAULT 'pending',
    review_status TEXT NOT NULL DEFAULT 'pending',
    extraction_status TEXT NOT NULL DEFAULT 'pending',
    acl TEXT NOT NULL DEFAULT 'private',
    tags TEXT DEFAULT '[]',
    sensitivity TEXT NOT NULL DEFAULT 'medium',

    FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
);
CREATE INDEX IF NOT EXISTS idx_experiences_task_type ON experiences(task_type);
CREATE INDEX IF NOT EXISTS idx_experiences_agent ON experiences(agent_id);

CREATE TABLE IF NOT EXISTS rewards (
    reward_id TEXT PRIMARY KEY,
    experience_id TEXT NOT NULL,
    judge_version TEXT NOT NULL,
    judge_model TEXT NOT NULL,

    r_outcome REAL NOT NULL,
    r_intent REAL NOT NULL,
    r_execution REAL NOT NULL,
    r_orchestration REAL NOT NULL,
    r_expression REAL NOT NULL,

    confidence REAL NOT NULL,
    self_consistency_variance REAL,
    is_unstable INTEGER DEFAULT 0,
    rationale TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(experience_id) REFERENCES experiences(experience_id)
);

CREATE TABLE IF NOT EXISTS experience_edges (
    parent_id TEXT NOT NULL,
    child_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    credit_applied INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (parent_id, child_id),
    FOREIGN KEY(parent_id) REFERENCES experiences(experience_id),
    FOREIGN KEY(child_id) REFERENCES experiences(experience_id)
);

-- Per-turn rewards (synergy schema: 5 dims × {-1, 0, +1} + confidence + reason).
-- Different from `rewards` (trajectory-level summary) — this is per assistant
-- turn, with the judge_model in the PK so multiple judges can coexist.
CREATE TABLE IF NOT EXISTS turn_rewards (
    experience_id    TEXT NOT NULL,
    turn_index       INTEGER NOT NULL,
    user_turn_index  INTEGER,
    r_outcome        INTEGER NOT NULL,
    r_intent         INTEGER NOT NULL,
    r_execution      INTEGER NOT NULL,
    r_orchestration  INTEGER NOT NULL,
    r_expression     INTEGER NOT NULL,
    confidence       REAL NOT NULL,
    reason           TEXT,
    judge_model      TEXT NOT NULL,
    judge_backend    TEXT NOT NULL DEFAULT 'unknown',
    annotated_at     TEXT NOT NULL,
    annotated_by     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (experience_id, turn_index, judge_model),
    FOREIGN KEY(experience_id) REFERENCES experiences(experience_id)
);
CREATE INDEX IF NOT EXISTS idx_turn_rewards_exp ON turn_rewards(experience_id);
CREATE INDEX IF NOT EXISTS idx_turn_rewards_model ON turn_rewards(judge_model);

-- Content fingerprint for idempotent push. Borrowed from
-- modelscope/ultron Trajectory Hub — fingerprint = first 16 hex of
-- SHA-256(role + content) over the trajectory. Re-uploading the same
-- content returns the existing experience_id without writing a new row.
CREATE TABLE IF NOT EXISTS content_fingerprints (
    fingerprint   TEXT NOT NULL,
    experience_id TEXT NOT NULL,
    agent_id      TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (fingerprint, agent_id),
    FOREIGN KEY(experience_id) REFERENCES experiences(experience_id)
);
CREATE INDEX IF NOT EXISTS idx_fp_exp ON content_fingerprints(experience_id);

-- Knowledge clusters (Ultron-style skill crystallization). Each cluster
-- tracks a semantic region in embedding space; once it accumulates enough
-- members it crystallizes into a workflow skill.
CREATE TABLE IF NOT EXISTS knowledge_clusters (
    cluster_id            TEXT PRIMARY KEY,
    label                 TEXT,
    centroid_blob         BLOB NOT NULL,
    centroid_dim          INTEGER NOT NULL,
    member_count          INTEGER NOT NULL DEFAULT 0,
    new_since_crystallize INTEGER NOT NULL DEFAULT 0,
    crystallized_skill_id TEXT,
    last_crystallized_at  TEXT,
    last_structure_score  REAL,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cluster_skill ON knowledge_clusters(crystallized_skill_id);

CREATE TABLE IF NOT EXISTS cluster_membership (
    cluster_id    TEXT NOT NULL,
    experience_id TEXT NOT NULL,
    similarity    REAL NOT NULL,
    added_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (cluster_id, experience_id),
    FOREIGN KEY(cluster_id) REFERENCES knowledge_clusters(cluster_id),
    FOREIGN KEY(experience_id) REFERENCES experiences(experience_id)
);
CREATE INDEX IF NOT EXISTS idx_cmember_exp ON cluster_membership(experience_id);

CREATE TABLE IF NOT EXISTS q_updates (
    update_id TEXT PRIMARY KEY,
    experience_id TEXT NOT NULL,
    triggered_by_child TEXT,
    alpha REAL NOT NULL,
    confidence REAL NOT NULL,
    delta_outcome REAL,
    delta_intent REAL,
    delta_execution REAL,
    delta_orchestration REAL,
    delta_expression REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reuse_events (
    event_id TEXT PRIMARY KEY,
    viewer_agent_id TEXT NOT NULL,
    viewer_name TEXT NOT NULL,
    query_text TEXT NOT NULL,
    scope TEXT NOT NULL,
    task_type TEXT,
    project_ref TEXT,
    top_k INTEGER NOT NULL,
    final_status TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    feedback_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_reuse_events_viewer ON reuse_events(viewer_agent_id, created_at);

CREATE TABLE IF NOT EXISTS reuse_items (
    event_id TEXT NOT NULL,
    experience_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL DEFAULT '',
    rank INTEGER NOT NULL,
    score REAL NOT NULL DEFAULT 0,
    similarity REAL NOT NULL DEFAULT 0,
    source TEXT,
    chunk_type TEXT,
    was_injected INTEGER NOT NULL DEFAULT 1,
    was_used_by_agent INTEGER,
    reward REAL,
    confidence REAL,
    feedback_source TEXT,
    feedback_reason TEXT,
    feedback_at TEXT,
    PRIMARY KEY(event_id, experience_id, chunk_id),
    FOREIGN KEY(event_id) REFERENCES reuse_events(event_id),
    FOREIGN KEY(experience_id) REFERENCES experiences(experience_id)
);
CREATE INDEX IF NOT EXISTS idx_reuse_items_exp ON reuse_items(experience_id);
CREATE INDEX IF NOT EXISTS idx_reuse_items_event ON reuse_items(event_id);

CREATE TABLE IF NOT EXISTS search_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    experience_id TEXT NOT NULL,
    queried_by TEXT,
    query_text TEXT,
    rank INTEGER,
    similarity REAL,
    score REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    actor_kind TEXT NOT NULL,
    action TEXT NOT NULL,
    target_id TEXT,
    payload TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Vector index lives in a separate table: id, kind ('intent'|'script'), payload, vector_blob.
-- For skills the kind is 'skill_intent' or 'skill_content' and the foreign key
-- is logical (we don't add a constraint because the same table holds both
-- experience and skill vectors).
CREATE TABLE IF NOT EXISTS vectors (
    experience_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    vector BLOB NOT NULL,
    PRIMARY KEY (experience_id, kind)
);

-- ===== Skills =====
-- A skill is a reusable bundle (the SKILL.md file plus any helper scripts)
-- that an agent can invoke during a trajectory. Skills earn Q values via the
-- same one-hop credit-assignment loop: when a child experience declares
-- `--uses-skill foo`, the foo skill's Q is updated from the child's reward.
CREATE TABLE IF NOT EXISTS skills (
    skill_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    name TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '0.1.0',
    description TEXT,                 -- one-liner from frontmatter
    content_md TEXT,                  -- sanitized SKILL.md body
    bundle_path TEXT,                 -- on-disk tar.gz path
    bundle_sha256 TEXT,
    bundle_size_bytes INTEGER DEFAULT 0,
    file_count INTEGER DEFAULT 0,

    trigger_keywords TEXT DEFAULT '[]',  -- JSON list
    dependencies TEXT DEFAULT '[]',      -- JSON list of other skill names
    frontmatter_json TEXT DEFAULT '{}',  -- raw frontmatter for forward-compat

    -- Q values, exact same shape as experiences so the credit code reuses logic.
    q_outcome REAL DEFAULT 0,
    q_intent REAL DEFAULT 0,
    q_execution REAL DEFAULT 0,
    q_orchestration REAL DEFAULT 0,
    q_expression REAL DEFAULT 0,
    q_update_count INTEGER DEFAULT 0,

    invoke_count INTEGER DEFAULT 0,    -- how many experiences declared they used it
    install_count INTEGER DEFAULT 0,   -- how many `install-skill` calls fetched it

    sanitization_status TEXT NOT NULL DEFAULT 'pending',
    review_status TEXT NOT NULL DEFAULT 'pending',
    acl TEXT NOT NULL DEFAULT 'private',
    tags TEXT DEFAULT '[]',
    sensitivity TEXT NOT NULL DEFAULT 'medium',

    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(name, version),
    FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
);
CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(name);
CREATE INDEX IF NOT EXISTS idx_skills_agent ON skills(agent_id);
CREATE INDEX IF NOT EXISTS idx_skills_review ON skills(review_status);

-- Junction: experience <-> skills it called.
CREATE TABLE IF NOT EXISTS experience_skill_uses (
    experience_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    credit_applied INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (experience_id, skill_id),
    FOREIGN KEY(experience_id) REFERENCES experiences(experience_id),
    FOREIGN KEY(skill_id) REFERENCES skills(skill_id)
);
CREATE INDEX IF NOT EXISTS idx_skill_uses_pending ON experience_skill_uses(experience_id) WHERE credit_applied = 0;

-- Q-update audit specifically for skills (mirrors q_updates for experiences).
-- FTS5 virtual tables for keyword retrieval. These are tokenizer="unicode61"
-- which works for English and tokenizes CJK by codepoint (good enough for
-- keyword fallback alongside vector search). They're maintained by triggers
-- below so the application code doesn't have to remember to update them.
CREATE VIRTUAL TABLE IF NOT EXISTS experiences_fts USING fts5(
    experience_id UNINDEXED,
    intent_text,
    summary,
    tokenize="unicode61"
);

CREATE TRIGGER IF NOT EXISTS experiences_ai AFTER INSERT ON experiences BEGIN
    INSERT INTO experiences_fts(experience_id, intent_text, summary)
    VALUES (new.experience_id, COALESCE(new.intent_text, ''), COALESCE(new.summary, ''));
END;
CREATE TRIGGER IF NOT EXISTS experiences_au AFTER UPDATE ON experiences BEGIN
    DELETE FROM experiences_fts WHERE experience_id = old.experience_id;
    INSERT INTO experiences_fts(experience_id, intent_text, summary)
    VALUES (new.experience_id, COALESCE(new.intent_text, ''), COALESCE(new.summary, ''));
END;
CREATE TRIGGER IF NOT EXISTS experiences_ad AFTER DELETE ON experiences BEGIN
    DELETE FROM experiences_fts WHERE experience_id = old.experience_id;
END;

CREATE VIRTUAL TABLE IF NOT EXISTS skills_fts USING fts5(
    skill_id UNINDEXED,
    name,
    description,
    content_md,
    tokenize="unicode61"
);

CREATE TRIGGER IF NOT EXISTS skills_ai AFTER INSERT ON skills BEGIN
    INSERT INTO skills_fts(skill_id, name, description, content_md)
    VALUES (new.skill_id, new.name, COALESCE(new.description, ''), COALESCE(new.content_md, ''));
END;
CREATE TRIGGER IF NOT EXISTS skills_au AFTER UPDATE ON skills BEGIN
    DELETE FROM skills_fts WHERE skill_id = old.skill_id;
    INSERT INTO skills_fts(skill_id, name, description, content_md)
    VALUES (new.skill_id, new.name, COALESCE(new.description, ''), COALESCE(new.content_md, ''));
END;
CREATE TRIGGER IF NOT EXISTS skills_ad AFTER DELETE ON skills BEGIN
    DELETE FROM skills_fts WHERE skill_id = old.skill_id;
END;

CREATE TABLE IF NOT EXISTS skill_q_updates (
    update_id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL,
    triggered_by_experience TEXT,
    alpha REAL NOT NULL,
    confidence REAL NOT NULL,
    delta_outcome REAL,
    delta_intent REAL,
    delta_execution REAL,
    delta_orchestration REAL,
    delta_expression REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(skill_id) REFERENCES skills(skill_id)
);
"""
