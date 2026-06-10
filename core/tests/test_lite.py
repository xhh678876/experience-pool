"""v0 lite path tests. Mock LLM, no real Claude calls."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("EXP_LLM", "mock")
os.environ.setdefault("EXP_DEFER_OPF", "1")

import pytest  # noqa: E402

from exp_core import lite as lite_mod  # noqa: E402
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
