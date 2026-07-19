"""Platform-side RAG over experience chunks.

The old recall path retrieved whole experience cards. This module indexes
experience fragments and returns a compact context pack that plugins can inject
directly.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import re
import sqlite3
import threading
from collections import Counter
from functools import lru_cache
from typing import Any, Callable

from .embeddings import cosine, embed, from_blob, to_blob
from . import reuse_feedback as reuse_feedback_mod


EMBED_MODEL = "trigram-256"
MAX_CHUNKS_PER_EXPERIENCE = 1
MAX_EXPERIENCE_UNITS = 1024
MAX_UNIT_CHARS = 1200
MAX_TRAJECTORY_SEGMENTS = 2048
MAX_SEGMENT_CHARS = 1800
MAX_SEGMENT_ITEMS = 8
FTS_WEIGHT = 0.07
LEXICAL_WEIGHT = 0.15
VECTOR_WEIGHT = 0.20
QUALITY_WEIGHT = 0.02
ACTION_WEIGHT = 0.24
SITUATION_WEIGHT = 0.22
OUTCOME_WEIGHT = 0.10
FTS_CANDIDATE_LIMIT = 300
VECTOR_CANDIDATE_LIMIT = 300
VECTOR_PREFETCH_LIMIT = 1600
MIN_RECALL_SCORE = 0.24
MIN_VECTOR_ONLY_SIMILARITY = 0.34
STALE_RETRIEVAL_REFRESH_LIMIT = 500

_VECTOR_CACHE_LOCK = threading.RLock()
_VECTOR_INDEX_CACHE: dict[str, dict[str, Any]] = {}

RUNTIME_KEYWORD_STOP = {
    "abort",
    "aborted",
    "auto",
    "background",
    "bash",
    "caveat",
    "command",
    "commands",
    "connected",
    "ctrl",
    "exec_command",
    "generated",
    "interrupt",
    "interrupted",
    "local",
    "local-command-stdout",
    "message",
    "messages",
    "partially",
    "previous",
    "process",
    "processes",
    "purpose",
    "request",
    "respond",
    "running",
    "session",
    "still",
    "task",
    "task-notification",
    "tool",
    "tool-use-id",
    "tools",
    "unless",
    "upload",
    "uploaded",
    "uploads",
    "调用工具",
    "工具返回",
    "工具返",
    "具返回",
    "思考",
    "图片",
}

GENERIC_KEYWORD_STOP = {
    "action",
    "agent",
    "agents",
    "add",
    "analysis",
    "analyze",
    "architecture",
    "args",
    "argument",
    "arguments",
    "arrive",
    "audit",
    "auth",
    "base",
    "batch",
    "case",
    "cases",
    "check",
    "checking",
    "chunk",
    "claude",
    "clipboard",
    "cmd",
    "code",
    "command-args",
    "command-message",
    "command-name",
    "confirm",
    "confirming",
    "config",
    "content",
    "context",
    "copy",
    "count",
    "curl",
    "current",
    "description",
    "develop",
    "detail",
    "details",
    "dev",
    "echo",
    "exactly",
    "execution",
    "expected",
    "exited",
    "exec",
    "explain",
    "file",
    "file_path",
    "find",
    "follow",
    "following",
    "future",
    "generation",
    "given",
    "grep",
    "github",
    "github.com",
    "head",
    "high-level",
    "homedir",
    "include",
    "input",
    "instances",
    "instruction",
    "instructions",
    "inspire",
    "import",
    "https",
    "json",
    "list",
    "login",
    "localhost",
    "logs",
    "model",
    "note",
    "notes",
    "null",
    "observation",
    "operate",
    "original",
    "output",
    "output-file",
    "path",
    "please",
    "private",
    "prompt",
    "prompt_audit",
    "project",
    "provided",
    "public",
    "python3",
    "read",
    "readme.md",
    "repository",
    "resume",
    "review",
    "root",
    "round",
    "script",
    "scripts",
    "sed",
    "seconds",
    "shorter",
    "sleeps",
    "single",
    "status",
    "stricthostkeychecking",
    "step",
    "steps",
    "structure",
    "subagents",
    "summary",
    "successfully",
    "tail",
    "target",
    "test",
    "time",
    "tmp",
    "token",
    "trace",
    "turn",
    "turns",
    "type",
    "unsupported",
    "updated",
    "used",
    "userknownhostsfile",
    "using",
    "video",
    "warning",
    "wall",
    "what",
    "with",
    "viewer",
    "users-tianyiliang",
}

DOMAIN_KEYWORD_BOOST = {
    "agent",
    "api",
    "avgen",
    "avgenbench",
    "授权",
    "bm25",
    "cloudflare",
    "expool",
    "fastapi",
    "hmac",
    "mova",
    "nava",
    "openveo3",
    "qzcli",
    "rag",
    "rerank",
    "resource_spec_price",
    "spec_id",
    "train_job",
    "qz_create_job",
    "飞书",
    "召回",
    "关键词",
    "经验池",
    "检索",
    "测评",
    "评测",
    "启智",
    "项目池",
    "社区池",
    "个人池",
    "切分",
    "摘要",
    "插件",
    "内网",
    "接口",
    "字段",
    "签名",
    "代码",
    "登录",
    "报错",
    "错误",
    "失败",
    "测试",
    "推理",
    "分布式",
    "资源",
    "训练",
    "视频",
    "路径",
    "权限",
}

PHRASE_ANCHORS = {
    "avgen",
    "avgenbench",
    "bm25",
    "cloudflare",
    "expool",
    "fastapi",
    "hmac",
    "mova",
    "nava",
    "openveo3",
    "qzcli",
    "rag",
    "rerank",
    "召回",
    "关键词",
    "经验池",
    "检索",
    "测评",
    "评测",
    "启智",
    "项目池",
    "社区池",
    "个人池",
    "切分",
    "摘要",
    "插件",
    "内网",
}

ALLOWED_PHRASE_PARTS = {
    "prompt",
    "235",
    "moe",
    "scaling",
    "law",
    "signature",
    "domain",
    "recall",
}

QUERY_SYNONYMS = {
    "expool": ("经验池", "experience", "pool"),
    "rag": ("召回", "检索", "chunk", "切分", "子经验"),
    "recall": ("召回", "检索"),
    "chunk": ("切分", "子经验", "片段", "rag"),
    "chunks": ("切分", "子经验", "片段", "rag"),
    "retrieval": ("召回", "检索", "rag"),
    "rerank": ("重排", "rerank"),
    "bm25": ("关键词", "检索"),
    "经验池": ("expool", "experience", "pool"),
    "召回": ("rag", "检索", "recall"),
    "检索": ("rag", "召回", "retrieval"),
    "切分": ("chunk", "子经验", "片段"),
    "子经验": ("chunk", "切分", "片段"),
    "关键词": ("bm25", "keyword"),
    "重排": ("rerank",),
}

GENERIC_PHRASE_PARTNER_STOP = {
    "agent",
    "api",
    "code",
    "error",
    "path",
    "test",
    "tool",
    "tools",
    "total_tokens",
    "tool_uses",
    "tool_use",
    "qzcli_tool",
    "yield_time_ms",
    "max_output_tokens",
    "exit_code",
    "stderr_tail",
    "stdout_tail",
    "x-agent-name",
    "exp_agent_name",
    "exp_agent_secret",
    "username",
    "password",
    "qzcli_username",
    "qzcli_password",
    "all_proxy",
    "http_proxy",
    "https_proxy",
    "codex_thread_id+x",
    "content_block_start",
    "nava_model_path",
    "nava_gpu_id",
    "first_frame_picture",
    "message_delta",
    "port_file",
    "as_completed",
    "first_completed",
    "conda_envs",
    "m_active",
    "subagent_type",
    "per_page",
    "self",
    "代码",
    "测试",
    "错误",
    "失败",
    "接口",
    "字段",
    "资源",
    "视频",
    "路径",
    "登录",
    "分布式",
    "代码和",
    "到的视频",
}

KEYPHRASE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("qzcli spec_id resource_spec_price", ("qzcli", "spec_id", "resource_spec_price")),
    ("qzcli train_job resource_spec_price", ("qzcli", "train_job", "resource_spec_price")),
    ("qzcli qz_create_job spec_id", ("qzcli", "qz_create_job", "spec_id")),
    ("mova moe scaling law", ("mova", "moe", "scaling")),
    ("mova distributed inference evaluation", ("mova", "distributed", "evaluation")),
    ("openveo3 prompt 235", ("openveo3", "prompt", "235")),
    ("openveo3 avgen prompt evaluation", ("openveo3", "avgen", "prompt")),
    ("avgenbench prompt evaluation", ("avgenbench", "prompt")),
    ("experience pool rag recall", ("expool", "rag")),
    ("experience pool bm25 rerank", ("expool", "bm25", "rerank")),
    ("fastapi hmac signature", ("fastapi", "hmac")),
    ("cloudflare intranet domain", ("cloudflare", "domain")),
    ("经验池 rag 召回", ("经验池", "rag", "召回")),
    ("经验池 关键词 检索", ("经验池", "关键词", "检索")),
    ("经验池 session 切分", ("经验池", "session", "切分")),
    ("项目池 好友 共享", ("项目池", "共享")),
)

WEAK_ASCII_KEYWORD_STOP = {
    "able",
    "adequately",
    "after",
    "again",
    "also",
    "always",
    "another",
    "available",
    "aware",
    "before",
    "being",
    "better",
    "called",
    "classifier",
    "complaints",
    "concise",
    "codebase",
    "commonly",
    "could",
    "create",
    "compiled",
    "cache",
    "cached",
    "distinct",
    "during",
    "enough",
    "explicit",
    "followed",
    "found",
    "gist",
    "imported",
    "inputvalidationerror",
    "issue",
    "later",
    "less",
    "maybe",
    "mean",
    "microsoft",
    "coming",
    "naturally",
    "needed",
    "necessary",
    "only",
    "other",
    "passed",
    "permanently",
    "powershell",
    "productive",
    "rather",
    "reflection",
    "same",
    "sbin",
    "should",
    "tests",
    "there",
    "these",
    "those",
    "through",
    "under",
    "want",
    "where",
    "which",
    "while",
    "without",
    "would",
    "yourself",
}

CJK_KEYWORD_STOP = {
    "一个",
    "一下",
    "不用",
    "以及",
    "以及已有",
    "他们",
    "你再",
    "你再看看",
    "其实",
    "其实不用",
    "里面",
    "再看",
    "几个",
    "出来",
    "当前",
    "当前的",
    "怎么",
    "我们",
    "所有",
    "接手",
    "接手下",
    "是否",
    "有没有",
    "现在",
    "现在的",
    "的任务",
    "目录",
    "目录里查",
    "面的任",
    "面的任务",
    "看看",
    "知道",
    "工具返",
    "具返回",
    "的话",
    "的是",
    "直接",
    "这个",
    "这些",
    "还是",
    "还有",
    "那个",
    "那些",
    "里面",
    "需要",
}


SCHEMA = """
CREATE TABLE IF NOT EXISTS rag_chunks (
    chunk_id TEXT PRIMARY KEY,
    experience_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    chunk_type TEXT NOT NULL,
    text TEXT NOT NULL,
    search_text TEXT,
    lexical_terms TEXT,
    turn_start INTEGER,
    turn_end INTEGER,
    meta_json TEXT,
    token_count INTEGER NOT NULL DEFAULT 0,
    quality_score REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(experience_id) REFERENCES experiences(experience_id)
);

CREATE TABLE IF NOT EXISTS rag_vectors (
    chunk_id TEXT PRIMARY KEY,
    vector BLOB NOT NULL,
    model TEXT NOT NULL DEFAULT 'trigram-256',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(chunk_id) REFERENCES rag_chunks(chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_rag_chunks_exp ON rag_chunks(experience_id);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_owner ON rag_chunks(owner);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_type ON rag_chunks(chunk_type);
CREATE INDEX IF NOT EXISTS idx_rag_vectors_model ON rag_vectors(model);

CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts USING fts5(
    chunk_id UNINDEXED,
    text,
    tokenize="unicode61"
);

CREATE TRIGGER IF NOT EXISTS rag_chunks_ai AFTER INSERT ON rag_chunks BEGIN
    INSERT INTO rag_chunks_fts(chunk_id, text)
    VALUES (new.chunk_id, COALESCE(new.search_text, new.text, ''));
END;
CREATE TRIGGER IF NOT EXISTS rag_chunks_au AFTER UPDATE ON rag_chunks BEGIN
    DELETE FROM rag_chunks_fts WHERE chunk_id = old.chunk_id;
    INSERT INTO rag_chunks_fts(chunk_id, text)
    VALUES (new.chunk_id, COALESCE(new.search_text, new.text, ''));
END;
CREATE TRIGGER IF NOT EXISTS rag_chunks_ad AFTER DELETE ON rag_chunks BEGIN
    DELETE FROM rag_chunks_fts WHERE chunk_id = old.chunk_id;
END;
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _ensure_chunk_columns(conn)
    _ensure_experience_provenance_columns(conn)
    _ensure_fts_triggers(conn)
    conn.commit()


def remove_experience_index(conn: sqlite3.Connection, experience_id: str) -> int:
    """Delete all searchable child units for one revoked experience."""

    chunk_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM rag_chunks WHERE experience_id = ?",
            (experience_id,),
        ).fetchone()[0]
    )
    conn.execute(
        "DELETE FROM rag_vectors WHERE chunk_id IN "
        "(SELECT chunk_id FROM rag_chunks WHERE experience_id = ?)",
        (experience_id,),
    )
    conn.execute("DELETE FROM rag_chunks WHERE experience_id = ?", (experience_id,))
    _invalidate_vector_index(conn)
    return chunk_count


def prune_stale_experience_indexes(
    conn: sqlite3.Connection, *, limit: int = 200
) -> tuple[int, int]:
    """Remove indexes whose parent experience is no longer searchable."""

    rows = conn.execute(
        """
        SELECT c.experience_id, COUNT(*) AS chunk_count
        FROM rag_chunks c
        LEFT JOIN experiences e ON e.experience_id = c.experience_id
        WHERE e.experience_id IS NULL
           OR e.review_status NOT IN ('approved', 'auto_approved', 'edited')
           OR e.extraction_status != 'done'
           OR COALESCE(e.revoked, 0) != 0
        GROUP BY c.experience_id
        ORDER BY c.experience_id
        LIMIT ?
        """,
        (max(1, limit),),
    ).fetchall()
    removed_chunks = 0
    with conn:
        for row in rows:
            removed_chunks += remove_experience_index(conn, row["experience_id"])
    return len(rows), removed_chunks


def warm_vector_index(conn: sqlite3.Connection) -> int:
    """Build the process-local vector matrix before the first user query."""
    ensure_schema(conn)
    _, count = _vector_top_signal(conn, embed("experience pool retrieval warmup"), limit=1)
    return count


def _ensure_chunk_columns(conn: sqlite3.Connection) -> None:
    """Backfill metadata columns for older local SQLite databases."""
    existing = {
        (row["name"] if isinstance(row, sqlite3.Row) else row[1])
        for row in conn.execute("PRAGMA table_info(rag_chunks)").fetchall()
    }
    additions = {
        "search_text": "ALTER TABLE rag_chunks ADD COLUMN search_text TEXT",
        "lexical_terms": "ALTER TABLE rag_chunks ADD COLUMN lexical_terms TEXT",
        "turn_start": "ALTER TABLE rag_chunks ADD COLUMN turn_start INTEGER",
        "turn_end": "ALTER TABLE rag_chunks ADD COLUMN turn_end INTEGER",
        "meta_json": "ALTER TABLE rag_chunks ADD COLUMN meta_json TEXT",
    }
    for column, ddl in additions.items():
        if column not in existing:
            conn.execute(ddl)


def _ensure_experience_provenance_columns(conn: sqlite3.Connection) -> None:
    existing = {
        (row["name"] if isinstance(row, sqlite3.Row) else row[1])
        for row in conn.execute("PRAGMA table_info(experiences)").fetchall()
    }
    additions = {
        "source_agent_type": "ALTER TABLE experiences ADD COLUMN source_agent_type TEXT",
        "parent_session_id": "ALTER TABLE experiences ADD COLUMN parent_session_id TEXT",
        "segment_id": "ALTER TABLE experiences ADD COLUMN segment_id TEXT",
        "source_byte_start": "ALTER TABLE experiences ADD COLUMN source_byte_start INTEGER",
        "source_byte_end": "ALTER TABLE experiences ADD COLUMN source_byte_end INTEGER",
        "task_status": "ALTER TABLE experiences ADD COLUMN task_status TEXT",
    }
    for column, ddl in additions.items():
        if column not in existing:
            conn.execute(ddl)


def _ensure_fts_triggers(conn: sqlite3.Connection) -> None:
    """Keep FTS wired to the retrieval text, not the injected context text."""

    rows = {
        row["name"] if isinstance(row, sqlite3.Row) else row[0]: row["sql"] if isinstance(row, sqlite3.Row) else row[1]
        for row in conn.execute(
            """
            SELECT name, sql
            FROM sqlite_master
            WHERE type = 'trigger'
              AND name IN ('rag_chunks_ai', 'rag_chunks_au', 'rag_chunks_ad')
            """
        ).fetchall()
    }
    if (
        "rag_chunks_ai" in rows
        and "rag_chunks_au" in rows
        and "rag_chunks_ad" in rows
        and "search_text" in (rows.get("rag_chunks_ai") or "")
        and "search_text" in (rows.get("rag_chunks_au") or "")
    ):
        return

    conn.executescript(
        """
        DROP TRIGGER IF EXISTS rag_chunks_ai;
        DROP TRIGGER IF EXISTS rag_chunks_au;
        DROP TRIGGER IF EXISTS rag_chunks_ad;
        CREATE TRIGGER rag_chunks_ai AFTER INSERT ON rag_chunks BEGIN
            INSERT INTO rag_chunks_fts(chunk_id, text)
            VALUES (new.chunk_id, COALESCE(new.search_text, new.text, ''));
        END;
        CREATE TRIGGER rag_chunks_au AFTER UPDATE ON rag_chunks BEGIN
            DELETE FROM rag_chunks_fts WHERE chunk_id = old.chunk_id;
            INSERT INTO rag_chunks_fts(chunk_id, text)
            VALUES (new.chunk_id, COALESCE(new.search_text, new.text, ''));
        END;
        CREATE TRIGGER rag_chunks_ad AFTER DELETE ON rag_chunks BEGIN
            DELETE FROM rag_chunks_fts WHERE chunk_id = old.chunk_id;
        END;
        """
    )


def rebuild_experience(conn: sqlite3.Connection, experience_id: str) -> int:
    """Rebuild one experience without holding SQLite's writer during parsing."""

    select_sql = """
        SELECT e.*, a.owner AS agent_owner, a.name AS agent_name
        FROM experiences e
        JOIN agents a ON a.agent_id = e.agent_id
        WHERE e.experience_id = ?
    """
    for attempt in range(2):
        conn.commit()
        row = conn.execute(select_sql, (experience_id,)).fetchone()
        source_version = _rag_source_version(row)
        indexable = bool(
            row is not None
            and row["review_status"] in {"approved", "auto_approved", "edited"}
            and row["extraction_status"] == "done"
            and not _int(row, "revoked")
        )
        chunk_rows: list[tuple[Any, ...]] = []
        vector_rows: list[tuple[Any, ...]] = []
        if indexable and row is not None:
            owner = row["agent_owner"] or row["agent_name"]
            quality = _quality(row)
            for index, chunk in enumerate(_chunks_from_row(row)):
                chunk_type = str(chunk.get("chunk_type") or "unknown")
                text = _clean_text(str(chunk.get("text") or ""))
                if not text:
                    continue
                meta = dict(chunk.get("meta") or {})
                search_text = _retrieval_text(text, meta, chunk_type) or text
                chunk_id = _chunk_id(experience_id, index, chunk_type, text)
                chunk_rows.append(
                    (
                        chunk_id,
                        experience_id,
                        owner,
                        row["agent_id"],
                        chunk_type,
                        text,
                        search_text,
                        _index_terms_json(search_text, text),
                        _optional_int(chunk.get("turn_start")),
                        _optional_int(chunk.get("turn_end")),
                        json.dumps(meta, ensure_ascii=False, sort_keys=True)
                        if meta
                        else None,
                        _token_count(text),
                        quality,
                    )
                )
                vector_rows.append(
                    (chunk_id, to_blob(embed(search_text)), EMBED_MODEL)
                )

        try:
            conn.execute("BEGIN IMMEDIATE")
            latest = conn.execute(select_sql, (experience_id,)).fetchone()
            if _rag_source_version(latest) != source_version:
                conn.rollback()
                if attempt == 0:
                    continue
                return 0
            conn.execute(
                """
                DELETE FROM rag_vectors
                WHERE chunk_id IN (
                    SELECT chunk_id FROM rag_chunks WHERE experience_id = ?
                )
                """,
                (experience_id,),
            )
            conn.execute(
                "DELETE FROM rag_chunks WHERE experience_id = ?",
                (experience_id,),
            )
            if chunk_rows:
                conn.executemany(
                    """
                    INSERT INTO rag_chunks (
                      chunk_id, experience_id, owner, agent_id, chunk_type,
                      text, search_text, lexical_terms, turn_start, turn_end,
                      meta_json, token_count, quality_score
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    chunk_rows,
                )
                conn.executemany(
                    """
                    INSERT INTO rag_vectors (chunk_id, vector, model)
                    VALUES (?, ?, ?)
                    """,
                    vector_rows,
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        _invalidate_vector_index(conn)
        return len(chunk_rows)
    return 0


def _rag_source_version(row: sqlite3.Row | None) -> tuple[Any, ...] | None:
    if row is None:
        return None
    keys = (
        "agent_id",
        "review_status",
        "extraction_status",
        "revoked",
        "trajectory_path",
        "content_fingerprint",
        "turn_count",
        "query",
        "intent_text",
        "script_steps",
        "key_decisions",
        "pitfalls",
        "tool_capabilities",
        "outcome",
        "summary",
        "q_outcome",
        "q_intent",
        "q_execution",
        "q_orchestration",
        "q_expression",
    )
    available = set(row.keys())
    return tuple(row[key] if key in available else None for key in keys)


def backfill_missing_chunks(conn: sqlite3.Connection, *, limit: int = 200) -> int:
    rows = conn.execute(
        """
        SELECT e.experience_id
        FROM experiences e
        LEFT JOIN rag_chunks c ON c.experience_id = e.experience_id
        WHERE c.chunk_id IS NULL
          AND e.review_status IN ('approved', 'auto_approved', 'edited')
          AND e.extraction_status = 'done'
          AND COALESCE(e.revoked, 0) = 0
        ORDER BY e.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    total = 0
    for row in rows:
        total += rebuild_experience(conn, row["experience_id"])
    return total


def backfill_experience_provenance(conn: sqlite3.Connection, *, limit: int = 1000) -> int:
    """Move session parent/segment metadata from JSON sidecars into SQL."""
    ensure_schema(conn)
    rows = conn.execute(
        """
        SELECT experience_id, trajectory_path, task_type, session_id
        FROM experiences
        WHERE source_agent_type IS NULL OR source_agent_type = ''
           OR (
                source_agent_type = 'codex'
                AND parent_session_id IS NULL
                AND (session_id IS NOT NULL OR trajectory_path IS NOT NULL)
           )
        ORDER BY created_at
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    updated = 0
    with conn:
        for row in rows:
            sidecar = _read_trajectory_sidecar(row["trajectory_path"])
            provenance = _trajectory_provenance(sidecar)
            meta = sidecar.get("meta")
            meta = meta if isinstance(meta, dict) else {}
            source_agent_type = str(
                provenance.get("agent_type")
                or _agent_type_from_task_type(str(row["task_type"] or ""))
            )
            conn.execute(
                """
                UPDATE experiences
                SET source_agent_type = ?,
                    session_id = COALESCE(session_id, ?),
                    parent_session_id = COALESCE(parent_session_id, ?),
                    segment_id = COALESCE(segment_id, ?),
                    source_byte_start = COALESCE(source_byte_start, ?),
                    source_byte_end = COALESCE(source_byte_end, ?),
                    task_status = COALESCE(task_status, ?)
                WHERE experience_id = ?
                """,
                (
                    source_agent_type,
                    provenance.get("session_id") or meta.get("session_id"),
                    provenance.get("parent_session_id")
                    or provenance.get("session_id")
                    or meta.get("session_id")
                    or row["session_id"],
                    provenance.get("segment_id"),
                    _optional_int(provenance.get("source_byte_start")),
                    _optional_int(provenance.get("source_byte_end")),
                    provenance.get("task_status"),
                    row["experience_id"],
                ),
            )
            updated += 1
    return updated


def _agent_type_from_task_type(task_type: str) -> str:
    low = (task_type or "").lower()
    for source in ("claude-code", "codex", "cursor", "hermes", "openclaw"):
        if source in low:
            return source
    return "unknown"


def refresh_stale_retrieval_text(conn: sqlite3.Connection, *, limit: int = STALE_RETRIEVAL_REFRESH_LIMIT) -> int:
    """Populate clean retrieval text for chunks created before this index split."""

    ensure_schema(conn)
    rows = conn.execute(
        """
        SELECT chunk_id, chunk_type, text, meta_json, search_text, lexical_terms
        FROM rag_chunks
        WHERE search_text IS NULL OR search_text = ''
           OR lexical_terms IS NULL OR lexical_terms = ''
           OR substr(ltrim(lexical_terms), 1, 1) != '{'
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    if not rows:
        return 0
    updated = 0
    with conn:
        for row in rows:
            meta = _json_obj(row["meta_json"])
            previous_search = str(row["search_text"] or "")
            search_text = previous_search or _retrieval_text(
                row["text"], meta, row["chunk_type"]
            ) or row["text"]
            conn.execute(
                """
                UPDATE rag_chunks
                SET search_text = ?, lexical_terms = ?, updated_at = datetime('now')
                WHERE chunk_id = ?
                """,
                (search_text, _index_terms_json(search_text, row["text"]), row["chunk_id"]),
            )
            if not previous_search:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO rag_vectors (chunk_id, vector, model)
                    VALUES (?, ?, ?)
                    """,
                    (row["chunk_id"], to_blob(embed(search_text)), EMBED_MODEL),
                )
            updated += 1
    _invalidate_vector_index(conn)
    return updated


def context_for_query(
    conn: sqlite3.Connection,
    *,
    viewer_name: str,
    query: str,
    top_k: int = 5,
    task_type: str | None = None,
    scope: str = "auto",
    project: str | None = None,
    record_event: bool = True,
) -> dict[str, Any]:
    from . import community as community_mod
    from . import projects as projects_mod

    ensure_schema(conn)
    viewer = conn.execute(
        "SELECT agent_id, team, owner, name FROM agents WHERE name = ?",
        (viewer_name,),
    ).fetchone()
    if viewer is None:
        raise ValueError(f"unknown agent: {viewer_name}")
    viewer_id = viewer["agent_id"]
    viewer_team = viewer["team"]
    viewer_owner = viewer["owner"] or viewer["name"]

    owner_agent_ids = {
        r["agent_id"]
        for r in conn.execute(
            "SELECT agent_id FROM agents WHERE owner = ? OR (owner IS NULL AND name = ?)",
            (viewer_owner, viewer_owner),
        ).fetchall()
    }
    quota = community_mod.get_quota(conn, viewer_owner)
    community_unlocked = quota.community_unlocked

    raw_scope = scope or "auto"
    requested_scope = _normalize_scope(raw_scope, project)
    project_ref = _project_from_scope(raw_scope, project)
    project_owners: dict[str, bool] = {}
    project_meta: dict[str, Any] | None = None
    if project_ref:
        project_owners = projects_mod.visible_project_owners_for_agent(
            conn, agent_name=viewer_name, project_ref=project_ref
        )
        proj = projects_mod.get_project(conn, project_ref)
        if proj is not None:
            project_meta = {
                "project_id": proj.project_id,
                "slug": proj.slug,
                "name": proj.name,
                "shared_owners": sorted(project_owners),
            }

    want_personal = requested_scope in {"auto", "personal"}
    want_community = requested_scope in {"auto", "community"}
    want_project = requested_scope == "project"

    personal_only = want_personal and not want_community and not want_project
    query_terms = _query_terms(query)
    query_term_profile = _term_profile(query_terms)
    if not _has_retrieval_signal(query, query_terms):
        return {
            "event_id": None,
            "context": "",
            "chunks": [],
            "experiences": [],
            "scope": requested_scope,
            "scope_meta": {
                "viewer": viewer_name,
                "viewer_owner": viewer_owner,
                "project": project_meta,
                "community_unlocked": community_unlocked,
            },
            "quota": quota.to_dict(),
            "community_locked_hint": None
            if community_unlocked
            else f"community pool locked: {quota.hint}",
            "retrieval_meta": {
                "query_terms": query_terms,
                "vector_index_size": 0,
                "prefetched_vectors": 0,
                "acl_candidates": 0,
                "reranked_candidates": 0,
                "accepted_candidates": 0,
                "returned_chunks": 0,
                "max_chunks_per_experience": MAX_CHUNKS_PER_EXPERIENCE,
                "min_score": MIN_RECALL_SCORE,
            },
        }
    fts_signal = _fts_signal(
        conn,
        query_terms,
        agent_ids=owner_agent_ids if personal_only else None,
    )
    qvec = embed(_query_text_for_embedding(query, query_terms))
    vector_signal, vector_index_size = _vector_top_signal(
        conn,
        qvec,
        limit=VECTOR_CANDIDATE_LIMIT if personal_only else VECTOR_PREFETCH_LIMIT,
        agent_ids=owner_agent_ids if personal_only else None,
        include_ids=set(fts_signal),
    )
    where_clauses = [
        "rv.model = ?",
        "e.review_status IN ('approved', 'auto_approved', 'edited')",
        "e.extraction_status = 'done'",
        "COALESCE(e.revoked, 0) = 0",
    ]
    params: list[Any] = [EMBED_MODEL]
    if personal_only:
        if not owner_agent_ids:
            where_clauses.append("0")
        else:
            placeholders = ",".join("?" * len(owner_agent_ids))
            where_clauses.append(f"c.agent_id IN ({placeholders})")
            params.extend(sorted(owner_agent_ids))
    elif requested_scope == "community":
        where_clauses.extend(
            ["COALESCE(e.publish_status, 'private') = 'published'", "e.acl = 'public'"]
        )
    elif requested_scope == "project":
        if not project_owners:
            where_clauses.append("0")
        else:
            placeholders = ",".join("?" * len(project_owners))
            where_clauses.append(f"c.owner IN ({placeholders})")
            params.extend(sorted(project_owners))
    elif requested_scope == "auto":
        access_clauses: list[str] = []
        if owner_agent_ids:
            placeholders = ",".join("?" * len(owner_agent_ids))
            access_clauses.append(f"c.agent_id IN ({placeholders})")
            params.extend(sorted(owner_agent_ids))
        if viewer_team:
            access_clauses.append("e.acl = ?")
            params.append(f"team:{viewer_team}")
        if community_unlocked:
            access_clauses.append(
                "(COALESCE(e.publish_status, 'private') = 'published' AND e.acl = 'public')"
            )
        where_clauses.append("(" + " OR ".join(access_clauses) + ")" if access_clauses else "0")
    if task_type:
        where_clauses.append("e.task_type = ?")
        params.append(task_type)
    candidate_ids = sorted(set(fts_signal) | set(vector_signal))
    if candidate_ids:
        placeholders = ",".join("?" * len(candidate_ids))
        where_clauses.append(f"c.chunk_id IN ({placeholders})")
        params.extend(candidate_ids)
    else:
        where_clauses.append("0")
    where_sql = " AND ".join(where_clauses)
    rows = conn.execute(
        f"""
        SELECT c.chunk_id, c.experience_id, c.owner, c.agent_id, c.chunk_type,
               c.text, COALESCE(c.search_text, c.text) AS search_text,
               c.lexical_terms,
               c.turn_start, c.turn_end, c.meta_json,
               c.token_count, c.quality_score,
               rv.vector,
               e.task_type, e.acl, e.sensitivity, e.created_at,
               e.session_id, e.parent_session_id,
               COALESCE(e.publish_status, 'private') AS publish_status,
               a.name AS agent_name, a.team AS agent_team, a.owner AS agent_owner
        FROM rag_chunks c
        JOIN rag_vectors rv ON rv.chunk_id = c.chunk_id
        JOIN experiences e ON e.experience_id = c.experience_id
        JOIN agents a ON a.agent_id = e.agent_id
        WHERE {where_sql}
        """,
        params,
    ).fetchall()

    # SQLite has no native ANN extension in this deployment. Scan only the
    # ACL-filtered vectors, retain a bounded semantic top-N, then union exact
    # FTS hits before running the comparatively expensive lexical scorer.
    # Previously lexical tokenization ran over every chunk and FTS hits
    # accidentally excluded all vector-only candidates.
    accessible: list[tuple[float, sqlite3.Row, str]] = []
    for row in rows:
        source = _row_source(
            row,
            viewer_id=viewer_id,
            viewer_team=viewer_team,
            owner_agent_ids=owner_agent_ids,
            want_personal=want_personal,
            want_community=want_community,
            want_project=want_project,
            community_unlocked=community_unlocked,
            project_owners=project_owners,
        )
        if source is None:
            continue
        cos = vector_signal.get(row["chunk_id"])
        if cos is None:
            cos = cosine(qvec, from_blob(row["vector"]))
        accessible.append((cos, row, source))

    vector_hits = heapq.nlargest(
        min(VECTOR_CANDIDATE_LIMIT, len(accessible)),
        accessible,
        key=lambda item: item[0],
    )
    candidate_rows = {item[1]["chunk_id"]: item for item in vector_hits}
    if fts_signal:
        for item in accessible:
            if item[1]["chunk_id"] in fts_signal:
                candidate_rows[item[1]["chunk_id"]] = item

    candidates: list[dict[str, Any]] = []
    query_outcome_preference = _query_outcome_preference(query)
    for cos, row, source in candidate_rows.values():
        fts = fts_signal.get(row["chunk_id"], 0.0)
        term_map = _decode_lexical_term_map(row["lexical_terms"])
        term_profiles = _decode_lexical_profile(row["lexical_terms"])
        lexical, coverage = _lexical_metrics(
            query_terms,
            row["search_text"],
            doc_terms=term_map.get("all"),
            doc_profile=term_profiles.get("all"),
            query_profile=query_term_profile,
        )
        action_lexical = _lexical_metrics(
            query_terms,
            "" if "action" in term_map else _chunk_action_text(row["text"]),
            doc_terms=term_map.get("action"),
            doc_profile=term_profiles.get("action"),
            query_profile=query_term_profile,
        )[0]
        situation_lexical = _lexical_metrics(
            query_terms,
            ""
            if "situation" in term_map
            else _chunk_labeled_text(row["text"], "situation"),
            doc_terms=term_map.get("situation"),
            doc_profile=term_profiles.get("situation"),
            query_profile=query_term_profile,
        )[0]
        outcome_lexical = _lexical_metrics(
            query_terms,
            ""
            if "outcome" in term_map
            else _chunk_labeled_text(row["text"], "outcome"),
            doc_terms=term_map.get("outcome"),
            doc_profile=term_profiles.get("outcome"),
            query_profile=query_term_profile,
        )[0]
        outcome_alignment = _outcome_status_alignment_for_preference(
            query_outcome_preference, row["chunk_type"]
        )
        keyword = max(fts, lexical)
        quality = float(row["quality_score"] or 0.0)
        score = (
            (VECTOR_WEIGHT * cos)
            + (FTS_WEIGHT * fts)
            + (LEXICAL_WEIGHT * lexical)
            + (QUALITY_WEIGHT * quality)
            + (ACTION_WEIGHT * action_lexical)
            + (SITUATION_WEIGHT * situation_lexical)
            + (OUTCOME_WEIGHT * outcome_lexical)
            + outcome_alignment
            + _chunk_type_bonus(row["chunk_type"], keyword, cos)
            + _keyword_coverage_bonus(query_terms, coverage)
        )
        candidates.append(
            {
                "chunk_id": row["chunk_id"],
                "experience_id": row["experience_id"],
                "chunk_type": row["chunk_type"],
                "text": row["text"],
                "retrieval_signature": hashlib.sha1(
                    row["search_text"].encode("utf-8")
                ).hexdigest(),
                "turn_start": row["turn_start"],
                "turn_end": row["turn_end"],
                "meta_json": row["meta_json"],
                "token_count": row["token_count"],
                "similarity": cos,
                "keyword": keyword,
                "fts": fts,
                "lexical": lexical,
                "coverage": coverage,
                "action_lexical": action_lexical,
                "situation_lexical": situation_lexical,
                "outcome_lexical": outcome_lexical,
                "outcome_alignment": outcome_alignment,
                "quality": quality,
                "score": score,
                "source": source,
                "owner": row["owner"],
                "agent_name": row["agent_name"],
                "team": row["agent_team"],
                "task_type": row["task_type"],
                "sensitivity": row["sensitivity"],
                "created_at": row["created_at"],
                "session_id": row["session_id"],
                "parent_session_id": row["parent_session_id"] or row["session_id"],
            }
        )
    candidates.sort(key=lambda item: item["score"], reverse=True)

    selected: list[dict[str, Any]] = []
    per_exp: dict[str, int] = {}
    seen_parent_sessions: set[str] = set()
    seen_retrieval_signatures: set[str] = set()
    seen_action_signatures: set[str] = set()
    for item in candidates:
        if not _candidate_is_relevant(item):
            continue
        count = per_exp.get(item["experience_id"], 0)
        if count >= MAX_CHUNKS_PER_EXPERIENCE:
            continue
        parent_key = str(item.get("parent_session_id") or item["experience_id"])
        if parent_key in seen_parent_sessions:
            continue
        signature = str(item.get("retrieval_signature") or "")
        if signature and signature in seen_retrieval_signatures:
            continue
        action_signature = _action_signature(item["text"], item["chunk_type"])
        if action_signature and action_signature in seen_action_signatures:
            continue
        selected.append(item)
        per_exp[item["experience_id"]] = count + 1
        seen_parent_sessions.add(parent_key)
        if signature:
            seen_retrieval_signatures.add(signature)
        if action_signature:
            seen_action_signatures.add(action_signature)
        if len(selected) >= max(1, top_k):
            break

    for item in selected:
        item.pop("retrieval_signature", None)
        item["meta"] = _json_obj(item.pop("meta_json", None))
    experiences = _experience_summaries(conn, [c["experience_id"] for c in selected])
    provenance_by_experience = {item["experience_id"]: item for item in experiences}
    for item in selected:
        experience = provenance_by_experience.get(item["experience_id"], {})
        meta = dict(item.get("meta") or {})
        for key in (
            "session_id",
            "source_agent_type",
            "parent_session_id",
            "segment_id",
            "source_byte_start",
            "source_byte_end",
            "task_status",
        ):
            value = experience.get(key)
            if value is not None and value != "":
                meta.setdefault(key, value)
        item["meta"] = meta
    event_id: str | None = None
    if selected and record_event:
        ids = sorted({c["experience_id"] for c in selected})
        conn.execute(
            f"UPDATE experiences SET visit_count = visit_count + 1 WHERE experience_id IN ({','.join('?' * len(ids))})",
            ids,
        )
        for rank, item in enumerate(selected):
            conn.execute(
                """
                INSERT INTO search_log (experience_id, queried_by, query_text, rank, similarity, score)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item["experience_id"],
                    viewer_id,
                    query,
                    rank,
                    item["similarity"],
                    item["score"],
                ),
            )
        event_id = reuse_feedback_mod.record_reuse_event(
            conn,
            viewer_agent_id=viewer_id,
            viewer_name=viewer_name,
            query=query,
            chunks=selected,
            scope=requested_scope,
            top_k=max(1, top_k),
            task_type=task_type,
            project_ref=project_ref,
        )
        conn.commit()

    return {
        "event_id": event_id,
        "context": _context_text(selected),
        "chunks": selected,
        "experiences": experiences,
        "scope": requested_scope,
        "scope_meta": {
            "viewer": viewer_name,
            "viewer_owner": viewer_owner,
            "project": project_meta,
            "community_unlocked": community_unlocked,
        },
        "quota": quota.to_dict(),
        "community_locked_hint": None
        if community_unlocked
        else f"community pool locked: {quota.hint}",
        "retrieval_meta": {
            "query_terms": query_terms[:32],
            "vector_index_size": vector_index_size,
            "prefetched_vectors": len(vector_signal),
            "acl_candidates": len(accessible),
            "reranked_candidates": len(candidate_rows),
            "accepted_candidates": sum(1 for item in candidates if _candidate_is_relevant(item)),
            "returned_chunks": len(selected),
            "max_chunks_per_experience": MAX_CHUNKS_PER_EXPERIENCE,
            "min_score": MIN_RECALL_SCORE,
        },
    }


def _normalize_scope(scope: str, project: str | None) -> str:
    raw = (scope or "auto").strip()
    if raw.startswith("project:") or project:
        return "project"
    if raw in {"auto", "personal", "community", "project"}:
        return raw
    return "auto"


def _project_from_scope(scope: str, project: str | None) -> str | None:
    if project:
        return project
    if scope.startswith("project:"):
        return scope.split(":", 1)[1]
    return None


def _row_source(
    row: sqlite3.Row,
    *,
    viewer_id: str,
    viewer_team: str,
    owner_agent_ids: set[str],
    want_personal: bool,
    want_community: bool,
    want_project: bool,
    community_unlocked: bool,
    project_owners: dict[str, bool],
) -> str | None:
    is_owner_row = row["agent_id"] in owner_agent_ids
    is_published = row["publish_status"] == "published" and row["acl"] == "public"
    if want_personal and is_owner_row:
        return "personal"
    if want_project and row["owner"] in project_owners:
        include_high = project_owners[row["owner"]]
        if row["sensitivity"] == "high" and not include_high:
            return None
        return "project"
    if want_community and is_published and community_unlocked and not is_owner_row:
        return "community"
    if want_personal and _legacy_can_read(
        viewer_id, viewer_team, row["agent_id"], row["acl"], is_published=is_published
    ):
        return "personal"
    return None


def _legacy_can_read(
    viewer_id: str,
    viewer_team: str,
    owner_agent_id: str,
    acl: str,
    *,
    is_published: bool,
) -> bool:
    if acl in {"public", "org"}:
        return is_published
    if acl == "private":
        return viewer_id == owner_agent_id
    if acl.startswith("team:"):
        return acl.split(":", 1)[1] == viewer_team
    return False


def _chunks_from_row(row: sqlite3.Row) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    query = _strip_runtime_noise(row["query"] or "")
    intent = _strip_runtime_noise(row["intent_text"] or "")
    if query or intent:
        chunks.append(_chunk("intent", f"Intent: {intent}\nQuery: {query}".strip()))
    steps = _json_list(row["script_steps"])
    if steps:
        lines = []
        for idx, item in enumerate(steps[:12], start=1):
            lines.append(f"{idx}. {_item_text(item)}")
        chunks.append(_chunk("steps", "Steps:\n" + "\n".join(lines)))
    decisions = _json_list(row["key_decisions"])
    if decisions:
        chunks.append(_chunk("decisions", "Key decisions:\n" + "\n".join(f"- {_item_text(x)}" for x in decisions[:10])))
    pitfalls = _json_list(row["pitfalls"])
    if pitfalls:
        chunks.append(_chunk("pitfalls", "Pitfalls:\n" + "\n".join(f"- {_item_text(x)}" for x in pitfalls[:10])))
    capabilities = _json_list(row["tool_capabilities"])
    if capabilities:
        chunks.append(_chunk("tools", "Tool capabilities:\n" + "\n".join(f"- {_item_text(x)}" for x in capabilities[:10])))
    outcome = row["outcome"] or row["summary"] or ""
    if outcome:
        chunks.append(_chunk("outcome", f"Outcome: {outcome}"))
    chunks.extend(_trajectory_segment_chunks(row["trajectory_path"]))
    return chunks


def _chunk(
    chunk_type: str,
    text: str,
    *,
    turn_start: int | None = None,
    turn_end: int | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "chunk_type": chunk_type,
        "text": text,
        "turn_start": turn_start,
        "turn_end": turn_end,
        "meta": dict(meta or {}),
    }


def _trajectory_segment_chunks(path: str | None) -> list[dict[str, Any]]:
    sidecar = _read_trajectory_sidecar(path)
    trajectory = sidecar.get("trajectory")
    if not isinstance(trajectory, list):
        return []

    provenance = _trajectory_provenance(sidecar)
    segments = _trajectory_segments(trajectory)
    chunks: list[dict[str, Any]] = []
    chunks.extend(_experience_unit_chunks(trajectory, provenance=provenance))
    selected_segments = _select_timeline_items(
        segments, MAX_TRAJECTORY_SEGMENTS
    )
    for original_index, segment in selected_segments:
        idx = original_index + 1
        text = _format_trajectory_segment(segment, idx)
        if not text:
            continue
        chunks.append(
            _chunk(
                "trajectory_segment",
                text,
                turn_start=segment["turn_start"],
                turn_end=segment["turn_end"],
                meta={
                    **provenance,
                    "source": "trajectory",
                    "segment_index": idx,
                    "roles": segment["roles"],
                    "tool_names": segment["tool_names"],
                    "keywords": segment["keywords"],
                    "keyphrases": segment.get("keyphrases") or [],
                },
            )
        )
    overview = _trajectory_overview(segments)
    if overview:
        chunks.append(
            _chunk(
                "trajectory_overview",
                overview,
                turn_start=segments[0]["turn_start"] if segments else None,
                turn_end=segments[-1]["turn_end"] if segments else None,
                meta={**provenance, "source": "trajectory", "segment_count": len(segments)},
            )
        )
    return chunks


def _experience_unit_chunks(
    trajectory: list[Any],
    *,
    provenance: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    units = _experience_units(trajectory)
    chunks: list[dict[str, Any]] = []
    selected_units = _select_timeline_items(
        units,
        MAX_EXPERIENCE_UNITS,
        priority=lambda item: item.get("status") == "failure",
    )
    for original_index, unit in selected_units:
        idx = original_index + 1
        if unit["status"] not in {"success", "failure"}:
            continue
        text = _format_experience_unit(unit, idx)
        if not text:
            continue
        status = unit["status"]
        chunk_type = "do_unit" if status == "success" else "dont_unit"
        chunks.append(
            _chunk(
                chunk_type,
                text,
                turn_start=unit["turn_start"],
                turn_end=unit["turn_end"],
                meta={
                    **(provenance or {}),
                    "source": "trajectory",
                    "unit_index": idx,
                    "unit_status": status,
                    "tool_name": unit["tool_name"],
                    "action_kind": unit["action_kind"],
                    "keywords": unit["keywords"],
                    "keyphrases": unit["keyphrases"],
                },
            )
        )
    return chunks


def _select_timeline_items(
    items: list[dict[str, Any]],
    limit: int,
    *,
    priority: Callable[[dict[str, Any]], bool] | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    """Keep timeline coverage when a pathological session exceeds its budget.

    Current long sessions fit under the generous limits above. For future
    sessions that do not, retain all high-value items (notably failures) and
    distribute the remaining slots from the beginning through the end instead
    of silently indexing only the first part of the trajectory.
    """

    if limit <= 0 or not items:
        return []
    indexed = list(enumerate(items))
    if len(indexed) <= limit:
        return indexed

    priority_indices = [
        idx for idx, item in indexed if priority is not None and priority(item)
    ]
    if len(priority_indices) >= limit:
        selected_indices = set(_evenly_spaced(priority_indices, limit))
    else:
        selected_indices = set(priority_indices)
        remaining_indices = [idx for idx, _ in indexed if idx not in selected_indices]
        selected_indices.update(
            _evenly_spaced(remaining_indices, limit - len(selected_indices))
        )
    return [(idx, items[idx]) for idx in sorted(selected_indices)]


def _evenly_spaced(indices: list[int], count: int) -> list[int]:
    if count <= 0 or not indices:
        return []
    if count >= len(indices):
        return list(indices)
    if count == 1:
        return [indices[len(indices) // 2]]
    last = len(indices) - 1
    return [indices[(slot * last) // (count - 1)] for slot in range(count)]


def _trajectory_provenance(sidecar: dict[str, Any]) -> dict[str, Any]:
    meta = sidecar.get("meta")
    meta = meta if isinstance(meta, dict) else {}
    extra = meta.get("extra")
    extra = extra if isinstance(extra, dict) else {}
    out: dict[str, Any] = {}
    for source_key, output_key in (
        ("parent_session_id", "parent_session_id"),
        ("segment_id", "segment_id"),
        ("codex_turn_id", "segment_id"),
        ("byte_start", "source_byte_start"),
        ("byte_end", "source_byte_end"),
        ("task_status", "task_status"),
    ):
        value = extra.get(source_key)
        if value is not None and value != "":
            out[output_key] = value
    session_id = meta.get("session_id")
    if session_id:
        out["session_id"] = str(session_id)
    agent_type = meta.get("agent_type")
    if agent_type:
        out["agent_type"] = str(agent_type)
    return out


def _experience_units(trajectory: list[Any]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    current_context = ""
    pending: list[dict[str, Any]] = []

    for turn_index, raw_turn in enumerate(trajectory):
        if not isinstance(raw_turn, dict):
            continue
        role = str(raw_turn.get("role") or "").lower()
        if role in {"system", "developer"}:
            continue
        text = _clean_turn_text(_turn_text(raw_turn))
        if role == "user":
            if text and not _is_trivial_user_text(text):
                current_context = text[:700]
            continue

        if role == "assistant":
            actions = _tool_actions_from_turn(raw_turn)
            if actions:
                for action in actions:
                    pending.append(
                        {
                            "turn_start": turn_index,
                            "turn_end": turn_index,
                            "context": current_context,
                            "action": action["text"],
                            "tool_name": action["name"],
                            "action_kind": action.get("kind") or "tool_call",
                            "tool_id": action.get("id") or "",
                        }
                    )
            elif text and current_context and _looks_like_outcome(text) and pending:
                # Assistant summaries often carry the success/failure signal
                # after one or more tool results.
                pending[-1]["assistant_outcome"] = text
            continue

        if role == "tool" and pending:
            outcome = text or _clean_turn_text(_content_text(raw_turn))
            result_id = str(raw_turn.get("tool_result_for") or "") or _tool_result_id(outcome)
            action = _pop_pending_action(pending, result_id)
            merged_outcome = " ".join(
                item
                for item in (outcome, action.get("assistant_outcome", ""))
                if item
            )
            unit = _make_experience_unit(action, merged_outcome, turn_index)
            if unit is not None:
                units.append(unit)

    # Keep dangling tool calls as unknown units. They are still useful for
    # API-name retrieval, but do not enter DO / DO NOT.
    for action in pending[: max(0, MAX_EXPERIENCE_UNITS - len(units))]:
        unit = _make_experience_unit(action, "", int(action["turn_end"]))
        if unit is not None:
            units.append(unit)
    return units


def _make_experience_unit(
    action: dict[str, Any],
    outcome: str,
    turn_end: int,
) -> dict[str, Any] | None:
    context = _clean_turn_text(str(action.get("context") or ""))
    action_text = _clean_turn_text(str(action.get("action") or ""))
    outcome = _clean_turn_text(outcome)
    signal = " ".join((context, action_text, outcome))
    if not action_text or _is_trivial_text(signal):
        return None
    tool_name = str(action.get("tool_name") or "tool")
    status = _outcome_status_for_tool(tool_name, outcome or action_text)
    if _is_low_value_unit(tool_name, action_text, outcome, status):
        return None
    keywords = _keyword_terms(signal)
    keyphrases = _keyphrase_terms(signal, keywords)
    return {
        "turn_start": _optional_int(action.get("turn_start")) or turn_end,
        "turn_end": turn_end,
        "context": context,
        "action": action_text,
        "outcome": outcome,
        "status": status,
        "tool_name": tool_name,
        "action_kind": str(action.get("action_kind") or "tool_call"),
        "keywords": keywords,
        "keyphrases": keyphrases,
    }


def _format_experience_unit(unit: dict[str, Any], unit_index: int) -> str:
    status = unit["status"]
    label = "DO" if status == "success" else ("DO NOT" if status == "failure" else "CHECK")
    lines = [
        f"Experience unit {unit_index} ({label}, turns {unit['turn_start']}-{unit['turn_end']})",
        f"Situation: {_situation_summary(unit['context'], unit['tool_name'])}",
        f"Action: {unit['action']}",
    ]
    if unit["outcome"]:
        lines.append(f"Outcome: {unit['outcome']}")
    if status == "failure":
        lines.append("Use: avoid repeating this action in the same situation; inspect the error first.")
    elif status == "success":
        lines.append("Use: imitate this action when the current situation matches.")
    else:
        lines.append("Use: treat as a candidate action; verify against current state before reuse.")
    if unit["keywords"]:
        lines.append("Keywords: " + ", ".join(unit["keywords"][:24]))
    if unit["keyphrases"]:
        lines.append("Keyphrases: " + "; ".join(unit["keyphrases"][:12]))
    return "\n".join(lines)[:MAX_UNIT_CHARS]


def _situation_summary(context: str, tool_name: str) -> str:
    context = _clip(context, 360)
    if context:
        return f"In a task like: {context}; before calling {tool_name}."
    return f"Before calling {tool_name}."


def _read_trajectory_sidecar(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _trajectory_segments(trajectory: list[Any]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def finish() -> None:
        nonlocal current
        if current is None:
            return
        signal = " ".join(
            [current["task"], *current["user_context"], *current["work"], *current["tools"], *current["outcomes"]]
        )
        if _is_trivial_text(signal):
            current = None
            return
        current["roles"] = sorted(current["roles"])
        current["tool_names"] = sorted(current["tool_names"])
        current["summary"] = _segment_summary(current)
        keyword_source = _segment_keyword_source(current)
        current["keywords"] = _keyword_terms(keyword_source)
        current["keyphrases"] = _keyphrase_terms(keyword_source, current["keywords"])
        segments.append(current)
        current = None

    for turn_index, raw_turn in enumerate(trajectory):
        if not isinstance(raw_turn, dict):
            continue
        role = str(raw_turn.get("role") or "").lower()
        if role in {"system", "developer"}:
            continue
        text = _clean_turn_text(_turn_text(raw_turn))
        if not text:
            continue

        if role == "user":
            if _is_trivial_user_text(text):
                if current is not None and not _is_trivial_text(text):
                    _append_segment_item(current, "user_context", text)
                    current["turn_end"] = turn_index
                continue
            finish()
            current = {
                "turn_start": turn_index,
                "turn_end": turn_index,
                "task": text[:520],
                "user_context": [],
                "work": [],
                "tools": [],
                "outcomes": [],
                "roles": {"user"},
                "tool_names": set(),
                "keywords": [],
                "keyphrases": [],
                "summary": "",
            }
            continue

        if current is None:
            continue
        current["turn_end"] = turn_index
        current["roles"].add(role or "unknown")
        for name in _tool_names(raw_turn):
            current["tool_names"].add(name)
        if role == "assistant":
            key = "outcomes" if _looks_like_outcome(text) else "work"
            _append_segment_item(current, key, text)
        elif role == "tool":
            _append_segment_item(current, "tools", text)
        else:
            _append_segment_item(current, "work", text)

    finish()
    return segments


def _append_segment_item(segment: dict[str, Any], key: str, text: str) -> None:
    items = segment[key]
    if len(items) >= MAX_SEGMENT_ITEMS:
        return
    text = _clip(text, 420)
    if text and text not in items:
        items.append(text)


def _format_trajectory_segment(segment: dict[str, Any], segment_index: int) -> str:
    lines = [
        f"Trajectory segment {segment_index} (turns {segment['turn_start']}-{segment['turn_end']})",
        f"Summary: {segment.get('summary') or segment['task']}",
        f"Task: {segment['task']}",
    ]
    _add_bullets(lines, "User context", segment["user_context"], limit=3)
    _add_bullets(lines, "Work performed", segment["work"], limit=5)
    _add_bullets(lines, "Tool signals", segment["tools"], limit=5)
    _add_bullets(lines, "Outcome", segment["outcomes"][-3:], limit=3)
    if segment["tool_names"]:
        lines.append("Tools: " + ", ".join(segment["tool_names"][:12]))
    if segment["keywords"]:
        lines.append("Keywords: " + ", ".join(segment["keywords"][:24]))
    if segment.get("keyphrases"):
        lines.append("Keyphrases: " + "; ".join(segment["keyphrases"][:12]))
    text = "\n".join(lines)
    return text[:MAX_SEGMENT_CHARS]


def _trajectory_overview(segments: list[dict[str, Any]]) -> str:
    if not segments:
        return ""
    tasks = [str(s.get("summary") or s["task"]) for s in segments[:12] if s.get("task")]
    if not tasks:
        return ""
    lines = [f"Trajectory overview: {len(segments)} searchable task segments."]
    for idx, task in enumerate(tasks, start=1):
        lines.append(f"{idx}. {_clip(task, 180)}")
    return "\n".join(lines)


def _add_bullets(lines: list[str], label: str, items: list[str], *, limit: int) -> None:
    clean = [_clip(item, 360) for item in items[:limit] if item]
    if not clean:
        return
    lines.append(label + ":")
    lines.extend(f"- {item}" for item in clean)


def _turn_text(turn: dict[str, Any]) -> str:
    parts: list[str] = []
    content = turn.get("content")
    if content is not None:
        parts.append(_content_text(content))
    for call in turn.get("tool_calls") or []:
        parts.append(_tool_call_text(call))
    return "\n".join(p for p in parts if p)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                typ = str(item.get("type") or "")
                if typ in {"text", "input_text", "output_text"} and item.get("text"):
                    parts.append(str(item.get("text")))
                elif typ in {"tool_use", "function_call"}:
                    parts.append(_tool_call_text(item))
                elif typ in {"tool_result", "function_call_output"}:
                    parts.append(_content_text(item.get("content") or item.get("text") or ""))
                elif item.get("content") is not None:
                    parts.append(_content_text(item.get("content")))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    if isinstance(content, dict):
        if content.get("text"):
            return str(content.get("text"))
        return _compact_payload(content)
    return str(content)


def _tool_call_text(call: Any) -> str:
    if not isinstance(call, dict):
        return str(call)
    function = call.get("function")
    function = function if isinstance(function, dict) else {}
    name = (
        call.get("name")
        or call.get("tool_name")
        or function.get("name")
        or call.get("type")
        or "tool"
    )
    payload = (
        call.get("input")
        or call.get("arguments")
        or function.get("arguments")
        or call.get("content")
        or {}
    )
    compact = _compact_payload(payload)
    return f"Tool {name}: {compact}" if compact else f"Tool {name}"


def _tool_actions_from_turn(turn: dict[str, Any]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for call in turn.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        name = _tool_name(call)
        actions.append({"name": name, "text": _tool_call_text(call), "id": _tool_id(call), "kind": "tool_call"})
    content = turn.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") in {"tool_use", "function_call"}:
                name = _tool_name(item)
                actions.append({"name": name, "text": _tool_call_text(item), "id": _tool_id(item), "kind": "tool_call"})
    elif isinstance(content, str):
        actions.extend(_textual_tool_actions(content))
    return actions


def _textual_tool_actions(text: str) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if "调用工具" not in (text or ""):
        return actions
    marker = re.compile(
        r"(?:🔧\s*)?调用工具:\s*([A-Za-z_][A-Za-z0-9_.:-]*)\s*(?:\((?:id=)?([A-Za-z0-9_-]+)\))?",
        flags=re.I,
    )
    matches = list(marker.finditer(text))
    for idx, match in enumerate(matches):
        name = match.group(1)
        tool_id = match.group(2) or ""
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[start:end]
        payload = _textual_tool_payload(block)
        compact = _compact_payload(payload) if payload is not None else _clip(_strip_summary_noise(block), 420)
        action_text = f"Tool {name}: {compact}" if compact else f"Tool {name}"
        actions.append({"name": name, "text": action_text, "id": tool_id, "kind": "text_tool_call"})
    return actions


def _textual_tool_payload(block: str) -> Any:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", block or "", flags=re.I | re.S)
    if fenced:
        raw = fenced.group(1).strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return None


def _tool_id(call: dict[str, Any]) -> str:
    return str(call.get("id") or call.get("tool_use_id") or call.get("call_id") or "")


def _tool_result_id(text: str) -> str:
    match = re.search(r"(?:工具返回|tool result|tool_use_id|id)\s*(?:[:=]|\()\s*(?:id=)?([A-Za-z0-9_-]+)", text or "", flags=re.I)
    if match:
        return match.group(1).rstrip(")")
    return ""


def _pop_pending_action(pending: list[dict[str, Any]], tool_id: str) -> dict[str, Any]:
    if tool_id:
        for idx, item in enumerate(pending):
            if item.get("tool_id") == tool_id:
                return pending.pop(idx)
    return pending.pop(0)


def _tool_name(call: dict[str, Any]) -> str:
    function = call.get("function")
    function = function if isinstance(function, dict) else {}
    return str(
        call.get("name")
        or call.get("tool_name")
        or function.get("name")
        or call.get("type")
        or "tool"
    )


def _compact_payload(payload: Any) -> str:
    if isinstance(payload, str):
        return _clip(_strip_large_blobs(payload), 520)
    if isinstance(payload, dict):
        preferred = []
        for key in (
            "cmd",
            "command",
            "file_path",
            "path",
            "query",
            "pattern",
            "url",
            "description",
            "prompt",
        ):
            if key in payload and payload[key]:
                preferred.append(f"{key}={_clip(str(payload[key]), 220)}")
        if preferred:
            return "; ".join(preferred)
    try:
        return _clip(json.dumps(payload, ensure_ascii=False, sort_keys=True), 520)
    except TypeError:
        return _clip(str(payload), 520)


def _tool_names(turn: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for call in turn.get("tool_calls") or []:
        if isinstance(call, dict):
            names.append(_tool_name(call))
    content = turn.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") in {"tool_use", "function_call"}:
                names.append(_tool_name(item))
    return _dedupe(names)


def _clean_turn_text(text: str) -> str:
    text = _extract_wrapped_task_text(text or "")
    text = _strip_exec_result_envelope(text)
    text = _strip_index_noise(_strip_large_blobs(_strip_runtime_noise(text)))
    text = " ".join(text.split())
    if len(text) < 2 or _is_runtime_notice(text):
        return ""
    return text


def _extract_wrapped_task_text(text: str) -> str:
    objective = re.search(
        r"<objective>\s*(.*?)\s*</objective>", text or "", flags=re.I | re.S
    )
    if objective:
        return objective.group(1).strip()
    low = (text or "").lower().lstrip()
    if low.startswith("this session is being continued from a previous conversation"):
        match = re.search(
            r"current/active request.*?:\s*\*{0,2}(.*?)\*{0,2}(?:\n|the user said:)",
            text,
            flags=re.I | re.S,
        )
        if match:
            return match.group(1).strip(" *\n")
    return text


def _strip_exec_result_envelope(text: str) -> str:
    text = re.sub(r"\bChunk ID:\s*\S+", " ", text or "", flags=re.I)
    text = re.sub(r"\bWall time:\s*[0-9.]+\s*seconds?", " ", text, flags=re.I)
    text = re.sub(r"\bOriginal token count:\s*\d+", " ", text, flags=re.I)
    text = re.sub(r"\b(?:Final output|Output):\s*", " ", text, flags=re.I)
    return text


def _strip_large_blobs(text: str) -> str:
    text = re.sub(r"data:[^;\\s]+;base64,[A-Za-z0-9+/=]{80,}", "[data-url omitted]", text)
    text = re.sub(r"[A-Za-z0-9+/=]{600,}", "[large blob omitted]", text)
    return text


def _is_runtime_notice(text: str) -> bool:
    low = text.lower()
    return (
        "connected to experience pool" in low
        or "auto-upload" in low
        or "tasks in this session will" in low
        or "not logged in" in low
        or "local-command-caveat" in low
        or "messages below were generated by the user while running local commands" in low
        or "do not respond to these messages" in low
        or "<turn_aborted>" in low
        or "request interrupted by user" in low
        or "user interrupted the previous turn" in low
        or "unified exec processes may still be running" in low
    )


def _is_trivial_user_text(text: str) -> bool:
    norm = re.sub(r"\s+", "", text.lower().strip("。.!?？！，,`\"'"))
    trivial = {
        "hi",
        "hello",
        "在不在",
        "在吗",
        "继续",
        "好的",
        "好",
        "ok",
        "1",
        "2",
        "3",
        "yes",
        "no",
    }
    if norm in trivial:
        return True
    return len(norm) <= 2 and not re.search(r"[a-z]{3,}|[\u4e00-\u9fff]{3,}", norm)


def _is_trivial_text(text: str) -> bool:
    cleaned = _clean_turn_text(text)
    return not cleaned or _is_trivial_user_text(cleaned)


def _looks_like_outcome(text: str) -> bool:
    low = text.lower()
    return any(
        marker in low
        for marker in (
            "已完成",
            "完成了",
            "done",
            "implemented",
            "fixed",
            "verified",
            "tests passed",
            "校验",
            "验证",
            "测试通过",
        )
    )


def _outcome_status(text: str) -> str:
    low = (text or "").lower()
    hard_failure_markers = (
        "traceback",
        "assertionerror",
        "failed",
        "failure",
        "not found",
        "no such file",
        "timeout",
        "timed out",
        "connection refused",
        "unauthorized",
        "forbidden",
        "404",
        "401",
        "403",
        "500",
        "报错",
        "错误",
        "失败",
        "未通过",
        "找不到",
        "超时",
    )
    soft_failure_markers = ("exception", "error", "错误")
    success_phrases = (
        "tests passed",
        "test passed",
        "process exited with code 0",
        "1 passed",
        "passed in",
        "has been updated",
        "successfully",
        "已完成",
        "测试通过",
    )
    success_words = (
        "passed",
        "success",
        "succeeded",
        "ok",
        "done",
        "fixed",
        "verified",
        "updated",
        "completed",
        "完成",
        "成功",
        "通过",
        "修复",
    )
    if "<tool_use_error>" in low:
        return "failure"
    if "process exited with code 0" in low:
        return "success"
    if re.search(r"process exited with code [1-9]\d*", low):
        return "failure"
    if any(marker in low for marker in hard_failure_markers):
        return "failure"
    if any(marker in low for marker in success_phrases):
        return "success"
    if re.search(r"\b(?:200|201|204)\b", low):
        return "success"
    if any(re.search(rf"\b{re.escape(marker)}\b", low) for marker in success_words if marker.isascii()):
        return "success"
    if any(marker in low for marker in success_words if not marker.isascii()):
        return "success"
    if any(marker in low for marker in soft_failure_markers):
        return "failure"
    return "unknown"


def _outcome_status_for_tool(tool_name: str, text: str) -> str:
    name = re.sub(r"[^a-z0-9_]+", "", (tool_name or "").lower())
    low = (text or "").lower()
    if name not in {"bash", "exec_command"}:
        return _outcome_status(text)
    if "<tool_use_error>" in low:
        return "failure"
    if "process exited with code 0" in low:
        return "success"
    if re.search(r"process exited with code [1-9]\d*", low):
        return "failure"
    head = low[:1200]
    if re.search(r"(?:^|\s)(?:failed|failure|assertionerror|traceback)(?:\s|:)", head):
        return "failure"
    if re.search(r"(?:^|\s)error:", head):
        return "failure"
    success_markers = (
        "tests passed",
        "test passed",
        "1 passed",
        "passed in",
        "import ok",
        "successfully",
        "已完成",
        "测试通过",
    )
    if any(marker in low for marker in success_markers):
        return "success"
    return "unknown"


def _is_low_value_unit(tool_name: str, action_text: str, outcome: str, status: str) -> bool:
    name = re.sub(r"[^a-z0-9_]+", "", tool_name.lower())
    low_action = action_text.lower()
    low_outcome = outcome.lower()
    if name in {"read", "view_image", "edit", "write"} and not _explicit_tool_failure(low_outcome):
        return True
    if name in {
        "askuserquestion",
        "enterplanmode",
        "exitplanmode",
        "get_goal",
        "pushnotification",
        "schedulewakeup",
        "taskcreate",
        "taskget",
        "tasklist",
        "taskoutput",
        "taskstop",
        "taskupdate",
        "toolsearch",
        "webfetch",
        "websearch",
        "update_plan",
    }:
        return True
    if name in {"write_stdin", "taskoutput"} and "process running with session id" in low_outcome:
        return True
    if name == "write_stdin" and re.search(r'"chars"\s*:\s*""', low_action):
        return True
    if name == "bash" and status == "unknown" and _is_exploratory_shell_action(low_action):
        return True
    if name in {"exp_push_latest", "exp_push_file"}:
        return True
    return False


def _explicit_tool_failure(low_outcome: str) -> bool:
    body = re.sub(r"^📤\s*工具返回\s*(?:\([^)]*\))?\s*", "", low_outcome or "").strip()
    return (
        "<tool_use_error>" in body
        or body.startswith(("error:", "failed ", "failed:", "traceback", "no such file", "file does not exist"))
        or "permission denied" in body[:240]
    )


def _is_exploratory_shell_action(low_action: str) -> bool:
    command = _shell_command_from_action(low_action)
    if not command:
        return False
    return bool(
        re.match(
            r"^(?:ls|ll|pwd|wc|du|df|tree|find\s+\S+\s+-name|rg\s+--files|git\s+status|git\s+branch)\b",
            command,
        )
    )


def _shell_command_from_action(low_action: str) -> str:
    match = re.search(r"\bcommand=([^;}\n]+)", low_action)
    if not match:
        match = re.search(r'"command"\s*:\s*"([^"]+)"', low_action)
    if not match:
        return ""
    return " ".join(match.group(1).strip().split())


def _keyword_terms(text: str) -> list[str]:
    stats: dict[str, dict[str, float]] = {}
    for match in re.finditer(r"[A-Za-z][A-Za-z0-9_+#.-]{2,}|[\u4e00-\u9fff]{2,}", text):
        token = match.group(0).strip("._-").lower()
        candidates = _cjk_keyword_candidates(token) if _is_cjk(token) else _ascii_keyword_candidates(token)
        for term in candidates:
            if not _is_valid_keyword(term):
                continue
            item = stats.setdefault(term, {"count": 0.0, "first": float(match.start())})
            item["count"] += 1.0
            item["first"] = min(item["first"], float(match.start()))
    ranked = sorted(
        stats,
        key=lambda term: (
            -_keyword_score(term, int(stats[term]["count"])),
            stats[term]["first"],
            term,
        ),
    )
    return ranked[:32]


def _keyphrase_terms(text: str, keywords: list[str]) -> list[str]:
    low = text.lower()
    keyset = set(keywords)
    phrases: list[str] = []

    def contains(term: str) -> bool:
        term_low = term.lower()
        if term_low in keyset:
            return True
        return term_low in low

    for phrase, required in KEYPHRASE_RULES:
        if all(contains(term) for term in required):
            phrases.append(phrase)

    anchors = [term for term in keywords if term in PHRASE_ANCHORS]
    strong_terms = [
        term
        for term in keywords
        if term not in anchors
        and _is_strong_phrase_term(term)
        and term not in GENERIC_PHRASE_PARTNER_STOP
    ]
    for anchor in anchors[:5]:
        partners = [term for term in strong_terms if term != anchor][:3]
        if partners:
            phrases.append(" ".join([anchor, *partners[:2]]))

    code_terms = [term for term in keywords if "_" in term and _is_strong_phrase_term(term)]
    for first, second in zip(code_terms, code_terms[1:]):
        if first != second:
            phrases.append(f"{first} {second}")

    return [phrase for phrase in _dedupe(phrases) if _is_valid_keyphrase(phrase)][:16]


def _is_strong_phrase_term(term: str) -> bool:
    if not term or term in GENERIC_KEYWORD_STOP or term in RUNTIME_KEYWORD_STOP:
        return False
    if term in GENERIC_PHRASE_PARTNER_STOP or _is_noisy_phrase_part(term):
        return False
    if term in DOMAIN_KEYWORD_BOOST:
        return True
    if "_" in term:
        return True
    if re.search(r"[a-z][0-9]|[0-9][a-z]", term):
        return True
    if _is_cjk(term) and _has_cjk_domain_signal(term):
        return True
    return len(term) >= 7 and not term.isalpha()


def _is_valid_keyphrase(phrase: str) -> bool:
    if not phrase or len(phrase) > 120:
        return False
    parts = [part for part in re.split(r"\s+", phrase.strip()) if part]
    if len(parts) < 2:
        return False
    if not any(part in PHRASE_ANCHORS for part in parts):
        return False
    for part in parts:
        if part in ALLOWED_PHRASE_PARTS:
            continue
        if _is_noise_keyword(part) or _is_noisy_phrase_part(part):
            return False
    return True


def _is_noisy_phrase_part(term: str) -> bool:
    if not term:
        return True
    low = term.lower()
    if low in PHRASE_ANCHORS or low in DOMAIN_KEYWORD_BOOST:
        return False
    if low in GENERIC_PHRASE_PARTNER_STOP:
        return True
    if re.search(r"\.(?:py|md|csv|json|jsonl|sh|pdf|txt|log|yaml|yml|mp4|mov|tar|tgz|gz)$", low):
        return True
    if re.search(r"(?:^|[_-])step[_-]?\d+|checkpoint|ckpt|miniconda|x86_64|linux-x86", low):
        return True
    if re.search(r"(?:_exps?|_eval|eval_|latest-linux|content_block_delta|api_error)", low):
        return True
    if re.search(r"(?:secret|credential|password|username|api[_-]?key|agent[_-]?secret|agent[_-]?name|x-agent|stderr|stdout|proxy)", low):
        return True
    if re.search(r"(?:qzcli[_-]?tool|[_-]tool[#_-]?\\d*|tool[#_-]?\\d+)", low):
        return True
    if low.startswith("call_") or low.startswith("toolu"):
        return True
    if re.fullmatch(r"(?:area)?[a-z0-9_-]{8,}", low) and re.search(r"\d", low):
        return True
    if re.fullmatch(r"[a-z0-9]{5,10}", low) and re.search(r"[a-z]", low) and re.search(r"\d", low):
        return True
    if re.fullmatch(r"[a-f0-9]{7,}", low):
        return True
    if "/" in low or "\\" in low:
        return True
    if low.count(".") >= 1:
        return True
    if len(low) > 48:
        return True
    return False


def _ascii_keyword_candidates(token: str) -> list[str]:
    if not token:
        return []
    out = [token]
    parts = [p for p in re.split(r"[^a-z0-9+#]+", token) if len(p) >= 3]
    if len(parts) > 1:
        out.extend(parts)
    return _dedupe(out)


def _cjk_keyword_candidates(token: str) -> list[str]:
    if not token:
        return []
    out: list[str] = []
    if 2 <= len(token) <= 8:
        out.append(token)
    max_n = 4 if len(token) >= 4 else len(token)
    for n in range(max_n, 1, -1):
        for idx in range(0, len(token) - n + 1):
            out.append(token[idx : idx + n])
    return _dedupe(out)


def _is_valid_keyword(term: str) -> bool:
    if not term or len(term) > 40:
        return False
    if term in {"the", "and", "for", "with", "this", "that", "from", "your", "you", "assistant", "user"}:
        return False
    if term in RUNTIME_KEYWORD_STOP or term in GENERIC_KEYWORD_STOP:
        return False
    if term in WEAK_ASCII_KEYWORD_STOP or term in CJK_KEYWORD_STOP:
        return False
    if _is_noise_keyword(term):
        return False
    if term.isascii() and term.isalpha() and len(term) < 4 and term not in DOMAIN_KEYWORD_BOOST:
        return False
    if _is_cjk(term):
        if len(term) < 2:
            return False
        if any(stop in term for stop in CJK_KEYWORD_STOP):
            return False
        if not _has_cjk_domain_signal(term):
            return False
        if len(term) <= 2 and term not in DOMAIN_KEYWORD_BOOST:
            return False
    return True


def _has_cjk_domain_signal(term: str) -> bool:
    for keyword in DOMAIN_KEYWORD_BOOST:
        if not _is_cjk(keyword):
            continue
        if term == keyword or term in keyword or keyword in term:
            return True
    return False


def _keyword_score(term: str, count: int) -> float:
    score = float(min(count, 5))
    if term in DOMAIN_KEYWORD_BOOST:
        score += 12.0
    if any(boost in term for boost in DOMAIN_KEYWORD_BOOST if len(boost) >= 4):
        score += 5.0
    if "_" in term:
        score += 8.0
    if re.search(r"[a-z][0-9]|[0-9][a-z]", term):
        score += 6.0
    if re.search(r"(?:error|exception|traceback|assertion|failed|unknown|timeout|404|403|500)", term):
        score += 4.0
    if "." in term or "#" in term or "+" in term:
        score += 2.0
    if term.isascii() and term.isalpha() and len(term) <= 5 and term not in DOMAIN_KEYWORD_BOOST:
        score -= 2.0
    if _is_cjk(term) and len(term) >= 3:
        score += 2.0
    return score


def _segment_summary(segment: dict[str, Any]) -> str:
    parts: list[str] = []
    task = _strip_summary_noise(str(segment.get("task") or ""))
    if task and not _is_low_signal_summary(task):
        parts.append(task)
    for key in ("user_context", "work", "outcomes"):
        for item in segment.get(key) or []:
            clean = _strip_summary_noise(str(item))
            if clean and not _is_low_signal_summary(clean):
                parts.append(clean)
            if len(parts) >= 3:
                break
        if len(parts) >= 3:
            break
    summary = " ".join(parts)
    return _clip(summary, 520)


def _segment_keyword_source(segment: dict[str, Any]) -> str:
    pieces = [
        str(segment.get("summary") or ""),
        str(segment.get("task") or ""),
        " ".join(str(x) for x in (segment.get("tool_names") or [])),
    ]
    return " ".join(_strip_summary_noise(piece) for piece in pieces if piece)


def _strip_summary_noise(text: str) -> str:
    text = re.sub(r"<(?:local-command-caveat|turn_aborted)>.*?(?=<|$)", " ", text, flags=re.I | re.S)
    text = re.sub(r"<command-(?:name|message|args)>(.*?)</command-(?:name|message|args)>", r" \1 ", text, flags=re.I | re.S)
    text = re.sub(r"</?command-(?:name|message|args)>", " ", text, flags=re.I)
    text = re.sub(r"(?:^|[•\n])\s*(?:Auth|Command|Tools):\s*[^•\n]+", " ", text, flags=re.I)
    text = _strip_runtime_tool_list(text)
    text = re.sub(r"\[Request interrupted by user[^\]]*\]", " ", text, flags=re.I)
    text = re.sub(r"Caveat: The messages below were generated.*?(?:DO NOT respond[^.。]*[.。])?", " ", text, flags=re.I | re.S)
    text = re.sub(r"\bcodex\s+resume\s+[0-9a-f-]{12,}\b", " ", text, flags=re.I)
    text = re.sub(r"/tmp/prompt_audit/[A-Za-z0-9_.-]+", " prompt audit file ", text, flags=re.I)
    return " ".join(text.split())


def _is_low_signal_summary(text: str) -> bool:
    low = (text or "").lower().strip()
    compact = re.sub(r"[^a-z0-9/_-]+", " ", low).strip()
    if not compact:
        return True
    if compact in {"/context", "context", "summary", "resume"}:
        return True
    if re.fullmatch(r"(?:codex\s+)?resume(?:\s+[0-9a-f-]{8,})?", compact):
        return True
    if compact.startswith("read prompt audit file") and len(compact.split()) <= 8:
        return True
    return False


def _is_noise_keyword(term: str) -> bool:
    if term in RUNTIME_KEYWORD_STOP or term in GENERIC_KEYWORD_STOP:
        return True
    if term.startswith(("claude-", "local-command-", "task-", "tool-", "toolu", "users-")):
        return True
    if re.fullmatch(r"(?:tmp|var|root|home|users|project|projects?)[/_.-].*", term):
        return True
    if term not in DOMAIN_KEYWORD_BOOST and re.fullmatch(r"(?:[a-z]{1,4}\d+[a-z]?|\d{3,4}p)", term):
        return True
    if re.fullmatch(r"[0-9a-f]{4,}(?:-[0-9a-f]{4,})*", term):
        return True
    if term.startswith("ctrl"):
        return True
    if term.startswith("turn_"):
        return True
    return False


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _json_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _json_obj(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _item_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("step", "why", "how", "decision", "pitfall", "name"):
            value = item.get(key)
            if value:
                return str(value)
        return json.dumps(item, ensure_ascii=False, sort_keys=True)
    return str(item)


def _quality(row: sqlite3.Row) -> float:
    vals = [
        float(row[k] or 0.0)
        for k in ("q_outcome", "q_intent", "q_execution", "q_orchestration", "q_expression")
        if k in row.keys()
    ]
    return sum(vals) / len(vals) if vals else 0.0


def _chunk_id(experience_id: str, index: int, chunk_type: str, text: str) -> str:
    digest = hashlib.sha1(f"{experience_id}\0{index}\0{chunk_type}\0{text}".encode("utf-8")).hexdigest()
    return f"chk_{digest[:24]}"


def _token_count(text: str) -> int:
    return max(1, len(text) // 4)


def _index_terms_json(search_text: str, chunk_text: str = "") -> str:
    payload = {
        "all": _terms(search_text)[:512],
        "situation": _terms(_chunk_labeled_text(chunk_text, "situation"))[:256],
        "action": _terms(_chunk_action_text(chunk_text))[:256],
        "outcome": _terms(_chunk_labeled_text(chunk_text, "outcome"))[:256],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _decode_lexical_terms(raw: Any) -> tuple[str, ...] | None:
    return _decode_lexical_term_map(raw).get("all")


def _decode_lexical_term_map(raw: Any) -> dict[str, tuple[str, ...]]:
    if not raw:
        return {}
    if isinstance(raw, list):
        return {"all": tuple(str(item) for item in raw if item)}
    if isinstance(raw, dict):
        parsed = raw
    else:
        return _decode_lexical_term_map_text(str(raw))
    return _normalize_lexical_term_map(parsed)


@lru_cache(maxsize=65_536)
def _decode_lexical_term_map_text(raw: str) -> dict[str, tuple[str, ...]]:
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return _normalize_lexical_term_map(parsed)


def _normalize_lexical_term_map(parsed: Any) -> dict[str, tuple[str, ...]]:
    if isinstance(parsed, list):
        return {"all": tuple(str(item) for item in parsed if item)}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for key in ("all", "situation", "action", "outcome"):
        values = parsed.get(key)
        if isinstance(values, list):
            out[key] = tuple(str(item) for item in values if item)
    return out


def _decode_lexical_profile(raw: Any) -> dict[str, tuple[Counter[str], int]]:
    if not raw:
        return {}
    if isinstance(raw, str):
        return _decode_lexical_profile_text(raw)
    return {
        key: _term_profile(terms)
        for key, terms in _decode_lexical_term_map(raw).items()
    }


@lru_cache(maxsize=16_384)
def _decode_lexical_profile_text(
    raw: str,
) -> dict[str, tuple[Counter[str], int]]:
    return {
        key: _term_profile(terms)
        for key, terms in _decode_lexical_term_map_text(raw).items()
    }


def _term_profile(terms: list[str] | tuple[str, ...]) -> tuple[Counter[str], int]:
    return Counter(terms), len(terms)


def _clean_text(text: str) -> str:
    text = _strip_index_noise(_strip_runtime_noise(text or ""))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)[:2000]


def _retrieval_text(text: str, meta: dict[str, Any], chunk_type: str) -> str:
    """Build the compact text used for FTS/vector search.

    The injected chunk can stay verbose, but the index should represent the
    situation and outcome, not runtime dumps, tool ids, or full shell output.
    """

    raw = _strip_runtime_noise(text or "")
    lines: list[str] = []
    capture_prefixes = (
        "summary:",
        "task:",
        "situation:",
        "outcome:",
        "intent:",
        "query:",
        "pitfalls:",
        "key decisions:",
        "tools:",
    )
    for line in raw.splitlines():
        clean = " ".join(_strip_summary_noise(line).split())
        if not clean:
            continue
        low = clean.lower()
        if low.startswith(capture_prefixes):
            lines.append(clean)
        elif low.startswith("action:"):
            action = _action_retrieval_line(clean)
            if action:
                lines.append(action)
        elif low.startswith(("keywords:", "keyphrases:")):
            lines.append(clean)
        if len(lines) >= 10:
            break

    keywords = [str(x) for x in (meta.get("keywords") or []) if _is_valid_keyword(str(x).lower())]
    keyphrases = [str(x) for x in (meta.get("keyphrases") or []) if _is_valid_keyphrase(str(x).lower())]
    if keywords:
        lines.append("Keywords: " + ", ".join(keywords[:24]))
    if keyphrases:
        lines.append("Keyphrases: " + "; ".join(keyphrases[:12]))
    if not lines:
        lines.append(_clip(_strip_summary_noise(raw), 700))
    text_out = _strip_index_noise(" ".join(lines))
    return " ".join(text_out.split())[:1000]


def _is_indexable_action(line: str) -> bool:
    low = line.lower()
    if len(line) > 520:
        return False
    if re.search(r"(?:secret|password|api[_-]?key|agent[_-]?secret|credential|token)", low):
        return False
    if low.count("/") >= 3 or low.count("\\") >= 2:
        return False
    if low.count("{") + low.count("}") + low.count("[") + low.count("]") >= 6:
        return False
    return True


def _action_retrieval_line(line: str) -> str:
    """Keep an action signature while removing path/credential payload noise."""
    clean = " ".join((line or "").split())
    if not clean:
        return ""
    clean = re.sub(
        r"(?i)(secret|password|api[_-]?key|agent[_-]?secret|credential|token)\s*[:=]\s*[^\s,;}]+",
        r"\1=<redacted>",
        clean,
    )
    clean = re.sub(
        r"(?:[A-Za-z]:)?(?:[/\\][^/\\\s'\"]+){2,}[/\\]?([^/\\\s'\"]*)",
        lambda match: f"<path>/{match.group(1)}" if match.group(1) else "<path>",
        clean,
    )
    clean = re.sub(r"\b(?:[0-9a-f]{12,}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\b", "<id>", clean, flags=re.I)
    clean = _clip(clean, 360)
    if not _is_indexable_action(clean):
        match = re.match(r"(?i)action:\s*(?:tool\s+)?([A-Za-z0-9_.:-]+)", clean)
        return f"Action tool: {match.group(1)}" if match else ""
    return clean


def _action_signature(text: str, chunk_type: str) -> str:
    if chunk_type not in {"do_unit", "dont_unit", "experience_unit"}:
        return ""
    for line in (text or "").splitlines():
        if not line.lower().startswith("action:"):
            continue
        clean = _action_retrieval_line(line).lower()
        clean = re.sub(r"\s+", " ", clean).strip()
        if clean:
            return f"{chunk_type}:{clean}"
    return ""


def _chunk_action_text(text: str) -> str:
    action = _chunk_labeled_text(text, "action")
    return _action_retrieval_line(f"Action: {action}") if action else ""


def _chunk_labeled_text(text: str, label: str) -> str:
    prefix = label.lower().rstrip(":") + ":"
    for line in (text or "").splitlines():
        if line.lower().startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


def _strip_runtime_noise(text: str) -> str:
    text = text or ""
    text = re.sub(r"# AGENTS\.md instructions.*?</INSTRUCTIONS>", "", text, flags=re.S)
    text = re.sub(r"<environment_context>.*?</environment_context>", "", text, flags=re.S)
    text = re.sub(r"(?:^|[•\n])\s*(?:Auth|Command|Tools):\s*[^•\n]+", " ", text, flags=re.I)
    text = _strip_runtime_tool_list(text)
    text = re.sub(
        r"📥 connected to experience pool.*?(?:opt out\.|`/me` to revoke)",
        "",
        text,
        flags=re.S,
    )
    text = re.sub(
        r"experience-pool agent contract.*?(?:<!-- end experience-pool -->)?",
        "",
        text,
        flags=re.S | re.I,
    )
    text = text.replace("Not logged in · Please run /login", "")
    return text.strip()


def _strip_runtime_tool_list(text: str) -> str:
    return re.sub(
        r"\b(?:exp(?:ool)?_[a-z0-9_]+|mcp__expool__[a-z0-9_]+)(?:\s*,\s*(?:exp(?:ool)?_[a-z0-9_]+|mcp__expool__[a-z0-9_]+)){2,}",
        " ",
        text,
        flags=re.I,
    )


def _strip_index_noise(text: str) -> str:
    text = re.sub(r"📤\s*工具返回\s*(?:\(id=[^)]+\))?", " ", text)
    text = re.sub(r"🔧\s*调用工具\s*:?", " ", text)
    text = re.sub(
        r"📤\s*(?:uploaded|upload skipped).*?(?=(?:\n|$| \d+\.|\s[-*]\s))",
        " ",
        text,
        flags=re.I,
    )
    text = re.sub(r"\[task-summary\]:.*?(?=(?:\n|$))", " ", text, flags=re.I)
    return text


def _int(row: sqlite3.Row, key: str) -> int:
    try:
        return int(row[key] or 0)
    except Exception:
        return 0


def _optional_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _fts_signal(
    conn: sqlite3.Connection,
    query_terms: list[str],
    *,
    agent_ids: set[str] | None = None,
) -> dict[str, float]:
    terms = [t for t in query_terms if len(t) >= 2]
    if not terms:
        return {}
    agent_filter = ""
    agent_params: list[Any] = []
    if agent_ids is not None:
        if not agent_ids:
            return {}
        placeholders = ",".join("?" * len(agent_ids))
        agent_filter = f" AND c.agent_id IN ({placeholders})"
        agent_params = list(sorted(agent_ids))
    rows: list[sqlite3.Row] = []
    operators = ("AND", "OR") if len(terms) >= 2 else ("OR",)
    for operator in operators:
        expr = _escape_fts(terms, operator=operator)
        if not expr:
            continue
        params: list[Any] = [expr, *agent_params]
        try:
            rows = conn.execute(
                f"""
                SELECT rag_chunks_fts.chunk_id
                FROM rag_chunks_fts
                JOIN rag_chunks c ON c.chunk_id = rag_chunks_fts.chunk_id
                WHERE rag_chunks_fts MATCH ?
                {agent_filter}
                ORDER BY rank
                LIMIT {FTS_CANDIDATE_LIMIT}
                """,
                params,
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if rows:
            break
    total = max(1, len(rows))
    return {r["chunk_id"]: 1.0 - (idx / total) for idx, r in enumerate(rows)}


def _vector_top_signal(
    conn: sqlite3.Connection,
    query_vector: Any,
    *,
    limit: int,
    agent_ids: set[str] | None = None,
    include_ids: set[str] | None = None,
) -> tuple[dict[str, float], int]:
    """Return vector top-N from a process-local, optionally ACL-filtered cache."""
    key = _vector_cache_key(conn)
    stamp_row = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(MAX(rowid), 0) AS max_rowid "
        "FROM rag_vectors WHERE model = ?",
        (EMBED_MODEL,),
    ).fetchone()
    stamp = (int(stamp_row["n"]), int(stamp_row["max_rowid"]))

    with _VECTOR_CACHE_LOCK:
        entry = _VECTOR_INDEX_CACHE.get(key)
        if entry is None or entry.get("stamp") != stamp:
            rows = conn.execute(
                """
                SELECT v.chunk_id, v.vector, c.agent_id
                FROM rag_vectors v
                JOIN rag_chunks c ON c.chunk_id = v.chunk_id
                WHERE v.model = ?
                ORDER BY v.rowid
                """,
                (EMBED_MODEL,),
            ).fetchall()
            ids = tuple(str(row["chunk_id"]) for row in rows)
            row_agent_ids = tuple(str(row["agent_id"]) for row in rows)
            dim = len(query_vector)
            matrix = None
            blobs: tuple[bytes, ...] = ()
            try:
                import numpy as np

                raw = b"".join(row["vector"] for row in rows)
                values = np.frombuffer(raw, dtype=np.float32)
                if values.size != len(rows) * dim:
                    raise ValueError("unexpected vector dimensions")
                matrix = values.reshape(len(rows), dim).copy()
            except (ImportError, TypeError, ValueError):
                blobs = tuple(bytes(row["vector"]) for row in rows)
            entry = {
                "stamp": stamp,
                "ids": ids,
                "id_to_index": {chunk_id: idx for idx, chunk_id in enumerate(ids)},
                "agent_ids": row_agent_ids,
                "matrix": matrix,
                "blobs": blobs,
            }
            _VECTOR_INDEX_CACHE[key] = entry
            while len(_VECTOR_INDEX_CACHE) > 4:
                _VECTOR_INDEX_CACHE.pop(next(iter(_VECTOR_INDEX_CACHE)))

    ids = entry["ids"]
    count = len(ids)
    row_agent_ids = entry.get("agent_ids") or ()
    eligible = (
        [idx for idx, agent_id in enumerate(row_agent_ids) if agent_id in agent_ids]
        if agent_ids is not None
        else list(range(count))
    )
    take = min(max(0, limit), len(eligible))
    if take == 0:
        return {}, count
    matrix = entry.get("matrix")
    if matrix is not None:
        import numpy as np

        query = np.frombuffer(query_vector, dtype=np.float32, count=len(query_vector))
        # einsum avoids BLAS thread startup overhead for this small 18k x 256 matrix.
        scores = np.einsum("ij,j->i", matrix, query, optimize=False)
        eligible_indices = np.asarray(eligible, dtype=np.intp)
        eligible_scores = scores[eligible_indices]
        if take < len(eligible):
            local_indices = np.argpartition(
                eligible_scores, len(eligible) - take
            )[-take:]
            indices = eligible_indices[local_indices]
        else:
            indices = eligible_indices
        indices = indices[np.argsort(scores[indices])[::-1]]
        result = {ids[int(idx)]: float(scores[int(idx)]) for idx in indices}
        id_to_index = entry.get("id_to_index") or {}
        for chunk_id in include_ids or ():
            idx = id_to_index.get(chunk_id)
            if idx is None:
                continue
            if agent_ids is not None and row_agent_ids[idx] not in agent_ids:
                continue
            result.setdefault(chunk_id, float(scores[idx]))
        return result, count

    blobs = entry.get("blobs") or ()
    ranked = heapq.nlargest(
        take,
        (
            (cosine(query_vector, from_blob(blobs[idx])), idx)
            for idx in eligible
        ),
    )
    result = {ids[idx]: float(score) for score, idx in ranked}
    id_to_index = entry.get("id_to_index") or {
        chunk_id: idx for idx, chunk_id in enumerate(ids)
    }
    for chunk_id in include_ids or ():
        idx = id_to_index.get(chunk_id)
        if idx is None:
            continue
        if agent_ids is not None and row_agent_ids[idx] not in agent_ids:
            continue
        result.setdefault(chunk_id, cosine(query_vector, from_blob(blobs[idx])))
    return result, count


def _vector_cache_key(conn: sqlite3.Connection) -> str:
    for row in conn.execute("PRAGMA database_list").fetchall():
        name = row[1] if not isinstance(row, sqlite3.Row) else row["name"]
        if name != "main":
            continue
        path = row[2] if not isinstance(row, sqlite3.Row) else row["file"]
        return str(path or f":memory:{id(conn)}")
    return f":connection:{id(conn)}"


def _invalidate_vector_index(conn: sqlite3.Connection) -> None:
    with _VECTOR_CACHE_LOCK:
        _VECTOR_INDEX_CACHE.pop(_vector_cache_key(conn), None)


def _escape_fts(query_terms: list[str], *, operator: str = "OR") -> str:
    # FTS is still useful for ASCII/code tokens and exact short phrases. For
    # Chinese, SQLite unicode61 has no word segmentation, so lexical overlap
    # below carries most of the signal.
    tokens = [t for t in query_terms if len(t) >= 2]
    joiner = f" {operator if operator in {'AND', 'OR'} else 'OR'} "
    return joiner.join(f'"{t}"' for t in tokens[:24])


def re_split(query: str) -> list[str]:
    return _query_terms(query)


def _query_terms(query: str) -> list[str]:
    terms = _dedupe([term for term in _terms(query) if _is_valid_query_term(term)])
    ranked = sorted(
        enumerate(terms),
        key=lambda item: (-_query_term_priority(item[1]), item[0]),
    )
    selected = [term for _, term in ranked[:32]]
    expanded = list(selected)
    for term in selected:
        expanded.extend(QUERY_SYNONYMS.get(term, ()))
    return _dedupe(expanded)[:40]


def _has_retrieval_signal(query: str, query_terms: list[str]) -> bool:
    if not (query or "").strip():
        return False
    if _is_weak_retrieval_query(query):
        return False
    if query_terms:
        return True
    return False


def _query_text_for_embedding(query: str, query_terms: list[str]) -> str:
    if query_terms:
        return " ".join(query_terms[:32])
    return " ".join((query or "").split())[:500]


def _is_valid_query_term(term: str) -> bool:
    term = (term or "").strip().lower()
    if not term:
        return False
    if term in QUERY_SYNONYMS:
        return True
    if term in DOMAIN_KEYWORD_BOOST:
        return True
    if term in RUNTIME_KEYWORD_STOP or term in GENERIC_KEYWORD_STOP:
        return False
    if term in WEAK_ASCII_KEYWORD_STOP or term in CJK_KEYWORD_STOP:
        return False
    if _is_noise_keyword(term) or _is_noisy_phrase_part(term):
        return False
    if "/" in term or "\\" in term:
        return False
    if term.isascii():
        if re.search(r"\d", term) and len(term) <= 5:
            return False
        if re.fullmatch(r"[a-f0-9]{7,}", term):
            return False
        if re.fullmatch(r"[a-z0-9_-]{6,}", term) and re.search(r"\d", term) and "_" not in term:
            return False
        if term.isalpha() and len(term) < 4:
            return False
        return len(term) >= 4 or "_" in term or re.search(r"[a-z][0-9]|[0-9][a-z]", term) is not None
    if _is_cjk(term):
        return len(term) >= 2
    return len(term) >= 3


def _query_term_priority(term: str) -> int:
    if term in DOMAIN_KEYWORD_BOOST or term in QUERY_SYNONYMS:
        return 100
    if term.isascii():
        if "_" in term or re.search(r"[a-z][0-9]|[0-9][a-z]", term):
            return 90
        return 70 + min(len(term), 12)
    if _is_cjk(term):
        return 60 + min(len(term), 4)
    return 10


def _is_weak_retrieval_query(query: str) -> bool:
    compact = re.sub(r"[\s\W_]+", "", (query or "").lower(), flags=re.UNICODE)
    if not compact:
        return True
    if compact in {
        "hi", "hello", "ok", "okay", "thanks", "thankyou", "continue",
        "你好", "您好", "在吗", "在不在", "继续", "继续做", "好的", "谢谢",
    }:
        return True
    if len(compact) <= 14 and re.fullmatch(
        r"(?:(?:请|你|再|帮我|给我|先|来|就|也|能不能|可以|快点|好好|一下|"
        r"看看|看下|看一看|最近|现在|当前|这个|那个|这些|那些|内容|情况|怎么样|如何|呢|吧|呀))+",
        compact,
    ):
        return True
    return False


def _text_terms(text: str) -> list[str]:
    return list(_cached_text_terms(text or ""))


@lru_cache(maxsize=50_000)
def _cached_text_terms(text: str) -> tuple[str, ...]:
    return tuple(_terms(text))


def _terms(text: str) -> list[str]:
    """Tokenize mixed Chinese/code text for lexical scoring.

    SQLite FTS5's default unicode61 tokenizer treats long Chinese runs poorly
    for this use case. We keep ASCII/code identifiers as terms and add CJK
    bigrams/trigrams so partial Chinese queries can still match naturally.
    """
    text = (text or "").lower()
    terms: list[str] = []
    for match in re.finditer(r"[a-z0-9_+#.-]+|[\u4e00-\u9fff]+", text):
        token = match.group(0).strip("._-")
        if len(token) < 2:
            continue
        if _is_cjk(token):
            terms.extend(_cjk_terms(token))
        else:
            terms.extend(_ascii_terms(token))
    return _dedupe(terms)


def _is_cjk(token: str) -> bool:
    return all("\u4e00" <= ch <= "\u9fff" for ch in token)


def _ascii_terms(token: str) -> list[str]:
    parts = [p for p in re.split(r"[^a-z0-9+#]+", token) if len(p) >= 2]
    out: list[str] = []
    if len(token) >= 2:
        out.append(token)
    for part in parts:
        out.append(part)
        # Split common compact identifiers enough for keyword search without
        # losing exact tokens like "fastapi", "hmac", or "claude-code".
        if len(part) > 8 and not part.isalpha():
            out.extend(part[i : i + 4] for i in range(0, len(part) - 3))
    return out


def _cjk_terms(token: str) -> list[str]:
    if len(token) <= 4:
        return [token] + [token[i : i + 2] for i in range(0, max(0, len(token) - 1))]
    grams = [token[i : i + 2] for i in range(0, len(token) - 1)]
    grams.extend(token[i : i + 3] for i in range(0, len(token) - 2))
    return grams


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            out.append(item)
            seen.add(item)
    return out


def _lexical_signal(query_terms: list[str], text: str) -> float:
    return _lexical_metrics(query_terms, text)[0]


def _lexical_metrics(
    query_terms: list[str],
    text: str,
    *,
    doc_terms: tuple[str, ...] | None = None,
    doc_profile: tuple[Counter[str], int] | None = None,
    query_profile: tuple[Counter[str], int] | None = None,
) -> tuple[float, float]:
    if not query_terms:
        return 0.0, 0.0
    if doc_profile is None:
        if doc_terms is None:
            doc_terms = _cached_text_terms(text or "")
        doc_profile = _term_profile(doc_terms)
    doc_counts, doc_total = doc_profile
    if doc_total <= 0:
        return 0.0, 0.0
    if query_profile is None:
        query_profile = _term_profile(query_terms)
    query_counts, query_total = query_profile
    overlap = 0
    for term, count in query_counts.items():
        overlap += min(count, doc_counts.get(term, 0))
    coverage = overlap / max(1, query_total)
    precision = overlap / max(1, doc_total)
    if coverage <= 0:
        return 0.0, 0.0
    # Coverage matters most for recall. The small precision term prevents
    # very long generic chunks from dominating when they mention one keyword.
    score = (0.85 * coverage) + (0.15 * math.sqrt(precision))
    return max(0.0, min(1.0, score)), coverage


def _keyword_coverage(query_terms: list[str], text: str) -> float:
    return _lexical_metrics(query_terms, text)[1]


def _keyword_coverage_bonus(query_terms: list[str], coverage: float) -> float:
    if len(query_terms) < 3:
        return 0.0
    if coverage >= 0.5:
        return 0.12
    if coverage >= 0.34:
        return 0.05
    if coverage <= 0.2:
        return -0.10
    return 0.0


def _chunk_type_bonus(chunk_type: str, keyword: float, similarity: float) -> float:
    if keyword <= 0 and similarity < 0.2:
        return 0.0
    if chunk_type in {"do_unit", "dont_unit"}:
        return 0.14
    if chunk_type == "trajectory_segment":
        return 0.03
    if chunk_type == "trajectory_overview":
        return 0.01
    return 0.0


def _outcome_status_alignment(query: str, chunk_type: str) -> float:
    """Prefer the successful or failed unit explicitly requested by a query."""

    return _outcome_status_alignment_for_preference(
        _query_outcome_preference(query), chunk_type
    )


def _outcome_status_alignment_for_preference(
    preference: str | None, chunk_type: str
) -> float:
    if preference is None or chunk_type not in {"do_unit", "dont_unit"}:
        return 0.0
    is_match = (preference == "success" and chunk_type == "do_unit") or (
        preference == "failure" and chunk_type == "dont_unit"
    )
    return 0.12 if is_match else -0.08


def _query_outcome_preference(query: str) -> str | None:
    low = (query or "").lower()
    success = bool(
        re.search(
            r"\b(?:passed|success(?:ful(?:ly)?)?|succeeded|fixed|resolved|completed|works)\b",
            low,
        )
        or re.search(r"成功|通过|已修复|已解决|完成", low)
    )
    failure = bool(
        re.search(
            r"\b(?:failed|failure|error|exception|traceback|assertionerror|timeout|denied)\b",
            low,
        )
        or re.search(r"失败|报错|错误|异常|超时|拒绝", low)
    )
    if success == failure:
        return None
    return "success" if success else "failure"


def _candidate_is_relevant(item: dict[str, Any]) -> bool:
    """Reject low-confidence filler instead of always filling ``top_k``."""
    score = float(item.get("score") or 0.0)
    if score < MIN_RECALL_SCORE:
        return False
    keyword = float(item.get("keyword") or 0.0)
    coverage = float(item.get("coverage") or 0.0)
    if keyword > 0.0 or coverage >= 0.2:
        return True
    return float(item.get("similarity") or 0.0) >= MIN_VECTOR_ONLY_SIMILARITY


def _experience_summaries(conn: sqlite3.Connection, ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    seen = []
    for eid in ids:
        if eid not in seen:
            seen.append(eid)
    rows = conn.execute(
        f"""
        SELECT e.experience_id, e.query, e.intent_text, e.outcome, e.summary,
               e.task_type, e.sensitivity, e.created_at,
               e.session_id, e.source_agent_type,
               COALESCE(e.parent_session_id, e.session_id) AS parent_session_id,
               e.segment_id,
               e.source_byte_start, e.source_byte_end, e.task_status,
               a.name AS agent_name
        FROM experiences e
        JOIN agents a ON a.agent_id = e.agent_id
        WHERE e.experience_id IN ({','.join('?' * len(seen))})
        """,
        seen,
    ).fetchall()
    by_id = {r["experience_id"]: dict(r) for r in rows}
    return [by_id[eid] for eid in seen if eid in by_id]


def _context_text(chunks: list[dict[str, Any]]) -> str:
    if not chunks:
        return "【经验池RAG上下文】未找到可用经验。"
    lines = ["【经验池RAG上下文】以下为按权限过滤、chunk 召回后的短上下文。"]
    for idx, chunk in enumerate(chunks, start=1):
        sim = round(float(chunk["similarity"]) * 100)
        score = float(chunk.get("score") or 0.0)
        short_eid = chunk["experience_id"][:8]
        turn_range = _turn_range_label(chunk.get("turn_start"), chunk.get("turn_end"))
        text = _context_excerpt(chunk["text"])
        usage = _usage_label(chunk)
        lines.append(
            f"{idx}. [{usage}{chunk['source']}:{chunk['chunk_type']}, score={score:.2f}, "
            f"sim={sim}%, exp={short_eid}{turn_range}] {text}"
        )
    lines.append("使用要求：DO 可模仿；DO NOT 只用于规避；若冲突，以当前代码和用户要求为准。")
    return "\n".join(lines)


def _context_excerpt(text: str, limit: int = 360) -> str:
    text = " ".join(_strip_runtime_noise(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _turn_range_label(start: Any, end: Any) -> str:
    start_i = _optional_int(start)
    end_i = _optional_int(end)
    if start_i is None:
        return ""
    if end_i is None or end_i == start_i:
        return f", turn={start_i}"
    return f", turns={start_i}-{end_i}"


def _usage_label(chunk: dict[str, Any]) -> str:
    chunk_type = str(chunk.get("chunk_type") or "")
    status = str((chunk.get("meta") or {}).get("unit_status") or "")
    if chunk_type == "do_unit" or status == "success":
        return "DO "
    if chunk_type == "dont_unit" or status == "failure":
        return "DO NOT "
    return ""
