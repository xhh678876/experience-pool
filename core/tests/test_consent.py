"""Tests for dist-public/exp_consent.py — the local consent module."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest


# --------------------------------------------------------------------------
# Module loading — exp_consent lives in dist-public/, not in the package.
# Each test gets an isolated INSTALL_DIR so consent.json doesn't bleed.
# --------------------------------------------------------------------------


def _load_consent_module(tmp_install_dir: Path):
    os.environ["EXP_INSTALL_DIR"] = str(tmp_install_dir)
    here = Path(__file__).resolve().parent.parent.parent
    src = here / "dist-public" / "exp_consent.py"
    spec = importlib.util.spec_from_file_location(
        f"exp_consent_{tmp_install_dir.name}", src
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[spec.name] = mod  # type: ignore[union-attr]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture
def consent_mod(tmp_path):
    return _load_consent_module(tmp_path / "exp_install")


# --------------------------------------------------------------------------


def test_default_consent_when_file_missing(consent_mod):
    data = consent_mod.load_consent()
    assert data["mode"] == "ask"
    assert data["agents"] == {}
    assert data["save_pending_on_skip"] is True


def test_save_and_load_roundtrip(consent_mod):
    data = consent_mod.default_consent()
    data["mode"] = "always"
    consent_mod.save_consent(data)
    again = consent_mod.load_consent()
    assert again["mode"] == "always"


def test_corrupt_consent_fallback(consent_mod, tmp_path):
    # Write garbage to consent.json
    consent_mod.CONSENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    consent_mod.CONSENT_PATH.write_text("not json {{{", encoding="utf-8")
    data = consent_mod.load_consent()
    # Should fall back to default rather than raise.
    assert data["mode"] == "ask"
    # The corrupt file should be backed up.
    assert consent_mod.CONSENT_PATH.with_suffix(".json.corrupt").exists()


def test_invalid_mode_rejected_on_save(consent_mod):
    data = consent_mod.default_consent()
    data["mode"] = "totally_made_up"
    with pytest.raises(ValueError):
        consent_mod.save_consent(data)


# --------------------------------------------------------------------------
# decide() priority cascade
# --------------------------------------------------------------------------


def test_decide_default_when_empty(consent_mod):
    res = consent_mod.decide("claude-code", str(Path.home()))
    assert res.mode == "ask"
    assert res.reason in ("default", "global")


def test_decide_global_mode(consent_mod):
    consent_mod.set_global("always")
    res = consent_mod.decide("claude-code", str(Path.home()))
    assert res.mode == "always"


def test_decide_agent_overrides_global(consent_mod):
    consent_mod.set_global("always")
    consent_mod.set_agent("cursor", "never")
    res = consent_mod.decide("cursor", "/tmp")
    assert res.mode == "never"
    assert res.reason == "agent:cursor"
    res2 = consent_mod.decide("claude-code", "/tmp")
    assert res2.mode == "always"


def test_decide_cwd_overrides_agent(consent_mod):
    consent_mod.set_agent("claude-code", "always")
    consent_mod.set_cwd("/tmp/secret/**", "never", reason="customer code")
    res = consent_mod.decide("claude-code", "/tmp/secret/abc/def")
    assert res.mode == "never"
    assert res.reason.startswith("cwd_rule:")
    # Outside the glob → falls back to agent rule.
    res2 = consent_mod.decide("claude-code", "/tmp/safe")
    assert res2.mode == "always"


def test_decide_session_override_wins(consent_mod):
    consent_mod.set_global("always")
    consent_mod.record_session_override("sess123", "never")
    res = consent_mod.decide("claude-code", "/tmp", session_id="sess123")
    assert res.mode == "never"
    assert res.reason == "session_override"


def test_never_is_hard_stop_even_at_lowest_level(consent_mod):
    """A `never` at any layer beats a more permissive layer above it."""
    consent_mod.set_global("never")
    consent_mod.set_agent("claude-code", "always")  # tries to override
    res = consent_mod.decide("claude-code", "/tmp")
    assert res.mode == "never"  # global hard-stop wins


def test_glob_double_star_matches_any_depth(consent_mod):
    consent_mod.set_cwd("~/work/**", "never")
    home = str(Path.home())
    res = consent_mod.decide("claude-code", f"{home}/work/myproject/src/page.tsx")
    assert res.mode == "never"
    res2 = consent_mod.decide("claude-code", f"{home}/play")
    assert res2.mode != "never"


def test_session_override_expires(consent_mod):
    """Expired session overrides should not influence decide()."""
    consent_mod.record_session_override("oldsess", "never", ttl_seconds=-1)
    res = consent_mod.decide("claude-code", "/tmp", session_id="oldsess")
    assert res.mode != "never"
    # And the expired entry should be pruned out of consent.json.
    data = consent_mod.load_consent()
    assert "oldsess" not in data.get("session_overrides", {})


def test_set_cwd_idempotent(consent_mod):
    consent_mod.set_cwd("/tmp/foo", "never")
    consent_mod.set_cwd("/tmp/foo", "always", reason="changed mind")
    data = consent_mod.load_consent()
    rules = data["cwd_rules"]
    assert len(rules) == 1
    assert rules[0]["mode"] == "always"
    assert rules[0]["reason"] == "changed mind"


# --------------------------------------------------------------------------
# Pending queue
# --------------------------------------------------------------------------


def test_save_pending_writes_file(consent_mod):
    p = consent_mod.save_pending({"trajectory": [{"role": "user", "content": "hi"}]},
                                 session_id="abc")
    assert p.exists()
    data = json.loads(p.read_text())
    assert data["trajectory"][0]["content"] == "hi"


def test_pending_cap_drops_oldest(consent_mod, monkeypatch):
    monkeypatch.setattr(consent_mod, "PENDING_MAX_ENTRIES", 3)
    for i in range(5):
        consent_mod.save_pending({"i": i}, session_id=f"s{i}")
        time.sleep(0.01)  # ensure mtime ordering
    files = list(consent_mod.PENDING_DIR.glob("*.json"))
    assert len(files) == 3
    # Newest 3 should remain (i=2,3,4).
    contents = sorted(int(json.loads(f.read_text())["i"]) for f in files)
    assert contents == [2, 3, 4]


def test_pending_ttl_drops_old(consent_mod):
    p = consent_mod.save_pending({"x": 1}, session_id="ttl")
    # Backdate it 8 days.
    old = time.time() - 8 * 86400
    os.utime(p, (old, old))
    consent_mod.prune_pending()
    assert not p.exists()


def test_list_pending_returns_metadata(consent_mod):
    consent_mod.save_pending({"one": 1}, session_id="a")
    consent_mod.save_pending({"two": 2}, session_id="b")
    listed = consent_mod.list_pending()
    assert len(listed) == 2
    assert all("size_bytes" in entry for entry in listed)
    assert all("mtime" in entry for entry in listed)


# --------------------------------------------------------------------------
# Prompt UI — non-interactive escape hatch
# --------------------------------------------------------------------------


def test_prompt_returns_no_when_noninteractive(consent_mod, monkeypatch):
    monkeypatch.setenv("EXP_NONINTERACTIVE", "1")
    answer = consent_mod.prompt("claude-code", "/tmp", "sid", timeout_seconds=1)
    assert answer == "no"


# --------------------------------------------------------------------------
# Audit log
# --------------------------------------------------------------------------


def test_audit_log_appended(consent_mod):
    consent_mod.set_global("never")
    consent_mod.set_agent("cursor", "always")
    log = consent_mod.AUDIT_LOG.read_text()
    assert "consent_set_global" in log
    assert "consent_set_agent" in log
    # Each entry must be valid JSON one-per-line.
    for line in log.strip().splitlines():
        json.loads(line)


# --------------------------------------------------------------------------
# explain() — the dry simulator used by `exp consent show --simulate`
# --------------------------------------------------------------------------


def test_explain_returns_structured_decision(consent_mod):
    consent_mod.set_cwd("/secret/**", "never", reason="legal")
    out = consent_mod.explain("claude-code", "/secret/foo")
    assert out["decision"]["mode"] == "never"
    assert "/secret" in out["decision"]["reason"]
    assert out["decision"]["rule"]["reason"] == "legal"
