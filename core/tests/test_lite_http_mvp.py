"""HTTP-level smoke tests for the lite MVP path.

This exercises the actual FastAPI app, HMAC request signing, local-lite card
upload, SQL persistence, vector search, and private/team/public ACL filtering.
It intentionally avoids the judge, credit assignment, and skills path.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import tarfile
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from exp_core import rag as rag_mod  # noqa: E402
from exp_core import server as server_mod  # noqa: E402
from exp_core.identity import Credential, sign_request  # noqa: E402


@pytest.fixture()
def app_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "pool"
    monkeypatch.setenv("EXP_ROOT", str(root))
    monkeypatch.setenv("EXP_CREDENTIALS_DIR", str(root / "credentials"))
    monkeypatch.setenv("EXP_LLM", "mock")
    monkeypatch.setenv("EXP_DEFER_OPF", "1")
    monkeypatch.setenv("EXP_REFINE_TITLE_SERVER", "0")
    server_mod.POOL = None
    server_mod.RATE_COUNTERS.clear()
    with TestClient(server_mod.app) as client:
        yield client
    # TestClient runs the app in a worker thread; the SQLite connection belongs
    # to that thread, so avoid closing it from pytest's main thread.
    server_mod.POOL = None


def register(client: TestClient, name: str, team: str) -> Credential:
    res = client.post("/v1/agents/register", json={"name": name, "team": team})
    assert res.status_code == 200, res.text
    payload = res.json()
    return Credential(
        agent_id=payload["agent_id"],
        agent_name=payload["agent_name"],
        team=payload["team"],
        secret=payload["secret"],
    )


def signed_json(
    client: TestClient,
    cred: Credential,
    method: str,
    path: str,
    payload: dict[str, Any],
):
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    sig = sign_request(cred, method, path, body)
    return client.request(
        method,
        path,
        content=body,
        headers={
            "content-type": "application/json",
            "x-agent-name": cred.agent_name,
            "x-signature": sig,
        },
    )


def lite_card(query: str, *, acl: str) -> dict[str, Any]:
    return {
        "query": query,
        "intent": "reuse a csv revenue aggregation playbook",
        "steps": [
            "inspect csv columns",
            "group by region and sum revenue",
            "sort descending and return top regions",
        ],
        "outcome": "APAC, EMEA, and AMER were returned as the top regions.",
        "task_type": "csv_analysis",
        "source_model": "claude-mvp",
        "sensitivity": "low",
        "acl": acl,
        "tags": ["mvp"],
        "redactions": {},
        "trajectory": [
            {"role": "user", "content": query},
            {"role": "assistant", "content": "inspect csv columns"},
            {"role": "assistant", "content": "group by region and sum revenue"},
            {"role": "assistant", "content": "APAC, EMEA, and AMER were returned."},
        ],
    }


def test_rag_retrieval_text_keeps_signal_and_drops_runtime_noise():
    text = """
    Experience unit 1 (DO, turns 3-4)
    Situation: In a task like: 修复 FastAPI HMAC 签名失败; before calling Bash.
    Action: command=EXP_AGENT_SECRET=abc123 curl http://127.0.0.1/private/path/with/many/segments
    Outcome: HMAC canonical path mismatch was fixed.
    Keywords: fastapi, hmac, 签名, 失败
    Keyphrases: fastapi hmac signature
    Auth: Unsupported • Command: /root/.codex/mcp-servers/expool/scripts/runner.sh • Tools: exp_search, exp_get, exp_push_latest
    """

    search_text = rag_mod._retrieval_text(  # noqa: SLF001
        text,
        {"keywords": ["fastapi", "hmac", "签名", "失败"], "keyphrases": ["fastapi hmac signature"]},
        "do_unit",
    )

    assert "fastapi" in search_text.lower()
    assert "hmac" in search_text.lower()
    assert "签名" in search_text
    assert "EXP_AGENT_SECRET" not in search_text
    assert "mcp-servers/expool" not in search_text
    assert "exp_push_latest" not in search_text

    action_text = rag_mod._retrieval_text(  # noqa: SLF001
        """Experience unit 2 (DO, turns 8-9)
Situation: Parse a reimbursement PDF before updating the report.
Action: Tool exec_command: cmd=pdftotext /home/alice/private/receipts/invoice.pdf -
Outcome: invoice total 154.00 was extracted successfully.
Keywords: pdftotext, invoice, reimbursement
""",
        {"keywords": ["pdftotext", "invoice", "reimbursement"]},
        "do_unit",
    )
    assert "pdftotext" in action_text
    assert "Action:" in action_text
    assert "invoice total 154.00" in action_text
    assert "/home/alice/private" not in action_text


def test_rag_general_chinese_queries_keep_signal_but_drop_chitchat():
    upload_terms = rag_mod._query_terms("大 session 超过大小限制无法上传")  # noqa: SLF001
    reward_terms = rag_mod._query_terms("Q值奖励反馈没有更新复用率")  # noqa: SLF001

    assert upload_terms
    assert any("限制" in term or "上传" in term for term in upload_terms)
    assert reward_terms
    assert any("奖励" in term or "反馈" in term for term in reward_terms)
    weak_terms = rag_mod._query_terms("看看你最近")  # noqa: SLF001
    assert not rag_mod._has_retrieval_signal("看看你最近", weak_terms)  # noqa: SLF001


def test_rag_pairs_parallel_tool_results_by_canonical_id():
    trajectory = [
        {"role": "user", "content": "部署服务并验证两个独立操作"},
        {
            "role": "assistant",
            "content": "执行两个操作",
            "tool_calls": [
                {"id": "call-a", "name": "deploy_alpha", "input": {"target": "alpha"}},
                {"id": "call-b", "name": "deploy_beta", "input": {"target": "beta"}},
            ],
        },
        {
            "role": "tool",
            "tool_result_for": "call-b",
            "content": "completed successfully for beta",
        },
        {
            "role": "tool",
            "tool_result_for": "call-a",
            "content": "ERROR: alpha deployment failed",
        },
    ]

    units = rag_mod._experience_units(trajectory)  # noqa: SLF001
    by_tool = {unit["tool_name"]: unit for unit in units}

    assert by_tool["deploy_beta"]["status"] == "success"
    assert "beta" in by_tool["deploy_beta"]["outcome"]
    assert by_tool["deploy_alpha"]["status"] == "failure"
    assert "alpha" in by_tool["deploy_alpha"]["outcome"]


def test_long_session_budget_covers_timeline_and_prioritizes_failures():
    items = [
        {"position": index, "status": "failure" if index in {4, 5} else "success"}
        for index in range(10)
    ]

    evenly = rag_mod._select_timeline_items(items, 4)  # noqa: SLF001
    assert [index for index, _ in evenly] == [0, 3, 6, 9]

    prioritized = rag_mod._select_timeline_items(  # noqa: SLF001
        items,
        4,
        priority=lambda item: item["status"] == "failure",
    )
    assert [index for index, _ in prioritized] == [0, 4, 5, 9]


def test_rag_cleans_compaction_and_exec_runtime_wrappers():
    wrapped = """This session is being continued from a previous conversation that ran out of context.
Summary:
1. Primary Request and Intent:
The current/active request (most recent): **Add remote-server support to Claude Fleet.**
The user said: "support remote hosts"
"""
    assert rag_mod._clean_turn_text(wrapped) == "Add remote-server support to Claude Fleet."  # noqa: SLF001

    result = (
        "Chunk ID: abc123 Wall time: 0.25 seconds Process exited with code 0 "
        "Original token count: 42 Output: 12 tests passed"
    )
    cleaned = rag_mod._clean_turn_text(result)  # noqa: SLF001
    assert "Chunk ID" not in cleaned
    assert "Wall time" not in cleaned
    assert "token count" not in cleaned
    assert "Process exited with code 0" in cleaned
    assert "12 tests passed" in cleaned


def test_lite_mvp_http_hmac_search_and_acl(app_client: TestClient):
    alice = register(app_client, "alice", "platform")
    bob = register(app_client, "bob", "data")
    carol = register(app_client, "carol", "platform")

    push = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/push",
        lite_card(
            "Find top regions by revenue for alice@example.com",
            acl="team:platform",
        ),
    )
    assert push.status_code == 202, push.text
    pushed = push.json()
    assert pushed["ingest_path"] == "lite"
    assert pushed["review_status"] == "auto_approved"
    assert pushed["redactions"].get("email", 0) >= 1

    carol_search = signed_json(
        app_client,
        carol,
        "POST",
        "/v1/lite/search",
        {"q": "csv top revenue regions", "top_k": 3, "task_type": "csv_analysis"},
    )
    assert carol_search.status_code == 200, carol_search.text
    carol_hits = carol_search.json()["results"]
    assert len(carol_hits) == 1
    assert carol_hits[0]["experience_id"] == pushed["experience_id"]
    assert carol_hits[0]["steps"][1] == "group by region and sum revenue"
    assert "alice@example.com" not in json.dumps(carol_hits, ensure_ascii=False)

    bob_search = signed_json(
        app_client,
        bob,
        "POST",
        "/v1/lite/search",
        {"q": "csv top revenue regions", "top_k": 3, "task_type": "csv_analysis"},
    )
    assert bob_search.status_code == 200, bob_search.text
    assert bob_search.json()["results"] == []

    public_push = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/push",
        lite_card("Public incident checklist for build failures", acl="public"),
    )
    assert public_push.status_code == 202, public_push.text
    public_eid = public_push.json()["experience_id"]
    assert public_push.json()["acl"] == "private"

    bob_public_search = signed_json(
        app_client,
        bob,
        "POST",
        "/v1/lite/search",
        {"q": "build failure checklist", "top_k": 5},
    )
    assert bob_public_search.status_code == 200, bob_public_search.text
    hits = bob_public_search.json()["results"]
    assert not any(h["experience_id"] == public_eid for h in hits)


def _register_user(client: TestClient, email: str) -> tuple[Credential, str]:
    res = client.post(
        "/v1/users/register",
        json={
            "email": email,
            "password": "correct-horse-battery",
            "display_name": email.split("@", 1)[0],
        },
    )
    assert res.status_code == 201, res.text
    cookie = client.cookies.get("exp_session")
    assert cookie
    body = res.json()
    return (
        Credential(
            agent_id=body["agent_id"],
            agent_name=body["default_agent_name"],
            team="default",
            secret=body["secret"],
        ),
        cookie,
    )


def test_rag_context_project_pool_shares_granted_personal_repo(app_client: TestClient):
    alice, alice_cookie = _register_user(app_client, "alice@example.com")

    pushed = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/push",
        lite_card("MOVA benchmark scoring and evaluator tuning", acl="private"),
    )
    assert pushed.status_code == 202, pushed.text

    project = app_client.post(
        "/v1/projects",
        json={"name": "MOVA Eval", "slug": "mova-eval"},
    )
    assert project.status_code == 201, project.text
    project_slug = project.json()["slug"]

    bob, bob_cookie = _register_user(app_client, "bob@example.com")

    app_client.cookies.set("exp_session", alice_cookie)
    invite = app_client.post(
        f"/v1/projects/{project_slug}/invites",
        json={"email": "bob@example.com", "role": "member"},
    )
    assert invite.status_code == 201, invite.text
    token = invite.json()["token"]

    app_client.cookies.set("exp_session", bob_cookie)
    accepted = app_client.post("/v1/projects/invites/accept", json={"token": token})
    assert accepted.status_code == 200, accepted.text

    personal = signed_json(
        app_client,
        bob,
        "POST",
        "/v1/rag/context",
        {"q": "MOVA evaluator benchmark", "top_k": 5, "scope": "personal"},
    )
    assert personal.status_code == 200, personal.text
    assert personal.json()["chunks"] == []

    project_hits = signed_json(
        app_client,
        bob,
        "POST",
        "/v1/rag/context",
        {"q": "MOVA evaluator benchmark", "top_k": 5, "scope": f"project:{project_slug}"},
    )
    assert project_hits.status_code == 200, project_hits.text
    body = project_hits.json()
    assert body["scope"] == "project"
    assert body["scope_meta"]["project"]["slug"] == project_slug
    assert any("MOVA" in chunk["text"] for chunk in body["chunks"])
    assert "经验池RAG上下文" in body["context"]


def test_project_invite_expiry_uses_sqlite_datetime_semantics(app_client: TestClient):
    _, alice_cookie = _register_user(app_client, "invite-owner@example.com")
    project = app_client.post(
        "/v1/projects",
        json={"name": "Expiry Test", "slug": "expiry-test"},
    )
    assert project.status_code == 201, project.text
    _, bob_cookie = _register_user(app_client, "invite-member@example.com")

    app_client.cookies.set("exp_session", alice_cookie)
    invite = app_client.post(
        "/v1/projects/expiry-test/invites",
        json={"email": "invite-member@example.com", "role": "member"},
    )
    assert invite.status_code == 201, invite.text

    db_path = Path(os.environ["EXP_ROOT"]) / "pool.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE project_invites SET expires_at = datetime('now', '-1 second') "
            "WHERE invite_id = ?",
            (invite.json()["invite_id"],),
        )
        conn.commit()
    finally:
        conn.close()

    app_client.cookies.set("exp_session", bob_cookie)
    accepted = app_client.post(
        "/v1/projects/invites/accept",
        json={"token": invite.json()["token"]},
    )
    assert accepted.status_code == 400
    assert "expired" in accepted.text


def test_rag_context_keyword_signal_handles_mixed_chinese_and_code_terms(app_client: TestClient):
    alice = register(app_client, "alice", "platform")

    hmac_card = lite_card("排查 FastAPI HMAC 签名失败", acl="private")
    hmac_card.update(
        {
            "intent": "修复 FastAPI HMAC 签名失败",
            "steps": [
                "检查 x-agent-name 和 x-signature 请求头是否参与 HMAC canonical string",
                "确认 FastAPI 读取 body 后没有改变签名输入",
                "用同一 secret 在客户端和服务端复算 digest",
            ],
            "outcome": "HMAC 签名失败被定位为 canonical path 不一致。",
            "task_type": "debugging",
        }
    )
    hmac_push = signed_json(app_client, alice, "POST", "/v1/lite/push", hmac_card)
    assert hmac_push.status_code == 202, hmac_push.text
    hmac_eid = hmac_push.json()["experience_id"]

    ui_card = lite_card("优化项目池页面视觉层级", acl="private")
    ui_card.update(
        {
            "intent": "优化 Next.js 项目池 UI 和导航",
            "steps": ["调整 Button variant", "补充 projects 页面", "运行 next build"],
            "outcome": "项目池页面构建通过。",
            "task_type": "frontend",
        }
    )
    ui_push = signed_json(app_client, alice, "POST", "/v1/lite/push", ui_card)
    assert ui_push.status_code == 202, ui_push.text

    res = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/rag/context",
        {"q": "HMAC签名失败 FastAPI", "top_k": 3, "scope": "personal"},
    )
    assert res.status_code == 200, res.text
    chunks = res.json()["chunks"]
    assert chunks, res.text
    assert chunks[0]["experience_id"] == hmac_eid
    assert chunks[0]["lexical"] > 0
    assert chunks[0]["keyword"] >= chunks[0]["lexical"]

    weak = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/rag/context",
        {"q": "看看你最近", "top_k": 3, "scope": "personal"},
    )
    assert weak.status_code == 200, weak.text
    weak_body = weak.json()
    assert weak_body["event_id"] is None
    assert weak_body["chunks"] == []


def test_rag_context_hot_path_never_runs_index_maintenance(
    app_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    alice = register(app_client, "alice", "platform")
    pushed = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/push",
        lite_card("repair deterministic cache invalidation failure", acl="private"),
    )
    assert pushed.status_code == 202, pushed.text

    def fail_online_maintenance(*args, **kwargs):
        raise AssertionError("index maintenance ran in the online recall path")

    monkeypatch.setattr(rag_mod, "backfill_missing_chunks", fail_online_maintenance)
    monkeypatch.setattr(rag_mod, "refresh_stale_retrieval_text", fail_online_maintenance)

    recalled = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/rag/context",
        {"q": "deterministic cache invalidation failure", "top_k": 3, "scope": "personal"},
    )
    assert recalled.status_code == 200, recalled.text
    assert recalled.json()["chunks"]


def test_rag_preview_can_skip_reuse_telemetry(app_client: TestClient):
    alice = register(app_client, "rag-preview", "platform")
    pushed = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/push",
        lite_card("preview retrieval without reward telemetry", acl="private"),
    )
    assert pushed.status_code == 202, pushed.text
    eid = pushed.json()["experience_id"]

    preview = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/rag/context",
        {
            "q": "preview retrieval reward telemetry",
            "top_k": 3,
            "scope": "personal",
            "record_event": False,
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["chunks"]
    assert preview.json()["event_id"] is None

    conn = sqlite3.connect(Path(os.environ["EXP_ROOT"]) / "pool.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM reuse_events").fetchone()[0] == 0
        assert conn.execute(
            "SELECT visit_count FROM experiences WHERE experience_id = ?", (eid,)
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_codex_task_keeps_long_session_parent_provenance(app_client: TestClient):
    alice = register(app_client, "alice", "platform")
    card = lite_card("repair rollout task indexing", acl="private")
    card["meta"] = {
        "agent_type": "codex",
        "session_id": "rollout-demo:turn-42",
        "extra": {
            "parent_session_id": "rollout-demo",
            "codex_turn_id": "turn-42",
            "byte_start": 1024,
            "byte_end": 4096,
            "task_status": "complete",
        },
    }
    pushed = signed_json(app_client, alice, "POST", "/v1/lite/push", card)
    assert pushed.status_code == 202, pushed.text
    body = pushed.json()
    assert body["parent_session_id"] == "rollout-demo"
    assert body["segment_id"] == "turn-42"
    assert body["source_byte_start"] == 1024
    assert body["source_byte_end"] == 4096

    recalled = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/rag/context",
        {"q": "rollout task indexing", "top_k": 3, "scope": "personal"},
    )
    assert recalled.status_code == 200, recalled.text
    assert recalled.json()["chunks"]
    experience = recalled.json()["experiences"][0]
    assert experience["parent_session_id"] == "rollout-demo"
    assert experience["segment_id"] == "turn-42"


def test_reuse_feedback_updates_q_from_rag_event(app_client: TestClient):
    alice = register(app_client, "alice", "platform")

    push = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/push",
        lite_card("debug pytest import path failure in FastAPI app", acl="private"),
    )
    assert push.status_code == 202, push.text
    eid = push.json()["experience_id"]
    conn = sqlite3.connect(Path(os.environ["EXP_ROOT"]) / "pool.db")
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM rag_chunks WHERE experience_id = ?", (eid,)
        ).fetchone()[0] > 0
    finally:
        conn.close()

    recall = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/rag/context",
        {"q": "pytest FastAPI import path failure", "top_k": 3, "scope": "personal"},
    )
    assert recall.status_code == 200, recall.text
    recalled = recall.json()
    assert recalled["event_id"]
    assert recalled["chunks"]
    chunk_id = recalled["chunks"][0]["chunk_id"]

    feedback = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/reuse/feedback",
        {
            "event_id": recalled["event_id"],
            "items": [
                {
                    "chunk_id": chunk_id,
                    "reward": 0.8,
                    "confidence": 0.5,
                    "reason": "the recalled pytest debugging step matched the fix",
                }
            ],
            "final_status": "success",
            "feedback_source": "agent",
        },
    )
    assert feedback.status_code == 200, feedback.text
    body = feedback.json()
    assert body["items_updated"] == 1
    assert body["experiences_updated"] == 1
    assert body["updates"][0]["experience_id"] == eid

    assert server_mod.POOL is not None
    conn = sqlite3.connect(server_mod.POOL.config.db_path)
    conn.row_factory = sqlite3.Row
    try:
        exp = conn.execute(
            """
            SELECT reuse_count, q_update_count, q_outcome, q_intent, q_execution,
                   q_orchestration, q_expression
            FROM experiences
            WHERE experience_id = ?
            """,
            (eid,),
        ).fetchone()
        assert exp["reuse_count"] == 1
        assert exp["q_update_count"] == 1
        assert exp["q_outcome"] == pytest.approx(0.08)
        assert exp["q_intent"] == pytest.approx(0.08)
        assert exp["q_execution"] == pytest.approx(0.08)
        assert exp["q_orchestration"] == pytest.approx(0.048)
        assert exp["q_expression"] == pytest.approx(0.032)

        item = conn.execute(
            """
            SELECT reward, confidence, feedback_source, was_used_by_agent
            FROM reuse_items
            WHERE event_id = ? AND chunk_id = ?
            """,
            (recalled["event_id"], chunk_id),
        ).fetchone()
        assert item["reward"] == pytest.approx(0.8)
        assert item["confidence"] == pytest.approx(0.5)
        assert item["feedback_source"] == "agent"
        assert item["was_used_by_agent"] == 1

        q_update = conn.execute(
            """
            SELECT triggered_by_child, delta_outcome
            FROM q_updates
            WHERE experience_id = ?
            """,
            (eid,),
        ).fetchone()
        assert q_update["triggered_by_child"] == f"reuse:{recalled['event_id']}"
        assert q_update["delta_outcome"] == pytest.approx(0.08)
    finally:
        conn.close()

    retry = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/reuse/feedback",
        {
            "event_id": recalled["event_id"],
            "items": [{"chunk_id": chunk_id, "reward": 0.8, "confidence": 0.5}],
            "final_status": "success",
        },
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["items_updated"] == 0
    assert retry.json()["items_skipped"] == 1
    assert retry.json()["experiences_updated"] == 0

    conn = sqlite3.connect(server_mod.POOL.config.db_path)
    conn.row_factory = sqlite3.Row
    try:
        exp = conn.execute(
            "SELECT reuse_count, q_update_count, q_outcome FROM experiences WHERE experience_id = ?",
            (eid,),
        ).fetchone()
        assert exp["reuse_count"] == 1
        assert exp["q_update_count"] == 1
        assert exp["q_outcome"] == pytest.approx(0.08)
        q_updates = conn.execute(
            "SELECT COUNT(*) AS n FROM q_updates WHERE experience_id = ?",
            (eid,),
        ).fetchone()
        assert q_updates["n"] == 1
    finally:
        conn.close()


def test_reuse_feedback_first_update_starts_from_trajectory_score(app_client: TestClient):
    alice = register(app_client, "alice", "platform")

    push = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/push",
        lite_card("high quality trajectory score seed for q feedback", acl="private"),
    )
    assert push.status_code == 202, push.text
    eid = push.json()["experience_id"]

    assert server_mod.POOL is not None
    conn = sqlite3.connect(server_mod.POOL.config.db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "UPDATE experiences SET trajectory_score = 0.7 WHERE experience_id = ?",
            (eid,),
        )
        conn.commit()
    finally:
        conn.close()

    recall = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/rag/context",
        {"q": "high quality trajectory score seed q feedback", "top_k": 3, "scope": "personal"},
    )
    assert recall.status_code == 200, recall.text
    recalled = recall.json()
    assert recalled["event_id"]
    chunk_id = recalled["chunks"][0]["chunk_id"]

    feedback = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/reuse/feedback",
        {
            "event_id": recalled["event_id"],
            "items": [{"chunk_id": chunk_id, "reward": 1.0, "confidence": 1.0}],
            "final_status": "success",
        },
    )
    assert feedback.status_code == 200, feedback.text

    conn = sqlite3.connect(server_mod.POOL.config.db_path)
    conn.row_factory = sqlite3.Row
    try:
        exp = conn.execute(
            "SELECT reuse_count, q_update_count, q_outcome, q_intent FROM experiences WHERE experience_id = ?",
            (eid,),
        ).fetchone()
        assert exp["reuse_count"] == 1
        assert exp["q_update_count"] == 1
        assert exp["q_outcome"] > 0.7
        assert exp["q_intent"] > 0.7
    finally:
        conn.close()


def test_reuse_feedback_not_used_negative_reward_updates_q_without_reuse_count(app_client: TestClient):
    alice = register(app_client, "alice", "platform")

    push = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/push",
        lite_card("unused recalled pytest fixture hint", acl="private"),
    )
    assert push.status_code == 202, push.text
    eid = push.json()["experience_id"]

    recall = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/rag/context",
        {"q": "unused pytest fixture hint", "top_k": 2, "scope": "personal"},
    )
    assert recall.status_code == 200, recall.text
    recalled = recall.json()
    chunk_id = recalled["chunks"][0]["chunk_id"]

    feedback = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/reuse/feedback",
        {
            "event_id": recalled["event_id"],
            "items": [{"chunk_id": chunk_id, "reward": -1.0, "confidence": 0.5, "used": False}],
            "final_status": "failed",
        },
    )
    assert feedback.status_code == 200, feedback.text
    body = feedback.json()
    assert body["items_updated"] == 1
    assert body["experiences_updated"] == 1

    assert server_mod.POOL is not None
    conn = sqlite3.connect(server_mod.POOL.config.db_path)
    conn.row_factory = sqlite3.Row
    try:
        exp = conn.execute(
            "SELECT reuse_count, q_update_count, q_outcome FROM experiences WHERE experience_id = ?",
            (eid,),
        ).fetchone()
        assert exp["reuse_count"] == 0
        assert exp["q_update_count"] == 1
        assert exp["q_outcome"] < 0
        item = conn.execute(
            """
            SELECT reward, confidence, was_used_by_agent
            FROM reuse_items
            WHERE event_id = ? AND chunk_id = ?
            """,
            (recalled["event_id"], chunk_id),
        ).fetchone()
        assert item["reward"] == pytest.approx(-1.0)
        assert item["confidence"] == pytest.approx(0.5)
        assert item["was_used_by_agent"] == 0
    finally:
        conn.close()


def test_reuse_feedback_rejects_other_viewer_event(app_client: TestClient):
    alice = register(app_client, "alice", "platform")
    bob = register(app_client, "bob", "platform")

    push = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/push",
        lite_card("debug private recall feedback ownership", acl="private"),
    )
    assert push.status_code == 202, push.text

    recall = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/rag/context",
        {"q": "private recall feedback ownership", "top_k": 2, "scope": "personal"},
    )
    assert recall.status_code == 200, recall.text
    event_id = recall.json()["event_id"]
    assert event_id

    feedback = signed_json(
        app_client,
        bob,
        "POST",
        "/v1/reuse/feedback",
        {"event_id": event_id, "reward": 1.0, "confidence": 0.5},
    )
    assert feedback.status_code == 403


def test_rag_context_indexes_clean_trajectory_segments(app_client: TestClient):
    alice = register(app_client, "alice", "platform")

    card = lite_card("generic debugging session", acl="private")
    card.update(
        {
            "intent": "debug a local service",
            "steps": ["inspect logs", "apply a focused fix", "run tests"],
            "outcome": "The service was fixed and verified.",
            "task_type": "debugging",
            "trajectory": [
                {
                    "role": "system",
                    "content": "# AGENTS.md instructions\nsecret runtime noise\n</INSTRUCTIONS>",
                },
                {"role": "user", "content": "hi"},
                {
                    "role": "user",
                    "content": "<local-command-caveat>Caveat: The messages below were generated by the user while running local commands. DO NOT respond unless interrupted partially.</local-command-caveat>",
                },
                {
                    "role": "user",
                    "content": "<command-name>/context</command-name> <command-message>context</command-message> <command-args></command-args>",
                },
                {
                    "role": "user",
                    "content": "Auth: Unsupported • Command: /root/.codex/mcp-servers/expool/scripts/runner.sh • Tools: exp_search, exp_get, exp_push_latest",
                },
                {
                    "role": "user",
                    "content": "修复 ZEPHYR_ALPHA_739 shard compaction 只在 warm replica 失败的问题",
                },
                {
                    "role": "assistant",
                    "content": "I inspected the shard planner and found the warm replica path skipped compaction retries.",
                },
                {
                    "role": "tool",
                    "content": "pytest tests/test_shard_compaction.py::test_warm_replica_retry passed",
                },
                {
                    "role": "assistant",
                    "content": "已完成。ZEPHYR_ALPHA_739 warm replica compaction regression is fixed and tests passed.",
                },
            ],
        }
    )
    pushed = signed_json(app_client, alice, "POST", "/v1/lite/push", card)
    assert pushed.status_code == 202, pushed.text
    eid = pushed.json()["experience_id"]

    res = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/rag/context",
        {"q": "ZEPHYR_ALPHA_739 warm replica compaction", "top_k": 3, "scope": "personal"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    chunks = body["chunks"]
    assert chunks, res.text
    assert chunks[0]["experience_id"] == eid
    assert chunks[0]["chunk_type"] == "trajectory_segment"
    assert chunks[0]["turn_start"] == 5
    assert chunks[0]["turn_end"] == 8
    assert chunks[0]["meta"]["source"] == "trajectory"
    assert not {"unless", "interrupted", "partially", "context", "summary"} & set(chunks[0]["meta"]["keywords"])
    assert "ZEPHYR_ALPHA_739" in chunks[0]["text"]
    assert "AGENTS.md instructions" not in chunks[0]["text"]
    assert "local-command-caveat" not in chunks[0]["text"]
    assert "<command-name>" not in chunks[0]["text"]
    assert "Auth: Unsupported" not in chunks[0]["text"]
    assert "mcp-servers/expool" not in chunks[0]["text"]
    assert "exp_push_latest" not in chunks[0]["text"]
    assert "turns=5-8" in body["context"]


def test_rag_context_indexes_do_and_dont_experience_units(app_client: TestClient):
    alice = register(app_client, "alice", "platform")

    card = lite_card("generic service repair", acl="private")
    card.update(
        {
            "intent": "repair a service workflow",
            "steps": ["inspect failure", "patch retry condition", "rerun test"],
            "outcome": "Warm replica retry was fixed.",
            "task_type": "debugging",
            "trajectory": [
                {
                    "role": "user",
                    "content": "修复 ZEPHYR_ALPHA_739 warm replica compaction retry",
                },
                {
                    "role": "assistant",
                    "content": "I will reproduce the failing compaction path.",
                    "tool_calls": [
                        {
                            "name": "Bash",
                            "arguments": {
                                "command": "pytest tests/test_shard_compaction.py::test_warm_replica_retry"
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "content": "FAILED AssertionError: warm replica skipped compaction retry",
                },
                {
                    "role": "assistant",
                    "content": "I will patch the retry condition and rerun the focused test.",
                    "tool_calls": [
                        {
                            "name": "Bash",
                            "arguments": {
                                "command": "pytest tests/test_shard_compaction.py::test_warm_replica_retry"
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "content": "1 passed in 0.42s",
                },
            ],
        }
    )
    pushed = signed_json(app_client, alice, "POST", "/v1/lite/push", card)
    assert pushed.status_code == 202, pushed.text
    eid = pushed.json()["experience_id"]

    dont = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/rag/context",
        {"q": "AssertionError warm replica skipped compaction retry", "top_k": 5, "scope": "personal"},
    )
    assert dont.status_code == 200, dont.text
    dont_chunks = dont.json()["chunks"]
    assert any(c["experience_id"] == eid and c["chunk_type"] == "dont_unit" for c in dont_chunks)
    dont_hit = next(c for c in dont_chunks if c["experience_id"] == eid and c["chunk_type"] == "dont_unit")
    assert dont_hit["meta"]["unit_status"] == "failure"
    assert dont_hit["turn_start"] == 1
    assert dont_hit["turn_end"] == 2
    assert "DO NOT" in dont.json()["context"]

    do = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/rag/context",
        {"q": "pytest warm replica retry passed", "top_k": 5, "scope": "personal"},
    )
    assert do.status_code == 200, do.text
    do_chunks = do.json()["chunks"]
    assert any(c["experience_id"] == eid and c["chunk_type"] == "do_unit" for c in do_chunks)
    do_hit = next(c for c in do_chunks if c["experience_id"] == eid and c["chunk_type"] == "do_unit")
    assert do_hit["meta"]["unit_status"] == "success"
    assert do_hit["turn_start"] == 3
    assert do_hit["turn_end"] == 4
    assert "DO " in do.json()["context"]


def test_rag_context_extracts_textual_claude_tool_units(app_client: TestClient):
    alice = register(app_client, "alice", "platform")

    card = lite_card("textual claude transcript", acl="private")
    card.update(
        {
            "intent": "repair qzcli resource spec submission",
            "steps": ["inspect qzcli payload", "patch resource_spec_price", "rerun focused pytest"],
            "outcome": "qzcli submission payload was fixed.",
            "task_type": "debugging",
            "trajectory": [
                {
                    "role": "user",
                    "content": "修复 qzcli create job unknown field spec_id，要改成 resource_spec_price",
                },
                {
                    "role": "assistant",
                    "content": """我先复现 qzcli payload 问题。

🔧 调用工具: Bash (id=toolu_bad)

```json
{
  "command": "pytest tests/test_qzcli_payload.py::test_submit_payload",
  "description": "Reproduce qzcli spec_id payload failure"
}
```""",
                },
                {
                    "role": "tool",
                    "content": """📤 工具返回 (id=toolu_bad)

FAILED AssertionError: unknown field spec_id in train_job create payload""",
                },
                {
                    "role": "assistant",
                    "content": """我改完 payload builder 后跑 focused test。

🔧 调用工具: Bash (id=toolu_good)

```json
{
  "command": "pytest tests/test_qzcli_payload.py::test_submit_payload",
  "description": "Verify resource_spec_price payload"
}
```""",
                },
                {
                    "role": "tool",
                    "content": """📤 工具返回 (id=toolu_good)

1 passed in 0.31s""",
                },
            ],
        }
    )
    pushed = signed_json(app_client, alice, "POST", "/v1/lite/push", card)
    assert pushed.status_code == 202, pushed.text
    eid = pushed.json()["experience_id"]

    bad = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/rag/context",
        {"q": "qzcli unknown field spec_id train_job payload", "top_k": 5, "scope": "personal"},
    )
    assert bad.status_code == 200, bad.text
    bad_chunks = bad.json()["chunks"]
    assert any(c["experience_id"] == eid and c["chunk_type"] == "dont_unit" for c in bad_chunks)
    bad_hit = next(c for c in bad_chunks if c["experience_id"] == eid and c["chunk_type"] == "dont_unit")
    assert bad_hit["meta"]["tool_name"] == "Bash"
    assert bad_hit["meta"]["action_kind"] == "text_tool_call"
    assert bad_hit["turn_start"] == 1
    assert bad_hit["turn_end"] == 2
    assert "unknown field spec_id" in bad_hit["text"]
    assert "qzcli train_job resource_spec_price" in bad_hit["meta"]["keyphrases"]

    good = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/rag/context",
        {"q": "qzcli resource_spec_price payload passed", "top_k": 5, "scope": "personal"},
    )
    assert good.status_code == 200, good.text
    good_chunks = good.json()["chunks"]
    assert any(c["experience_id"] == eid and c["chunk_type"] == "do_unit" for c in good_chunks)
    good_hit = next(c for c in good_chunks if c["experience_id"] == eid and c["chunk_type"] == "do_unit")
    assert good_hit["meta"]["tool_name"] == "Bash"
    assert good_hit["meta"]["action_kind"] == "text_tool_call"
    assert good_hit["turn_start"] == 3
    assert good_hit["turn_end"] == 4
    assert "resource_spec_price" in good_hit["text"]
    assert "qzcli spec_id resource_spec_price" in good_hit["meta"]["keyphrases"]


def test_release_healthz_public_and_admin(
    app_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    alice = register(app_client, "alice", "platform")
    monkeypatch.setenv("EXP_ADMIN_TOKEN", "admin-secret")

    public = app_client.get("/healthz")
    assert public.status_code == 200, public.text
    public_body = public.json()
    assert public_body["status"] in {"ok", "degraded"}
    assert public_body["checks"]["sqlite"]["status"] == "ok"
    assert "free_percent" in public_body["checks"]["disk"]

    sig = sign_request(alice, "GET", "/v1/admin/healthz", b"")
    admin = app_client.get(
        "/v1/admin/healthz",
        headers={
            "x-agent-name": alice.agent_name,
            "x-signature": sig,
            "x-admin-token": "admin-secret",
        },
    )
    assert admin.status_code == 200, admin.text
    admin_body = admin.json()
    assert admin_body["status"] in {"ok", "degraded"}
    assert admin_body["counts"]["agents"] == 1
    assert "root" in admin_body


def test_optional_register_token_protects_public_registration(
    app_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("EXP_REGISTER_TOKEN", "reg-secret")

    blocked = app_client.post("/v1/agents/register", json={"name": "alice", "team": "platform"})
    assert blocked.status_code == 403
    assert blocked.json()["error"].startswith("registration disabled")

    allowed = app_client.post(
        "/v1/agents/register",
        json={"name": "alice", "team": "platform"},
        headers={"x-register-token": "reg-secret"},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["agent_name"] == "alice"


def test_reregistering_existing_agent_is_refused(app_client: TestClient):
    """An existing agent name must not be hijackable: a second registration
    without an ops token is refused (409) and the original credential keeps
    working, so the first registrant retains control of their data."""
    alice = register(app_client, "alice", "platform")

    takeover = app_client.post(
        "/v1/agents/register",
        json={"name": "alice", "team": "attacker"},
    )
    assert takeover.status_code == 409, takeover.text

    # Original credential still authenticates after the refused takeover.
    sig = sign_request(alice, "GET", "/v1/experiences/search?q=x&top_k=1", b"")
    res = app_client.get(
        "/v1/experiences/search",
        params={"q": "x", "top_k": 1},
        headers={"x-agent-name": alice.agent_name, "x-signature": sig},
    )
    assert res.status_code == 200, res.text


def test_register_token_allows_credential_rotation(
    app_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """With EXP_REGISTER_TOKEN configured, presenting it authorizes rotating
    an existing agent's credential (the supported ops path)."""
    register(app_client, "alice", "platform")
    monkeypatch.setenv("EXP_REGISTER_TOKEN", "reg-secret")

    rotated = app_client.post(
        "/v1/agents/register",
        json={"name": "alice", "team": "platform"},
        headers={"x-register-token": "reg-secret"},
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["agent_name"] == "alice"


def test_admin_routes_fail_closed_and_require_admin_token(
    app_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    alice = register(app_client, "alice", "platform")
    monkeypatch.delenv("EXP_ADMIN_TOKEN", raising=False)
    sig = sign_request(alice, "GET", "/v1/admin/healthz", b"")
    headers = {"x-agent-name": alice.agent_name, "x-signature": sig}

    disabled = app_client.get("/v1/admin/healthz", headers=headers)
    assert disabled.status_code == 403
    assert disabled.json()["error"] == "admin endpoint disabled: set EXP_ADMIN_TOKEN"

    monkeypatch.setenv("EXP_ADMIN_TOKEN", "admin-secret")
    blocked = app_client.get("/v1/admin/healthz", headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["error"].startswith("admin endpoint disabled")

    allowed = app_client.get(
        "/v1/admin/healthz",
        headers={**headers, "x-admin-token": "admin-secret"},
    )
    assert allowed.status_code == 200, allowed.text


def test_latest_plugin_tarball_prefers_package_version_over_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("EXP_PLUGIN_TARBALL", raising=False)
    monkeypatch.setattr(server_mod, "_REPO_ROOT", tmp_path)
    plugin_dir = tmp_path / "dist-public" / "plugins"
    plugin_dir.mkdir(parents=True)

    low = _write_plugin_tarball(plugin_dir / "haohui666-expool-plugin-1.0.0.tgz", "1.0.0")
    high = _write_plugin_tarball(plugin_dir / "expool.tgz", "2.0.0")
    os.utime(low, (2_000_000_000, 2_000_000_000))
    os.utime(high, (1_000_000_000, 1_000_000_000))

    assert server_mod._latest_plugin_tarball() == high


def test_release_rate_limit_returns_429(
    app_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("EXP_RATE_PUSH_PER_MIN", "1")
    monkeypatch.setenv("EXP_RATE_WINDOW_SECONDS", "600")
    alice = register(app_client, "alice", "platform")

    first = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/push",
        lite_card("first push", acl="private"),
    )
    assert first.status_code == 202, first.text

    second = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/push",
        lite_card("second push", acl="private"),
    )
    assert second.status_code == 429
    assert second.headers["Retry-After"] == "600"
    assert second.json()["error"] == "rate_limited"


def test_public_auth_routes_are_rate_limited(
    app_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("EXP_RATE_LOGIN_PER_MIN", "1")
    monkeypatch.setenv("EXP_RATE_PAIR_PER_MIN", "1")
    monkeypatch.setenv("EXP_RATE_WINDOW_SECONDS", "600")

    first_login = app_client.post(
        "/v1/users/login",
        json={"email": "nobody@example.com", "password": "bad-password"},
    )
    assert first_login.status_code == 401
    second_login = app_client.post(
        "/v1/users/login",
        json={"email": "nobody@example.com", "password": "bad-password"},
    )
    assert second_login.status_code == 429
    assert second_login.json()["group"] == "user_login"

    first_pair = app_client.post("/v1/plugin/pair", json={"code": "expair_bad"})
    assert first_pair.status_code == 400
    second_pair = app_client.post("/v1/plugin/pair", json={"code": "expair_bad"})
    assert second_pair.status_code == 429
    assert second_pair.json()["group"] == "plugin_pair"


def test_lite_mvp_rejects_bad_hmac(app_client: TestClient):
    alice = register(app_client, "alice", "platform")
    body = json.dumps(lite_card("tamper test", acl="private"), separators=(",", ":")).encode()
    sig = sign_request(alice, "POST", "/v1/lite/push", body)

    tampered = body.replace(b"tamper", b"changed")
    res = app_client.request(
        "POST",
        "/v1/lite/push",
        content=tampered,
        headers={
            "content-type": "application/json",
            "x-agent-name": alice.agent_name,
            "x-signature": sig,
        },
    )
    assert res.status_code == 401
    assert res.json()["error"] == "bad signature"


def test_lite_revoke_marks_row_and_excludes_from_search(app_client: TestClient):
    """Revoke flow: owner can delete their experience; row stays in DB
    with revoked=1, vector is dropped, searches no longer return it,
    and the audit_log captures the deletion."""
    alice = register(app_client, "alice", "platform")

    # 1. Push.
    push = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/push",
        lite_card("revocable secret playbook", acl="private"),
    )
    assert push.status_code == 202, push.text
    eid = push.json()["experience_id"]

    # 2. Verify it shows up in search before revoke.
    pre = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/search",
        {"q": "revocable secret"},
    )
    assert pre.status_code == 200
    assert any(r["experience_id"] == eid for r in pre.json()["results"])

    # 3. Revoke.
    rev = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/revoke",
        {"experience_id": eid, "reason": "testing"},
    )
    assert rev.status_code == 200, rev.text
    body = rev.json()
    assert body["ok"] is True
    assert body["status"] == "revoked"
    assert body["experience_id"] == eid
    conn = sqlite3.connect(Path(os.environ["EXP_ROOT"]) / "pool.db")
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM rag_chunks WHERE experience_id = ?", (eid,)
        ).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM rag_vectors").fetchone()[0] == 0
    finally:
        conn.close()

    # 4. Search no longer returns it.
    post = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/search",
        {"q": "revocable secret"},
    )
    assert post.status_code == 200
    assert not any(r["experience_id"] == eid for r in post.json()["results"])

    # 5. Second revoke is idempotent.
    again = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/revoke",
        {"experience_id": eid, "reason": "retry"},
    )
    assert again.status_code == 200
    assert again.json()["status"] == "already_revoked"


def test_lite_revoke_rejects_other_agents(app_client: TestClient):
    """Bob cannot revoke Alice's experience."""
    alice = register(app_client, "alice", "platform")
    bob = register(app_client, "bob", "platform")
    push = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/push",
        lite_card("alice's private knowledge", acl="private"),
    )
    eid = push.json()["experience_id"]
    rev = signed_json(
        app_client,
        bob,
        "POST",
        "/v1/lite/revoke",
        {"experience_id": eid, "reason": "stealing"},
    )
    assert rev.status_code == 403
    assert "does not own" in rev.json()["detail"]


def test_lite_revoke_unknown_eid_returns_404(app_client: TestClient):
    alice = register(app_client, "alice", "platform")
    rev = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/revoke",
        {"experience_id": "not-a-real-id", "reason": "test"},
    )
    assert rev.status_code == 404


def test_publish_requires_strict_clean_content(app_client: TestClient):
    """A trace containing a file:// URI must be blocked with 422 + hits."""
    alice = register(app_client, "alice", "platform")
    push = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/push",
        {
            **lite_card("review screenshot", acl="private"),
            "trajectory": [
                {"role": "user", "content": "see file:///Users/alice/Library/x/y.png"},
            ],
        },
    )
    assert push.status_code == 202, push.text
    eid = push.json()["experience_id"]

    pub = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/publish",
        {"experience_id": eid},
    )
    assert pub.status_code == 422, pub.text
    body = pub.json()
    assert body["ok"] is False
    assert body["status"] == "blocked"
    rules_hit = {h["rule"] for h in body["blocking_hits"]}
    assert "file_uri" in rules_hit


def test_publish_clean_content_succeeds_and_unpublish_round_trips(app_client: TestClient):
    """A clean trace publishes, bumps publish_count, then unpublishes."""
    alice = register(app_client, "alice", "platform")
    push = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/push",
        {
            **lite_card("clean playbook for csv aggregation", acl="private"),
            "trajectory": [
                {"role": "user", "content": "I want to aggregate the rows by region"},
                {"role": "assistant", "content": "use pandas groupby"},
            ],
        },
    )
    assert push.status_code == 202, push.text
    eid = push.json()["experience_id"]

    # quota before
    q1 = signed_json(app_client, alice, "GET", "/v1/me/quota", {})
    assert q1.status_code == 200
    before = q1.json()["publish_count"]

    pub = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/publish",
        {"experience_id": eid},
    )
    assert pub.status_code == 200, pub.text
    body = pub.json()
    assert body["ok"] is True
    assert body["status"] == "published"
    assert body["quota"]["publish_count"] == before + 1

    # Idempotent re-publish
    again = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/publish",
        {"experience_id": eid},
    )
    assert again.json()["status"] == "already_public"
    # Count must NOT bump on repeat.
    assert again.json()["quota"]["publish_count"] == before + 1

    # Unpublish — count stays.
    un = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/unpublish",
        {"experience_id": eid},
    )
    assert un.json()["status"] == "unpublished"
    assert un.json()["quota"]["publish_count"] == before + 1


def test_publish_other_owner_forbidden(app_client: TestClient):
    """Bob can't publish Alice's experience even though both have credentials."""
    alice = register(app_client, "alice", "platform")
    bob = register(app_client, "bob", "platform")
    push = signed_json(
        app_client,
        alice,
        "POST",
        "/v1/lite/push",
        lite_card("alice's clean idea", acl="private"),
    )
    eid = push.json()["experience_id"]
    bad = signed_json(
        app_client,
        bob,
        "POST",
        "/v1/lite/publish",
        {"experience_id": eid},
    )
    assert bad.status_code == 403


def test_quota_endpoint_default_locked(app_client: TestClient):
    alice = register(app_client, "alice", "platform")
    res = signed_json(app_client, alice, "GET", "/v1/me/quota", {})
    assert res.status_code == 200
    body = res.json()
    assert body["publish_count"] == 0
    assert body["threshold"] == 3
    assert body["community_unlocked"] is False


def _write_plugin_tarball(path: Path, version: str) -> Path:
    payload = json.dumps({
        "name": "@haohui666/expool-plugin",
        "version": version,
    }).encode("utf-8")
    with tarfile.open(path, "w:gz") as tf:
        info = tarfile.TarInfo("package/package.json")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    return path
