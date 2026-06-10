"""Monitoring + judge drift detection.

Three things this module provides:

1. `dashboard_stats(pool)`: pool-level health snapshot (counts, Q distribution
   buckets, top-K reused, judge confidence percentiles, daily ingestion rate).

2. `judge_drift(pool, benchmark_path)`: re-judges a fixed benchmark set with
   the current judge prompt + model and compares scores to a stored baseline.
   Drift = mean absolute deviation per dimension. Above 0.15 it flags.

3. `record_benchmark(pool, ids, label, baseline_path)`: snapshot the current
   reward of a chosen set of experience_ids as the baseline JSON file. Run
   this once when you trust the judge; later compare against it.

The drift loop is intentionally explicit (operator runs it on a cadence)
rather than automatic — we don't want to spend judge tokens on every pool
change. CLI exposes `expctl drift` to do this.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .pool import DIMS, ExperiencePool
from .prompts import render_judge


DRIFT_THRESHOLD_ABS = 0.15  # mean absolute per-dim deviation that triggers an alert


@dataclass
class DriftReport:
    benchmark_label: str
    n_items: int
    per_dim_mad: dict[str, float]  # mean absolute deviation per dim
    overall_mad: float
    triggered: bool
    item_details: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_label": self.benchmark_label,
            "n_items": self.n_items,
            "per_dim_mad": self.per_dim_mad,
            "overall_mad": self.overall_mad,
            "triggered": self.triggered,
            "items": self.item_details,
        }


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[idx]


def _histogram(values: list[float], buckets: int = 10) -> list[dict[str, Any]]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if lo == hi:
        return [{"lo": lo, "hi": hi, "count": len(values)}]
    width = (hi - lo) / buckets
    counts = [0] * buckets
    for v in values:
        i = min(buckets - 1, int((v - lo) / width))
        counts[i] += 1
    return [
        {"lo": lo + i * width, "hi": lo + (i + 1) * width, "count": counts[i]}
        for i in range(buckets)
    ]


def dashboard_stats(pool: ExperiencePool) -> dict[str, Any]:
    cur = pool.conn.execute(
        """
        SELECT q_outcome, q_intent, q_execution, q_orchestration, q_expression,
               reuse_count, visit_count, q_update_count, review_status,
               sensitivity, task_type, created_at, intent_text, experience_id
        FROM experiences
        """
    )
    rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        return {
            "total_experiences": 0,
            "by_review_status": {},
            "by_task_type": {},
            "by_sensitivity": {},
            "q_scalar_histogram": [],
            "q_scalar_p50": 0.0,
            "q_scalar_p90": 0.0,
            "judge_confidence_p50": 0.0,
            "top_reused": [],
            "ingestion_last_7_days": 0,
        }

    q_scalars = [
        0.30 * r["q_outcome"] + 0.20 * r["q_intent"] + 0.20 * r["q_execution"]
        + 0.15 * r["q_orchestration"] + 0.15 * r["q_expression"]
        for r in rows
    ]

    by_status: dict[str, int] = {}
    by_task: dict[str, int] = {}
    by_sens: dict[str, int] = {}
    for r in rows:
        by_status[r["review_status"]] = by_status.get(r["review_status"], 0) + 1
        by_task[r["task_type"]] = by_task.get(r["task_type"], 0) + 1
        by_sens[r["sensitivity"]] = by_sens.get(r["sensitivity"], 0) + 1

    cur = pool.conn.execute(
        "SELECT confidence FROM rewards WHERE confidence IS NOT NULL"
    )
    confs = [r[0] for r in cur.fetchall()]

    top_reused = sorted(
        rows, key=lambda r: (r["reuse_count"], r["q_update_count"]), reverse=True
    )[:10]

    cur = pool.conn.execute(
        """
        SELECT COUNT(*) FROM experiences
        WHERE created_at >= datetime('now', '-7 days')
        """
    )
    ingestion_7d = cur.fetchone()[0]

    return {
        "total_experiences": len(rows),
        "by_review_status": by_status,
        "by_task_type": by_task,
        "by_sensitivity": by_sens,
        "q_scalar_histogram": _histogram(q_scalars),
        "q_scalar_p50": _percentile(q_scalars, 50),
        "q_scalar_p90": _percentile(q_scalars, 90),
        "judge_confidence_p50": _percentile(confs, 50) if confs else 0.0,
        "judge_confidence_p10": _percentile(confs, 10) if confs else 0.0,
        "top_reused": [
            {
                "experience_id": r["experience_id"],
                "intent_text": r["intent_text"],
                "reuse_count": r["reuse_count"],
                "q_update_count": r["q_update_count"],
            }
            for r in top_reused
            if r["reuse_count"] > 0
        ],
        "ingestion_last_7_days": ingestion_7d,
    }


def record_benchmark(
    pool: ExperiencePool,
    experience_ids: list[str],
    label: str,
    baseline_path: Path,
) -> dict[str, Any]:
    """Snapshot the latest reward for each id as the trusted baseline."""
    items = []
    for eid in experience_ids:
        cur = pool.conn.execute(
            """
            SELECT r_outcome, r_intent, r_execution, r_orchestration, r_expression, confidence
            FROM rewards WHERE experience_id = ? ORDER BY created_at DESC LIMIT 1
            """,
            (eid,),
        )
        row = cur.fetchone()
        if row is None:
            continue
        items.append({
            "experience_id": eid,
            "baseline": {d: row[f"r_{d}"] for d in DIMS},
            "confidence": row["confidence"],
        })
    payload = {"label": label, "items": items}
    baseline_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return payload


def judge_drift(pool: ExperiencePool, baseline_path: Path) -> DriftReport:
    """Re-judge each baseline item with the CURRENT prompt + model and report drift."""
    from . import llm  # local to avoid import-time cycles

    baseline = json.loads(baseline_path.read_text())
    label = baseline["label"]
    items = baseline["items"]

    per_dim_diffs: dict[str, list[float]] = {d: [] for d in DIMS}
    item_details = []
    for item in items:
        eid = item["experience_id"]
        cur = pool.conn.execute(
            "SELECT trajectory_path, intent_text, summary, script_steps FROM experiences WHERE experience_id = ?",
            (eid,),
        )
        row = cur.fetchone()
        if row is None:
            continue
        try:
            traj = json.loads(Path(row["trajectory_path"]).read_text())["trajectory"]
        except Exception:  # noqa: BLE001
            continue
        card = {
            "intent_text": row["intent_text"],
            "summary": row["summary"],
            "script_steps": json.loads(row["script_steps"] or "[]"),
        }
        sys, prompt = render_judge(traj, card)
        try:
            new = llm.call_json(prompt, system=sys)
        except Exception as e:  # noqa: BLE001
            item_details.append({"experience_id": eid, "error": str(e)})
            continue
        diffs = {d: abs(float(new.get(d, 0.0)) - float(item["baseline"][d])) for d in DIMS}
        for d in DIMS:
            per_dim_diffs[d].append(diffs[d])
        item_details.append({
            "experience_id": eid,
            "baseline": item["baseline"],
            "current": {d: float(new.get(d, 0.0)) for d in DIMS},
            "diffs": diffs,
        })

    per_dim_mad = {
        d: (statistics.mean(v) if v else 0.0) for d, v in per_dim_diffs.items()
    }
    overall_mad = statistics.mean(per_dim_mad.values()) if per_dim_mad else 0.0
    return DriftReport(
        benchmark_label=label,
        n_items=len(item_details),
        per_dim_mad=per_dim_mad,
        overall_mad=overall_mad,
        triggered=overall_mad > DRIFT_THRESHOLD_ABS,
        item_details=item_details,
    )


def reuse_leaderboard(pool: ExperiencePool, top_k: int = 20) -> list[dict[str, Any]]:
    cur = pool.conn.execute(
        """
        SELECT experience_id, intent_text, task_type, reuse_count, q_update_count,
               q_outcome, q_intent, q_execution, q_orchestration, q_expression
        FROM experiences
        WHERE review_status IN ('approved', 'auto_approved', 'edited')
        ORDER BY reuse_count DESC, q_update_count DESC
        LIMIT ?
        """,
        (top_k,),
    )
    out = []
    for r in cur.fetchall():
        d = dict(r)
        d["q_scalar"] = (
            0.30 * d["q_outcome"] + 0.20 * d["q_intent"] + 0.20 * d["q_execution"]
            + 0.15 * d["q_orchestration"] + 0.15 * d["q_expression"]
        )
        out.append(d)
    return out
