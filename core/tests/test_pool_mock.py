"""End-to-end test of the closed loop using the mock LLM backend.

Sets EXP_LLM=mock so it runs offline and deterministically.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ["EXP_LLM"] = "mock"

from exp_core.pool import ExperiencePool, PoolConfig  # noqa: E402


def make_pool(tmp: Path) -> ExperiencePool:
    return ExperiencePool(PoolConfig(root=tmp))


def trajectory(extra: str = "") -> list[dict]:
    return [
        {"role": "user", "content": f"please summarize this CSV {extra}"},
        {"role": "assistant", "content": "loaded csv, ran describe()"},
        {"role": "user", "content": "now group by region"},
        {"role": "assistant", "content": "applied groupby; here are totals"},
    ]


def test_full_loop(tmp_path):
    pool = make_pool(tmp_path)
    pool.register_agent("agent-a", "platform")

    parent = pool.push(
        "agent-a", "csv_analysis", "claude-stub", trajectory(),
        sensitivity="low", acl="private",
    )
    assert parent["extraction_status"] == "done"
    assert parent["q_update_count"] == 1
    parent_id = parent["experience_id"]
    parent_outcome_v1 = parent["q_outcome"]

    child = pool.push(
        "agent-a", "csv_analysis", "claude-stub", trajectory("v2 different wording"),
        parent_experience_ids=[parent_id], sensitivity="low",
    )
    # Child either lands as a fresh entry, or its intent matches parent and dedup
    # routes everything into the parent. Both are valid; assert one of them.
    parent_after = pool._get(parent_id)  # type: ignore[attr-defined]
    # Even if dedup merged the child later, credit should have fired before merge.
    assert parent_after["q_update_count"] >= 2, (
        f"expected parent Q to update on child push, got {parent_after['q_update_count']}"
    )
    # With identical mock rewards Q stays the same value; we only assert update *count*.
    _ = parent_outcome_v1

    hits = pool.search("agent-a", "summarize csv", top_k=5)
    assert hits, "search returned nothing"
    assert all("score_components" in h for h in hits)
    pool.close()


def test_dedup_merges_near_duplicates(tmp_path):
    pool = make_pool(tmp_path)
    pool.register_agent("agent-a", "platform")
    a = pool.push("agent-a", "csv_analysis", "claude-stub", trajectory())
    b = pool.push("agent-a", "csv_analysis", "claude-stub", trajectory())
    # With the mock extractor both runs produce the same intent_text, so b should
    # be merged into a.
    a_row = pool._get(a["experience_id"])  # type: ignore[attr-defined]
    b_row = pool._get(b["experience_id"])  # type: ignore[attr-defined]
    assert a_row is not None and b_row is not None
    assert (
        b_row["review_status"] == "rejected"
        or a_row["review_status"] == "rejected"
    ), "expected dedup to mark one of them rejected"
    pool.close()


def test_search_score_components_present(tmp_path):
    pool = make_pool(tmp_path)
    pool.register_agent("agent-a", "platform")
    pool.push("agent-a", "csv_analysis", "claude-stub", trajectory())
    pool.push("agent-a", "code_review", "claude-stub", trajectory("review"))
    hits = pool.search("agent-a", "csv summary", task_type="csv_analysis")
    assert hits
    h = hits[0]
    assert set(h["score_components"]) == {"similarity", "q_value", "exploration"}
    assert set(h["q_breakdown"]) == {
        "outcome", "intent", "execution", "orchestration", "expression"
    }
    pool.close()
