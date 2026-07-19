"""v0 lite path tests. Mock LLM, no real Claude calls."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

os.environ.setdefault("EXP_LLM", "mock")
os.environ.setdefault("EXP_DEFER_OPF", "1")

import pytest  # noqa: E402

from exp_core import lite as lite_mod  # noqa: E402
from exp_core import rag as rag_mod  # noqa: E402
from exp_core.pool import ExperiencePool, PoolConfig  # noqa: E402


def trajectory(query: str = "summarize this csv") -> list[dict]:
    return [
        {"role": "user", "content": query},
        {"role": "assistant", "content": "loaded csv, ran describe()"},
        {"role": "assistant", "content": "applied groupby; here are totals"},
        {"role": "assistant", "content": "Top 3 regions: APAC, EMEA, AMER."},
    ]


def make_pool(tmp_path: Path) -> ExperiencePool:
    return ExperiencePool(PoolConfig(root=tmp_path))


def test_pool_connection_enables_sqlite_concurrency_and_integrity(tmp_path):
    pool = make_pool(tmp_path)

    assert pool.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert pool.conn.execute("PRAGMA synchronous").fetchone()[0] == 1
    assert pool.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert pool.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30_000
    pool.close()


# ---- structuring ----

def test_rule_based_structure_extracts_query_and_steps():
    card = lite_mod.structure_rule_based(trajectory("compute revenue"))
    assert card.query == "compute revenue"
    assert "compute revenue" in card.intent
    assert len(card.steps) == 3
    assert "APAC" in card.outcome


def test_rule_based_handles_empty_trajectory():
    card = lite_mod.structure_rule_based([])
    assert card.query == "(no user turn)"
    assert card.outcome == "(no assistant turn)"


# ---- local sanitize ----

def test_local_sanitize_redacts_email_and_aws_key():
    traj = [
        {"role": "user", "content": "email me at a@corp.example.com using AKIAIOSFODNN7EXAMPLE"},
        {"role": "assistant", "content": "done"},
    ]
    cleaned, counts = lite_mod.sanitize_trajectory_local(traj)
    assert "<EMAIL>" in cleaned[0]["content"]
    assert "<KEY>" in cleaned[0]["content"]
    assert "AKIAIOSFODNN7EXAMPLE" not in cleaned[0]["content"]
    assert counts.get("aws_access_key", 0) >= 1


def test_prepare_local_end_to_end():
    card = lite_mod.prepare_local(
        trajectory("rank top regions"),
        task_type="csv_analysis", source_model="claude-haiku",
        acl="team:platform", tags=["csv"],
    )
    assert card.query == "rank top regions"
    assert card.task_type == "csv_analysis"
    assert card.acl == "team:platform"
    assert "csv" in card.tags


# ---- push + search round trip ----

def test_push_lite_then_search_round_trip(tmp_path):
    pool = make_pool(tmp_path)
    pool.register_agent("alice", "platform")

    card = lite_mod.prepare_local(
        trajectory("rank top regions by revenue"),
        task_type="csv_analysis", source_model="claude-stub",
        acl="team:platform",
    )
    res = lite_mod.push_lite(
        pool.conn, rules=pool._sanitize_rules, agent_name="alice", card=card,
    )
    assert res["ingest_path"] == "lite"
    assert res["sanitization_status"] in {"done", "flagged", "layer1_only"}
    assert res["review_status"] == "auto_approved"

    hits = lite_mod.search_lite(
        pool.conn, viewer_name="alice", query="rank revenue regions", top_k=5,
    )
    assert hits, "expected at least one hit"
    h = hits[0]
    assert h["ingest_path"] == "lite"
    assert "similarity" in h
    assert h["query"] == "rank top regions by revenue"
    assert isinstance(h["steps"], list)
    pool.close()


def test_acl_private_invisible_to_other_agent(tmp_path):
    pool = make_pool(tmp_path)
    pool.register_agent("alice", "platform")
    pool.register_agent("bob", "data")

    card = lite_mod.prepare_local(trajectory("private notes"), acl="private")
    lite_mod.push_lite(pool.conn, rules=pool._sanitize_rules,
                       agent_name="alice", card=card)

    hits = lite_mod.search_lite(pool.conn, viewer_name="bob",
                                 query="private notes")
    assert hits == []
    # alice can see her own row
    hits = lite_mod.search_lite(pool.conn, viewer_name="alice",
                                 query="private notes")
    assert len(hits) == 1
    pool.close()


def test_acl_team_visible_to_same_team(tmp_path):
    pool = make_pool(tmp_path)
    pool.register_agent("alice", "platform")
    pool.register_agent("carol", "platform")
    pool.register_agent("bob", "data")

    card = lite_mod.prepare_local(trajectory("team CSV playbook"),
                                  acl="team:platform")
    lite_mod.push_lite(pool.conn, rules=pool._sanitize_rules,
                       agent_name="alice", card=card)

    assert len(lite_mod.search_lite(pool.conn, viewer_name="carol",
                                     query="csv playbook")) == 1
    assert lite_mod.search_lite(pool.conn, viewer_name="bob",
                                 query="csv playbook") == []
    pool.close()


def test_acl_public_direct_upload_is_private_until_publish(tmp_path):
    pool = make_pool(tmp_path)
    pool.register_agent("alice", "platform")
    pool.register_agent("bob", "data")

    card = lite_mod.prepare_local(trajectory("everyone-sees this"),
                                  acl="public")
    info = lite_mod.push_lite(pool.conn, rules=pool._sanitize_rules,
                              agent_name="alice", card=card)
    assert info["acl"] == "private"
    # Direct uploads cannot become community-visible; use publish instead.
    hits = lite_mod.search_lite(pool.conn, viewer_name="bob", query="everyone")
    assert hits == []
    pool.close()


def test_acl_org_legacy_alias_direct_upload_is_private(tmp_path):
    pool = make_pool(tmp_path)
    pool.register_agent("alice", "platform")
    pool.register_agent("bob", "data")

    card = lite_mod.prepare_local(trajectory("legacy-org visible"), acl="org")
    info = lite_mod.push_lite(pool.conn, rules=pool._sanitize_rules,
                              agent_name="alice", card=card)
    assert info["acl"] == "private"

    hits = lite_mod.search_lite(pool.conn, viewer_name="bob", query="legacy-org")
    assert hits == []
    pool.close()


def test_private_high_redaction_does_not_block_recall(tmp_path):
    pool = make_pool(tmp_path)
    pool.register_agent("alice", "platform")

    raw = trajectory("ship with key AKIAIOSFODNN7EXAMPLE today")
    card = lite_mod.prepare_local(raw, acl="private")
    info = lite_mod.push_lite(
        pool.conn,
        rules=pool._sanitize_rules,
        agent_name="alice",
        card=card,
        trajectory=raw,
        trajectories_dir=tmp_path / "trajectories",
    )

    assert info["acl"] == "private"
    assert info["review_status"] == "auto_approved"
    assert info["redactions"].get("aws_access_key", 0) >= 1
    chunks = pool.conn.execute(
        "SELECT lexical_terms FROM rag_chunks WHERE experience_id = ?",
        (info["experience_id"],),
    ).fetchall()
    assert chunks
    for chunk in chunks:
        term_map = json.loads(chunk["lexical_terms"])
        assert set(term_map) == {"all", "situation", "action", "outcome"}
        assert all(isinstance(value, list) for value in term_map.values())

    # Index metadata from older deployments was a bare list. Maintenance
    # upgrades it without rebuilding or re-embedding the long session.
    pool.conn.execute(
        "UPDATE rag_chunks SET lexical_terms = '[\"legacy\"]' WHERE experience_id = ?",
        (info["experience_id"],),
    )
    pool.conn.commit()
    assert rag_mod.refresh_stale_retrieval_text(pool.conn) == len(chunks)
    upgraded = pool.conn.execute(
        "SELECT lexical_terms FROM rag_chunks WHERE experience_id = ?",
        (info["experience_id"],),
    ).fetchall()
    assert all(isinstance(json.loads(row["lexical_terms"]), dict) for row in upgraded)
    pool.close()


def test_codex_provenance_backfill_repairs_existing_source_rows(tmp_path):
    pool = make_pool(tmp_path)
    pool.register_agent("alice", "platform")
    raw = trajectory("repair a long Codex rollout task")
    card = lite_mod.prepare_local(raw, acl="private")
    info = lite_mod.push_lite(
        pool.conn,
        rules=pool._sanitize_rules,
        agent_name="alice",
        card=card,
        trajectory=raw,
        meta={
            "agent_type": "codex",
            "session_id": "rollout-demo:turn-9",
            "extra": {
                "parent_session_id": "rollout-demo",
                "codex_turn_id": "turn-9",
                "byte_start": 100,
                "byte_end": 900,
                "task_status": "complete",
            },
        },
        trajectories_dir=tmp_path / "trajectories",
    )
    pool.conn.execute(
        "UPDATE experiences SET parent_session_id = NULL, segment_id = NULL "
        "WHERE experience_id = ?",
        (info["experience_id"],),
    )
    pool.conn.commit()

    assert rag_mod.backfill_experience_provenance(pool.conn) == 1
    repaired = pool.conn.execute(
        "SELECT parent_session_id, segment_id, source_byte_start, source_byte_end "
        "FROM experiences WHERE experience_id = ?",
        (info["experience_id"],),
    ).fetchone()
    assert repaired["parent_session_id"] == "rollout-demo"
    assert repaired["segment_id"] == "turn-9"
    assert repaired["source_byte_start"] == 100
    assert repaired["source_byte_end"] == 900
    pool.close()


def test_rag_maintenance_prunes_indexes_for_non_searchable_experiences(tmp_path):
    pool = make_pool(tmp_path)
    pool.register_agent("alice", "platform")
    card = lite_mod.prepare_local(trajectory("prune a revoked experience"), acl="private")
    info = lite_mod.push_lite(
        pool.conn,
        rules=pool._sanitize_rules,
        agent_name="alice",
        card=card,
        trajectory=trajectory("prune a revoked experience"),
        trajectories_dir=tmp_path / "trajectories",
    )
    chunk_count = pool.conn.execute(
        "SELECT COUNT(*) FROM rag_chunks WHERE experience_id = ?",
        (info["experience_id"],),
    ).fetchone()[0]
    assert chunk_count > 0

    # Simulate an older deployment that marked the row revoked but left its
    # child index behind.
    pool.conn.execute(
        "UPDATE experiences SET revoked = 1, review_status = 'revoked' "
        "WHERE experience_id = ?",
        (info["experience_id"],),
    )
    pool.conn.commit()

    assert rag_mod.prune_stale_experience_indexes(pool.conn) == (1, chunk_count)
    assert rag_mod.prune_stale_experience_indexes(pool.conn) == (0, 0)
    assert pool.conn.execute(
        "SELECT COUNT(*) FROM rag_chunks WHERE experience_id = ?",
        (info["experience_id"],),
    ).fetchone()[0] == 0
    assert pool.conn.execute(
        "SELECT COUNT(*) FROM rag_vectors v JOIN rag_chunks c "
        "ON c.chunk_id = v.chunk_id WHERE c.experience_id = ?",
        (info["experience_id"],),
    ).fetchone()[0] == 0
    pool.close()


def test_rag_rebuild_computes_outside_writer_lock_and_retries_stale_source(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    pool = make_pool(tmp_path)
    pool.register_agent("alice", "platform")
    raw = trajectory("rebuild a long session without holding sqlite writer")
    card = lite_mod.prepare_local(raw, acl="private")
    info = lite_mod.push_lite(
        pool.conn,
        rules=pool._sanitize_rules,
        agent_name="alice",
        card=card,
        trajectory=raw,
        trajectories_dir=tmp_path / "trajectories",
    )
    original = rag_mod._chunks_from_row  # noqa: SLF001
    calls = 0

    def concurrent_update(row):
        nonlocal calls
        calls += 1
        assert not pool.conn.in_transaction
        chunks = original(row)
        if calls == 1:
            other = sqlite3.connect(pool.config.db_path, timeout=30)
            other.execute("PRAGMA journal_mode = WAL")
            other.execute(
                "UPDATE experiences SET q_outcome = q_outcome + 0.1 "
                "WHERE experience_id = ?",
                (info["experience_id"],),
            )
            other.commit()
            other.close()
        return chunks

    monkeypatch.setattr(rag_mod, "_chunks_from_row", concurrent_update)
    rebuilt = rag_mod.rebuild_experience(pool.conn, info["experience_id"])

    assert rebuilt > 0
    assert calls == 2
    pool.close()


def test_team_high_redaction_still_requires_review(tmp_path):
    pool = make_pool(tmp_path)
    pool.register_agent("alice", "platform")

    raw = trajectory("ship with key AKIAIOSFODNN7EXAMPLE today")
    card = lite_mod.prepare_local(raw, acl="team:platform")
    info = lite_mod.push_lite(
        pool.conn,
        rules=pool._sanitize_rules,
        agent_name="alice",
        card=card,
        trajectory=raw,
        trajectories_dir=tmp_path / "trajectories",
    )

    assert info["acl"] == "team:platform"
    assert info["review_status"] == "pending"
    assert info["redactions"].get("aws_access_key", 0) >= 1
    chunks = pool.conn.execute(
        "SELECT COUNT(*) AS n FROM rag_chunks WHERE experience_id = ?",
        (info["experience_id"],),
    ).fetchone()
    assert chunks["n"] == 0
    pool.close()


def test_unknown_agent_rejected_on_push(tmp_path):
    pool = make_pool(tmp_path)
    card = lite_mod.prepare_local(trajectory())
    with pytest.raises(ValueError, match="unknown agent"):
        lite_mod.push_lite(pool.conn, rules=pool._sanitize_rules,
                            agent_name="nobody", card=card)
    pool.close()


def test_lite_skips_judge_and_credit(tmp_path):
    """v0 invariant: no Q updates, no edges, no rewards rows."""
    pool = make_pool(tmp_path)
    pool.register_agent("alice", "platform")
    card = lite_mod.prepare_local(trajectory(), acl="public")
    lite_mod.push_lite(pool.conn, rules=pool._sanitize_rules,
                        agent_name="alice", card=card)

    cur = pool.conn.execute("SELECT COUNT(*) FROM rewards")
    assert cur.fetchone()[0] == 0
    cur = pool.conn.execute("SELECT COUNT(*) FROM q_updates")
    assert cur.fetchone()[0] == 0
    cur = pool.conn.execute("SELECT COUNT(*) FROM experience_edges")
    assert cur.fetchone()[0] == 0
    cur = pool.conn.execute("SELECT q_outcome, q_update_count FROM experiences")
    row = cur.fetchone()
    assert row["q_outcome"] == 0
    assert row["q_update_count"] == 0
    pool.close()
