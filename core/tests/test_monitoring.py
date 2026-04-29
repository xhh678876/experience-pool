from __future__ import annotations

import os
from pathlib import Path

os.environ["EXP_LLM"] = "mock"

from exp_core.monitoring import (  # noqa: E402
    dashboard_stats,
    judge_drift,
    record_benchmark,
    reuse_leaderboard,
)
from exp_core.pool import ExperiencePool, PoolConfig  # noqa: E402


def trajectory(extra: str = "") -> list[dict]:
    return [
        {"role": "user", "content": f"task {extra}"},
        {"role": "assistant", "content": "did the task"},
    ]


def make_pool(tmp_path: Path) -> ExperiencePool:
    return ExperiencePool(PoolConfig(root=tmp_path))


def test_dashboard_stats_empty(tmp_path):
    pool = make_pool(tmp_path)
    stats = dashboard_stats(pool)
    assert stats["total_experiences"] == 0
    assert stats["q_scalar_p50"] == 0.0


def test_dashboard_stats_populated(tmp_path):
    pool = make_pool(tmp_path)
    pool.register_agent("a", "platform")
    pool.register_agent("b", "data")
    e1 = pool.push("a", "csv_analysis", "stub", trajectory("v1"))
    e2 = pool.push(
        "b", "csv_analysis", "stub", trajectory("v2"),
        parent_experience_ids=[e1["experience_id"]],
    )
    stats = dashboard_stats(pool)
    assert stats["total_experiences"] == 2
    assert "csv_analysis" in stats["by_task_type"]
    assert stats["q_scalar_p50"] > 0
    assert stats["judge_confidence_p50"] > 0
    pool.close()


def test_reuse_leaderboard_orders_by_reuse(tmp_path):
    pool = make_pool(tmp_path)
    pool.register_agent("a", "p")
    e1 = pool.push("a", "t", "stub", trajectory("a"))
    e2 = pool.push("a", "t", "stub", trajectory("b"))
    e3 = pool.push(
        "a", "t", "stub", trajectory("c"),
        parent_experience_ids=[e1["experience_id"], e2["experience_id"]],
    )
    board = reuse_leaderboard(pool, top_k=10)
    assert board, "expected at least one entry"
    # whichever wasn't the child should have reuse_count >= 1
    ids_with_reuse = {b["experience_id"] for b in board if b["reuse_count"] >= 1}
    assert ids_with_reuse, board
    pool.close()


def test_drift_baseline_round_trip(tmp_path):
    pool = make_pool(tmp_path)
    pool.register_agent("a", "p")
    e1 = pool.push("a", "t", "stub", trajectory("once"))
    baseline_path = tmp_path / "baseline.json"
    snap = record_benchmark(pool, [e1["experience_id"]], "weekly_v1", baseline_path)
    assert snap["label"] == "weekly_v1"
    assert len(snap["items"]) == 1

    report = judge_drift(pool, baseline_path)
    # Mock LLM is deterministic, drift should be ~0.
    assert report.n_items == 1
    assert report.overall_mad < 0.05
    assert not report.triggered
    pool.close()
