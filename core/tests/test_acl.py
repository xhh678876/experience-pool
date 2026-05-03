from __future__ import annotations

import os
from pathlib import Path

os.environ["EXP_LLM"] = "mock"

from exp_core.acl_search import get_with_acl, search_with_acl  # noqa: E402
from exp_core.identity import (  # noqa: E402
    can_read,
    issue_credential,
    load_credential,
    parse_acl,
    sign_request,
    verify_signature,
)
from exp_core.pool import ExperiencePool, PoolConfig  # noqa: E402


def trajectory(extra: str = "") -> list[dict]:
    return [
        {"role": "user", "content": f"task {extra}"},
        {"role": "assistant", "content": "did the task"},
    ]


def test_parse_acl():
    assert parse_acl("private") == ("private", None)
    assert parse_acl("public") == ("public", None)
    assert parse_acl("org") == ("public", None)  # legacy alias
    assert parse_acl("team:platform") == ("team", "platform")
    assert parse_acl("garbage") == ("private", None)  # fail closed


def test_can_read_matrix():
    # private: only the owner.
    assert can_read("a1", "platform", "a1", "private")
    assert not can_read("a2", "platform", "a1", "private")

    # team: same team can read.
    assert can_read("a2", "platform", "a1", "team:platform")
    assert not can_read("a2", "data", "a1", "team:platform")

    # org: anyone.
    assert can_read("a2", "data", "a1", "org")


def test_signature_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("EXP_CREDENTIALS_DIR", str(tmp_path / "creds"))
    # Have to re-import after setting env so module-level dir is fresh.
    from exp_core import identity  # noqa: PLC0415
    import importlib  # noqa: PLC0415
    importlib.reload(identity)

    cred = identity.issue_credential("agent_1", "alice", "platform")
    body = b'{"hello":"world"}'
    sig = identity.sign_request(cred, "POST", "/v1/experiences", body)
    assert identity.verify_signature(cred.secret, "POST", "/v1/experiences", body, sig)
    # Tampered body fails.
    assert not identity.verify_signature(cred.secret, "POST", "/v1/experiences", b'{"hello":"x"}', sig)
    # Loadable from disk.
    loaded = identity.load_credential("alice")
    assert loaded is not None
    assert loaded.secret == cred.secret


def test_search_respects_acl(tmp_path):
    pool = ExperiencePool(PoolConfig(root=tmp_path))
    pool.register_agent("alice", "platform")
    pool.register_agent("bob", "data")
    pool.register_agent("carol", "platform")

    # Use distinct task_types so dedup (which is task-type-scoped) doesn't fold
    # the three rows into one. The mock LLM emits the same intent for everything,
    # so we'd otherwise merge.
    e_priv = pool.push("alice", "task_priv", "stub", trajectory("priv"), acl="private")
    e_team = pool.push("alice", "task_team", "stub", trajectory("team"), acl="team:platform")
    e_org = pool.push("alice", "task_org", "stub", trajectory("org"), acl="org")

    # Bob (different team) sees only the org one.
    bob_hits = search_with_acl(pool, "bob", "task")
    bob_ids = {h["experience_id"] for h in bob_hits}
    assert e_org["experience_id"] in bob_ids
    assert e_team["experience_id"] not in bob_ids
    assert e_priv["experience_id"] not in bob_ids

    # Carol (same team as alice) sees team + org but not private.
    carol_hits = search_with_acl(pool, "carol", "task")
    carol_ids = {h["experience_id"] for h in carol_hits}
    assert e_org["experience_id"] in carol_ids
    assert e_team["experience_id"] in carol_ids
    assert e_priv["experience_id"] not in carol_ids

    # Alice (the owner) sees all three.
    alice_hits = search_with_acl(pool, "alice", "task")
    alice_ids = {h["experience_id"] for h in alice_hits}
    assert {e_org["experience_id"], e_team["experience_id"], e_priv["experience_id"]} <= alice_ids

    # get_with_acl logs denied reads.
    blocked = get_with_acl(pool, "bob", e_priv["experience_id"])
    assert blocked is None
    cur = pool.conn.execute(
        "SELECT action FROM audit_log WHERE actor = 'bob' AND action = 'read_denied'"
    )
    assert cur.fetchone() is not None
    pool.close()
