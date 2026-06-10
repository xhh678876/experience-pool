-- Experience Pool v2 schema.
-- Continuous rewards stored as float in [-1, 1]. Discretization happens at export/UI layer.
-- Q values are mutable; reward is immutable per judge run.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE agents (
    agent_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    team TEXT NOT NULL,
    public_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE experiences (
    experience_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(agent_id),
    task_type TEXT NOT NULL,
    source_model TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Intent (z)
    intent_text TEXT,
    preconditions TEXT,

    -- Script (s)
    script_steps JSONB,             -- [{step, why, how}, ...]
    tool_capabilities JSONB,        -- abstract capability list
    key_decisions JSONB,
    pitfalls JSONB,

    -- Digest (d)
    summary TEXT,
    trajectory_uri TEXT,            -- s3://trajectories/<id>.json
    tool_calls_count INT,
    token_count INT,
    duration_ms BIGINT,

    -- Q (multi-dim, mutable, continuous in [-1, 1])
    q_outcome REAL DEFAULT 0,
    q_intent REAL DEFAULT 0,
    q_execution REAL DEFAULT 0,
    q_orchestration REAL DEFAULT 0,
    q_expression REAL DEFAULT 0,
    q_update_count INT DEFAULT 0,

    visit_count INT DEFAULT 0,
    reuse_count INT DEFAULT 0,

    -- Governance
    sanitization_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (sanitization_status IN ('pending', 'done', 'flagged', 'human_review')),
    review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending', 'approved', 'rejected', 'edited', 'auto_approved')),
    extraction_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (extraction_status IN ('pending', 'done', 'unstable', 'failed')),
    acl TEXT NOT NULL DEFAULT 'private',
    tags TEXT[] DEFAULT '{}',
    sensitivity TEXT NOT NULL DEFAULT 'medium'
        CHECK (sensitivity IN ('low', 'medium', 'high')),

    -- Pipeline tracking
    pipeline_stage TEXT NOT NULL DEFAULT 'queued'
);

CREATE INDEX idx_experiences_task_type ON experiences(task_type);
CREATE INDEX idx_experiences_agent ON experiences(agent_id);
CREATE INDEX idx_experiences_created ON experiences(created_at DESC);
CREATE INDEX idx_experiences_review ON experiences(review_status) WHERE review_status = 'pending';
CREATE INDEX idx_experiences_acl ON experiences(acl);
CREATE INDEX idx_experiences_tags ON experiences USING GIN(tags);

-- Initial reward from judge. Immutable. Multiple rows allowed when re-judged.
CREATE TABLE rewards (
    reward_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    experience_id UUID NOT NULL REFERENCES experiences(experience_id) ON DELETE CASCADE,
    judge_version TEXT NOT NULL,
    judge_model TEXT NOT NULL,

    r_outcome REAL NOT NULL,
    r_intent REAL NOT NULL,
    r_execution REAL NOT NULL,
    r_orchestration REAL NOT NULL,
    r_expression REAL NOT NULL,

    confidence REAL NOT NULL,
    self_consistency_variance REAL,
    is_unstable BOOLEAN DEFAULT FALSE,

    rationale TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_rewards_experience ON rewards(experience_id, created_at DESC);

-- Parent → child edges. Created at push time from declared parent_experience_ids.
-- One-hop only: CreditAssigner walks one level up, never recurses.
CREATE TABLE experience_edges (
    parent_id UUID NOT NULL REFERENCES experiences(experience_id) ON DELETE CASCADE,
    child_id UUID NOT NULL REFERENCES experiences(experience_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    credit_applied BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (parent_id, child_id)
);
CREATE INDEX idx_edges_pending ON experience_edges(child_id) WHERE credit_applied = FALSE;

-- Q value update log. Audit trail for delayed credit assignment.
CREATE TABLE q_updates (
    update_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    experience_id UUID NOT NULL REFERENCES experiences(experience_id) ON DELETE CASCADE,
    triggered_by_child UUID REFERENCES experiences(experience_id) ON DELETE SET NULL,
    alpha REAL NOT NULL,
    confidence REAL NOT NULL,
    delta_outcome REAL,
    delta_intent REAL,
    delta_execution REAL,
    delta_orchestration REAL,
    delta_expression REAL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_q_updates_experience ON q_updates(experience_id, created_at DESC);

-- Search log feeds visit_count and exploration UCB.
CREATE TABLE search_log (
    log_id BIGSERIAL PRIMARY KEY,
    experience_id UUID NOT NULL REFERENCES experiences(experience_id) ON DELETE CASCADE,
    queried_by UUID REFERENCES agents(agent_id),
    query_text TEXT,
    rank INT,
    similarity REAL,
    score REAL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_search_log_experience ON search_log(experience_id);

-- Append-only audit log. All admin actions land here.
CREATE TABLE audit_log (
    audit_id BIGSERIAL PRIMARY KEY,
    actor TEXT NOT NULL,            -- agent_id or human user
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('agent', 'human', 'system')),
    action TEXT NOT NULL,
    target_id UUID,
    payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_target ON audit_log(target_id);
CREATE INDEX idx_audit_actor ON audit_log(actor, created_at DESC);

-- Seed agent for local dev.
INSERT INTO agents (name, team) VALUES ('dev-local', 'platform')
ON CONFLICT (name) DO NOTHING;
