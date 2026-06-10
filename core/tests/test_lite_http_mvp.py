"""HTTP-level smoke tests for the lite MVP path.

This exercises the actual FastAPI app, HMAC request signing, local-lite card
upload, SQL persistence, vector search, and private/team/public ACL filtering.
It intentionally avoids the judge, credit assignment, and skills path.
"""

from __future__ import annotations

import io
import json
import os
import tarfile
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

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

    low = _write_plugin_tarball(plugin_dir / "chuangzhi-expool-plugin-1.0.0.tgz", "1.0.0")
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
        "name": "@chuangzhi/expool-plugin",
        "version": version,
    }).encode("utf-8")
    with tarfile.open(path, "w:gz") as tf:
        info = tarfile.TarInfo("package/package.json")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    return path
