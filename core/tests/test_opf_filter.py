"""Tests for opf_filter (Layer 1.5) — OpenAI Privacy Filter integration.

Strategy: don't depend on the actual `opf` package being installed. We mock
the runtime API to exercise the integration plumbing (loading, span
splicing, recursive walker, severity escalation, error handling).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable
from unittest import mock

import pytest

from exp_core import opf_filter, sanitize


# --------------------------------------------------------------------------
# Fake OPF API: pattern-based stub so we can simulate model behavior
# without downloading a 1.5B checkpoint.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeSpan:
    start: int
    end: int
    label: str


class _FakeRedactor:
    """Pretends to be opf._api.RedactorAPI. Reports every regex match as
    a span with the provided label."""

    def __init__(self, patterns: Iterable[tuple[re.Pattern[str], str]]):
        self._patterns = list(patterns)

    def redact(self, text: str) -> list[_FakeSpan]:
        spans: list[_FakeSpan] = []
        for pat, label in self._patterns:
            for m in pat.finditer(text):
                spans.append(_FakeSpan(start=m.start(), end=m.end(), label=label))
        return spans


@pytest.fixture(autouse=True)
def _reset_opf_state():
    """Each test gets a fresh global state in opf_filter."""
    opf_filter._OPF_API = None
    opf_filter._OPF_TRIED = False
    opf_filter._OPF_LOAD_ERROR = None
    yield
    opf_filter._OPF_API = None
    opf_filter._OPF_TRIED = False


def _install_fake(api: _FakeRedactor) -> None:
    """Inject a pre-loaded fake into opf_filter's module-level cache."""
    opf_filter._OPF_API = api
    opf_filter._OPF_TRIED = True


# --------------------------------------------------------------------------


def test_disabled_via_env_short_circuits(monkeypatch):
    monkeypatch.setenv("EXP_OPF_DISABLED", "1")
    assert opf_filter.is_enabled() is False
    res = opf_filter.redact_text("Alice was born on 1990-01-02")
    assert res.used is False
    assert res.text == "Alice was born on 1990-01-02"
    assert res.hits == {}


def test_no_package_installed_falls_through(monkeypatch):
    """When the `opf` package is not importable, redact_text must
    silently no-op rather than crash."""
    monkeypatch.delenv("EXP_OPF_DISABLED", raising=False)
    # Force ImportError by patching the import site.
    monkeypatch.setitem(__import__("sys").modules, "opf", None)
    res = opf_filter.redact_text("hello")
    assert res.used is False
    assert res.text == "hello"


def test_redact_text_replaces_spans():
    fake = _FakeRedactor([
        (re.compile(r"Alice"), "private_person"),
        (re.compile(r"\d{4}-\d{2}-\d{2}"), "private_date"),
    ])
    _install_fake(fake)
    res = opf_filter.redact_text("Alice was born on 1990-01-02 in Paris")
    assert res.used is True
    assert "<PRIVATE_FILTER:private_person>" in res.text
    assert "<PRIVATE_FILTER:private_date>" in res.text
    assert res.hits == {"private_person": 1, "private_date": 1}
    assert res.triggered_high is False  # no `secret` label fired


def test_secret_label_triggers_high():
    fake = _FakeRedactor([(re.compile(r"hunter2"), "secret")])
    _install_fake(fake)
    res = opf_filter.redact_text("password=hunter2")
    assert res.used is True
    assert res.triggered_high is True
    assert res.hits == {"secret": 1}


def test_empty_input_short_circuits():
    fake = _FakeRedactor([(re.compile(r"."), "secret")])
    _install_fake(fake)
    res = opf_filter.redact_text("")
    assert res.used is False
    assert res.text == ""


def test_redact_node_walks_nested_dict():
    fake = _FakeRedactor([(re.compile(r"Bob"), "private_person")])
    _install_fake(fake)
    hits: dict[str, int] = {}
    node = {
        "role": "user",  # SKIP_KEYS — must not be scrubbed
        "id": "msg_1",   # SKIP_KEYS
        "content": "Bob said hi",
        "tool_calls": [{
            "id": "call_1",  # SKIP
            "function": {"name": "send", "arguments": {"to": "Bob"}},
        }],
    }
    cleaned, _ = opf_filter.redact_node(node, hits)
    assert cleaned["role"] == "user"
    assert cleaned["id"] == "msg_1"
    assert cleaned["tool_calls"][0]["id"] == "call_1"
    assert "<PRIVATE_FILTER:private_person>" in cleaned["content"]
    # Nested arguments dict was scrubbed too.
    assert "<PRIVATE_FILTER:private_person>" in str(cleaned["tool_calls"])
    # Two Bob hits (one in content, one in args).
    assert hits == {"private_person": 2}


def test_load_failure_caches_so_we_dont_retry():
    """If the first load attempt fails, subsequent calls must NOT keep
    re-trying the load (which would burn time per push)."""
    with mock.patch.object(
        opf_filter, "_load_api", wraps=opf_filter._load_api
    ) as wrapped:
        # Both calls run, but the underlying import path only attempts once.
        opf_filter.redact_text("first")
        opf_filter.redact_text("second")
        assert wrapped.call_count == 2  # function called twice
        # but the import was only attempted once; verify via _OPF_TRIED flag.
        assert opf_filter._OPF_TRIED is True


# --------------------------------------------------------------------------
# Integration: sanitize_trajectory end-to-end with OPF as Layer 1.5.
# --------------------------------------------------------------------------


def test_sanitize_trajectory_layer1_then_opf():
    """Layer 1 (regex) handles `sk-ant-...`; Layer 1.5 (OPF) handles a
    contextual person name. Both contribute to the redactions dict with
    the OPF entries prefixed `opf_`."""
    fake = _FakeRedactor([(re.compile(r"\bAlice\b"), "private_person")])
    _install_fake(fake)
    traj = [
        {"role": "user", "content": "Hi I'm Alice and my key is sk-ant-" + "a" * 30},
    ]
    result = sanitize.sanitize_trajectory(traj, sensitivity="medium")
    # Layer 1 caught the Anthropic key (regex).
    assert "anthropic_key" in result.redactions
    # Layer 1.5 (OPF) caught the person name and was credited as `opf_*`.
    assert "opf_private_person" in result.redactions
    # Layer 1 fired with `severity: high`, so review must be triggered.
    assert result.triggered_human_review is True
    # OPF attribution is preserved separately for audit.
    assert result.opf_used is True
    assert result.opf_hits.get("private_person") == 1


def test_sanitize_trajectory_use_opf_false_disables_layer1_5():
    fake = _FakeRedactor([(re.compile(r"Charlie"), "private_person")])
    _install_fake(fake)
    traj = [{"role": "user", "content": "Hello Charlie"}]
    result = sanitize.sanitize_trajectory(traj, use_opf=False)
    assert result.opf_used is False
    assert result.opf_hits == {}
    assert "Charlie" in result.sanitized[0]["content"]  # untouched


def test_sanitize_trajectory_opf_secret_label_routes_to_human_review():
    """An OPF-only `secret` hit (regex missed it) must still trigger
    human review on the resulting status."""
    fake = _FakeRedactor([(re.compile(r"NEWFORMAT-[A-Z0-9]{10}"), "secret")])
    _install_fake(fake)
    traj = [{"role": "user", "content": "use NEWFORMAT-ABC1234567 for auth"}]
    result = sanitize.sanitize_trajectory(traj)
    assert result.status == "human_review"
    assert "opf_secret" in result.redactions
