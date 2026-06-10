"""Tests for the three-layer sanitizer.

Covers:
  * Layer 1 unit tests for individual rules.
  * pool.push() integration:
    - API key in trajectory -> Layer 1 redacts, status=human_review.
    - Email + phone -> Layer 1 redacts, status=flagged.
    - Clean trajectory -> status=done.
  * Raw trajectory is preserved at <id>.raw.json when changes happen.

Run with EXP_LLM=mock so Layer 3 doesn't make real API calls.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ["EXP_LLM"] = "mock"

import pytest  # noqa: E402

from exp_core.pool import ExperiencePool, PoolConfig  # noqa: E402
from exp_core.sanitize import (  # noqa: E402
    layer1_text,
    layer2_trajectory,
    load_rules,
    sanitize_trajectory,
)


# ---------- Layer 1 unit tests ----------


@pytest.fixture(scope="module")
def rules():
    return load_rules()


def test_layer1_redacts_email(rules):
    text = "ping me at alice@example.com please"
    out, counts, hi = layer1_text(text, rules)
    assert "<EMAIL>" in out
    assert "alice@example.com" not in out
    assert counts.get("email") == 1
    assert hi is False  # email is medium severity, not high


def test_layer1_redacts_phone(rules):
    text = "call +1 415 555 0142 today"
    out, counts, _ = layer1_text(text, rules)
    assert "<PHONE>" in out
    assert "415" not in out
    assert counts.get("phone_intl", 0) >= 1


def test_layer1_redacts_credit_card_via_luhn(rules):
    # 4111 1111 1111 1111 is a known Luhn-valid test card.
    text = "use card 4111 1111 1111 1111 for the demo"
    out, counts, hi = layer1_text(text, rules)
    assert "<CARD>" in out
    assert counts.get("credit_card") == 1
    assert hi is True


def test_layer1_does_not_redact_non_luhn(rules):
    # Sequence of 16 digits that fails Luhn.
    text = "tracking number 1234 5678 9012 3456 ok"
    out, counts, _ = layer1_text(text, rules)
    assert "<CARD>" not in out
    assert "credit_card" not in counts


def test_layer1_redacts_aws_key(rules):
    text = "key=AKIAIOSFODNN7EXAMPLE in config"
    out, counts, hi = layer1_text(text, rules)
    assert "<KEY>" in out
    assert counts.get("aws_access_key", 0) >= 1
    assert hi is True


def test_layer1_redacts_ipv4(rules):
    text = "src 192.168.1.42 connected"
    out, counts, _ = layer1_text(text, rules)
    assert "<IP>" in out
    assert counts.get("ipv4") == 1


def test_layer1_redacts_internal_domain(rules):
    text = "VPN at corp.example.com is up"
    out, counts, _ = layer1_text(text, rules)
    assert "<INTERNAL_HOST>" in out
    assert counts.get("internal_host", 0) >= 1


def test_layer1_redacts_employee_id(rules):
    text = "contact EMP12345 for access"
    out, counts, _ = layer1_text(text, rules)
    assert "<EMP_ID>" in out
    assert counts.get("employee_id", 0) >= 1


def test_layer1_redacts_url_credentials(rules):
    text = "fetch https://admin:hunter2@example.com/path"
    out, counts, hi = layer1_text(text, rules)
    assert "hunter2" not in out
    assert "<USER>" in out and "<PASS>" in out
    assert hi is True


def test_layer1_redacts_anthropic_key(rules):
    text = "auth sk-ant-Test1234567890ABCDEFghijKL token"
    out, counts, hi = layer1_text(text, rules)
    assert "<SECRET>" in out
    assert "sk-ant-Test" not in out
    assert hi is True


def test_layer1_redacts_github_token(rules):
    text = "token ghp_AbCdEf1234567890zyXwVuTsRq committed"
    out, counts, hi = layer1_text(text, rules)
    assert "<SECRET>" in out
    assert "ghp_" not in out
    assert hi is True


# ---------- Layer 2 unit tests ----------


def test_layer2_flags_address():
    traj = [{"role": "user", "content": "Please ship to 123 Main Street and confirm."}]
    findings = layer2_trajectory(traj)
    assert any(f.category == "address" for f in findings)


def test_layer2_flags_dob():
    traj = [{"role": "user", "content": "DOB on file: 1985-03-22 confirmed"}]
    findings = layer2_trajectory(traj)
    assert any(f.category == "dob" for f in findings)


def test_layer2_flags_name_mention():
    traj = [{"role": "user", "content": "I met Bob Smith at the conference"}]
    findings = layer2_trajectory(traj)
    assert any(f.category == "name" for f in findings)


# ---------- Orchestrator gating ----------


def test_sanitize_clean_low_sensitivity_skips_layer2_3():
    traj = [
        {"role": "user", "content": "summarize this paragraph please"},
        {"role": "assistant", "content": "here is the summary"},
    ]
    result = sanitize_trajectory(traj, sensitivity="low")
    assert result.status == "done"
    assert result.changed is False
    assert result.layer2_findings == ()
    assert result.layer3 is None


def test_sanitize_email_phone_marks_flagged():
    traj = [
        {"role": "user", "content": "email me at bob@test.com or +1 415 555 0142"},
    ]
    result = sanitize_trajectory(traj, sensitivity="medium")
    assert result.status == "flagged"
    assert result.triggered_human_review is False


def test_sanitize_aws_key_marks_human_review():
    traj = [
        {"role": "user", "content": "use AKIAIOSFODNN7EXAMPLE for deploy"},
    ]
    result = sanitize_trajectory(traj, sensitivity="medium")
    assert result.status == "human_review"
    assert result.triggered_human_review is True


# ---------- pool.push() integration ----------


def make_pool(tmp: Path) -> ExperiencePool:
    return ExperiencePool(PoolConfig(root=tmp))


def test_push_with_api_key_routes_to_human_review(tmp_path):
    pool = make_pool(tmp_path)
    pool.register_agent("agent-a", "platform")
    traj = [
        {"role": "user", "content": "ship with key AKIAIOSFODNN7EXAMPLE today"},
        {"role": "assistant", "content": "deployed using the supplied key"},
    ]
    row = pool.push("agent-a", "csv_analysis", "claude-stub", traj, sensitivity="medium")
    assert row["sanitization_status"] == "human_review"
    assert row["review_status"] == "pending"

    # The on-disk trajectory must be sanitized.
    on_disk = json.loads(Path(row["trajectory_path"]).read_text())
    flat = json.dumps(on_disk)
    assert "AKIAIOSFODNN7EXAMPLE" not in flat
    assert "<KEY>" in flat

    # The raw original must be preserved alongside.
    raw_path = Path(row["trajectory_path"]).with_suffix(".raw.json")
    assert raw_path.exists()
    raw = raw_path.read_text()
    assert "AKIAIOSFODNN7EXAMPLE" in raw
    pool.close()


def test_push_with_email_phone_marks_flagged(tmp_path):
    pool = make_pool(tmp_path)
    pool.register_agent("agent-a", "platform")
    traj = [
        {"role": "user", "content": "ping me at alice@example.com or +1 415 555 0142"},
        {"role": "assistant", "content": "got it, will reach out"},
    ]
    row = pool.push("agent-a", "csv_analysis", "claude-stub", traj, sensitivity="medium")
    assert row["sanitization_status"] == "flagged"
    # Mock judge gives stable variance, so review_status should remain auto_approved.
    assert row["review_status"] in {"auto_approved", "pending"}

    on_disk = json.loads(Path(row["trajectory_path"]).read_text())
    flat = json.dumps(on_disk)
    assert "alice@example.com" not in flat
    assert "<EMAIL>" in flat
    assert "<PHONE>" in flat
    pool.close()


def test_push_clean_trajectory_status_done(tmp_path):
    pool = make_pool(tmp_path)
    pool.register_agent("agent-a", "platform")
    traj = [
        {"role": "user", "content": "summarize csv data"},
        {"role": "assistant", "content": "loaded csv, ran describe()"},
    ]
    row = pool.push("agent-a", "csv_analysis", "claude-stub", traj, sensitivity="low")
    assert row["sanitization_status"] == "done"
    # No raw side-file should exist for a clean trajectory.
    raw_path = Path(row["trajectory_path"]).with_suffix(".raw.json")
    assert not raw_path.exists()
    pool.close()


def test_push_high_sensitivity_runs_layer3(tmp_path):
    """Sensitivity=high triggers Layer 3 even when Layer 1/2 are quiet.
    With EXP_LLM=mock, Layer 3 returns is_sensitive=False, so the final
    status stays 'done' (or 'flagged' if any heuristic Layer 2 fired)."""
    pool = make_pool(tmp_path)
    pool.register_agent("agent-a", "platform")
    traj = [
        {"role": "user", "content": "review the unreleased product roadmap"},
        {"role": "assistant", "content": "reviewing now"},
    ]
    row = pool.push("agent-a", "csv_analysis", "claude-stub", traj, sensitivity="high")
    assert row["sanitization_status"] in {"done", "flagged"}
    pool.close()


def test_push_full_fixture(tmp_path):
    """The packaged sensitive trajectory hits multiple high-severity rules."""
    fixture = (
        Path(__file__).parent / "fixtures" / "sensitive_traj.json"
    ).read_text()
    traj = json.loads(fixture)["trajectory"]

    pool = make_pool(tmp_path)
    pool.register_agent("agent-a", "platform")
    row = pool.push("agent-a", "csv_analysis", "claude-stub", traj, sensitivity="high")
    assert row["sanitization_status"] == "human_review"
    assert row["review_status"] == "pending"

    on_disk = json.loads(Path(row["trajectory_path"]).read_text())
    flat = json.dumps(on_disk)
    # No leak of any of the secrets/keys.
    assert "AKIAIOSFODNN7EXAMPLE" not in flat
    assert "ghp_AbCdEf1234567890zyXwVuTsRq" not in flat
    assert "sk-ant-Test1234567890ABCDEFghijKL" not in flat
    assert "4111 1111 1111 1111" not in flat
    assert "hunter2" not in flat
    assert "john.doe@example.com" not in flat
    assert "EMP12345" not in flat
    pool.close()
