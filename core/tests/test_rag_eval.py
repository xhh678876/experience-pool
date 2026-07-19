from __future__ import annotations

import math

from exp_core import rag_eval


def test_eval_metrics_reward_relevant_rank_position(monkeypatch):
    case = rag_eval.EvalCase(
        case_id="c1",
        query="fix warm replica retry",
        relevant_chunks={"target": 1.0},
        relevant_experiences={},
    )

    def fake_rank_loaded_chunks(conn, loaded, **kwargs):
        return [
            {
                "chunk_id": "noise",
                "experience_id": "e-noise",
                "chunk_type": "do_unit",
                "score": 0.9,
                "similarity": 0.2,
                "lexical": 0.0,
                "fts": 0.0,
            },
            {
                "chunk_id": "target",
                "experience_id": "e-target",
                "chunk_type": "do_unit",
                "score": 0.8,
                "similarity": 0.2,
                "lexical": 0.0,
                "fts": 0.0,
            },
        ]

    monkeypatch.setattr(rag_eval, "load_rank_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(rag_eval, "rank_loaded_chunks", fake_rank_loaded_chunks)

    report = rag_eval.evaluate(None, [case], top_k=2, ks=(1, 2))
    metrics = report["metrics"]

    assert metrics["Recall@1"] == 0.0
    assert metrics["Precision@1"] == 0.0
    assert metrics["Recall@2"] == 1.0
    assert metrics["Precision@2"] == 0.5
    assert metrics["MRR"] == 0.5
    assert metrics["SessionMRR"] == 0.5
    assert metrics["SessionRecall@1"] == 0.0
    assert metrics["SessionRecall@2"] == 1.0
    assert math.isclose(metrics["nDCG@2"], 1 / math.log2(3))


def test_eval_metrics_support_experience_level_labels(monkeypatch):
    case = rag_eval.EvalCase(
        case_id="c1",
        query="same experience is relevant",
        relevant_chunks={},
        relevant_experiences={"exp-1": 1.0},
    )

    def fake_rank_loaded_chunks(conn, loaded, **kwargs):
        return [
            {
                "chunk_id": "chunk-a",
                "experience_id": "exp-1",
                "chunk_type": "trajectory_segment",
                "score": 0.7,
                "similarity": 0.2,
                "lexical": 0.0,
                "fts": 0.0,
            }
        ]

    monkeypatch.setattr(rag_eval, "load_rank_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(rag_eval, "rank_loaded_chunks", fake_rank_loaded_chunks)

    report = rag_eval.evaluate(None, [case], top_k=1, ks=(1,))

    assert report["metrics"]["Recall@1"] == 1.0
    assert report["metrics"]["MRR"] == 1.0
    assert report["metrics"]["SessionRecall@1"] == 1.0
    assert report["cases"][0]["ranked"][0]["relevance"] == 1.0


def test_short_silver_query_keeps_all_retrieval_fields():
    text = """Experience unit 1 (DO, turns 20-21)
Situation: repair a warm replica in the compaction service
Action: Tool exec_command: cmd=python -m pytest tests/test_compaction.py::test_warm_replica_retry
Outcome: focused retry test passed successfully
Keywords: compaction, warm replica, retry, pytest
"""

    query = rag_eval._query_from_unit_text(text, max_chars=96)  # noqa: SLF001

    assert len(query) <= 96
    assert "pytest" in query
    assert "compaction" in query
    assert "passed" in query
    assert "warm" in query
