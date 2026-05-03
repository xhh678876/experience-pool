"""Tests for sanitize_public.strict_public_check.

These are publication-gate rules — every hit blocks publish-to-community.
The motivating case is the user's report: a Lark resource URI like
    file:///Users/xiehaohui/Library/Application Support/LarkShell/...
must never make it to the public pool, even though the regular sanitizer
considers it benign.
"""

from __future__ import annotations

import pytest

from exp_core.sanitize_public import (
    StrictHit,
    StrictPublicResult,
    list_rules,
    strict_public_check,
)


# --------------------------------------------------------------------------
# Per-rule unit tests
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected_rule", [
    # The user's actual example — Lark resource URI.
    ("file:///Users/xiehaohui/Library/Application%20Support/LarkShell/sdk_storage/82b78290f09634ed2abf927b07c330f5/resources/images/img.jpg",
     "file_uri"),
    # Plain file URI without percent encoding.
    ("file:///etc/hosts is at file:///etc/hosts", "file_uri"),
    # IM-app local resource (without file://)
    ("LarkShell/sdk_storage/abcdef/resources/img.png", "local_app_resource"),
    ("DingTalkShell/sdk_storage/x/y", "local_app_resource"),
    # IDE resource URIs.
    ("vscode-resource://file///Users/foo/x.ts", "vscode_resource"),
    ("vscode-webview://12345/index.html", "vscode_resource"),
    # Browser extensions.
    ("chrome-extension://abcdefg/popup.html", "browser_extension_url"),
    ("moz-extension://1234/options", "browser_extension_url"),
    # Localhost URLs.
    ("http://localhost:8080/api", "localhost_url"),
    ("https://127.0.0.1:5000", "localhost_url"),
    ("http://[::1]:3000/x", "localhost_url"),
    # Private IP ranges.
    ("http://192.168.1.10/admin", "private_ip_url"),
    ("https://10.0.0.5:8443/v1/things", "private_ip_url"),
    ("http://172.16.5.1/", "private_ip_url"),
    # Absolute system paths.
    ("see /etc/passwd for", "absolute_system_path"),
    ("write to /var/log/app.log", "absolute_system_path"),
    ("open /Library/Preferences/com.example.plist", "absolute_system_path"),
    # Windows paths.
    ("Windows path: C:\\Users\\Alice\\Desktop\\thing.txt", "windows_path"),
    # Session UUIDs.
    ("session 80e7bce1-817f-4dcd-ad90-9fa2796dd4d3 done", "session_uuid"),
])
def test_rule_fires(text: str, expected_rule: str):
    res = strict_public_check(trajectory=[{"role": "user", "content": text}])
    assert res.ok is False
    assert any(h.rule == expected_rule for h in res.hits), (
        f"expected {expected_rule!r} in {[h.rule for h in res.hits]}"
    )


# --------------------------------------------------------------------------
# Negative cases — content that should NOT block publish.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    # Generic public URLs.
    "see https://docs.python.org/3/library/re.html for syntax",
    "https://github.com/openai/privacy-filter is the upstream",
    # Arbitrary text.
    "I refactored the loop in the parser to handle empty input",
    # Public IPs (8.8.8.8 is a public Google DNS).
    "the upstream DNS is 8.8.8.8",
    # Relative paths and code-style references.
    "edit src/server/sanitize.py around line 60",
    "the function `layer1_text` lives in core/exp_core/sanitize.py",
    # Empty / whitespace.
    "",
    "   \n\t  ",
])
def test_clean_content_passes(text: str):
    res = strict_public_check(trajectory=[{"role": "user", "content": text}])
    assert res.ok is True, f"unexpected hits: {[h.rule for h in res.hits]}"
    assert res.hits == []


# --------------------------------------------------------------------------
# Walker behaviour — nested structures
# --------------------------------------------------------------------------


def test_walker_finds_hit_in_nested_tool_call():
    traj = [
        {
            "role": "assistant",
            "content": "calling tool",
            "tool_calls": [{
                "id": "call_1",         # SKIP_KEYS — must not be scanned
                "type": "function",     # SKIP_KEYS
                "function": {
                    "name": "preview",
                    "arguments": {
                        "url": "file:///Users/alice/Downloads/secret.pdf",
                    },
                },
            }],
        },
    ]
    res = strict_public_check(trajectory=traj)
    assert res.ok is False
    assert len(res.hits) == 1
    assert res.hits[0].rule == "file_uri"
    assert "tool_calls" in res.hits[0].location
    assert "arguments" in res.hits[0].location


def test_walker_skips_structural_keys():
    """A session UUID legitimately appearing as a tool_use_id should NOT
    block publish, but the same UUID inside content SHOULD."""
    safe_traj = [{
        "role": "assistant",
        "content": "ok",
        "tool_calls": [{
            "id": "80e7bce1-817f-4dcd-ad90-9fa2796dd4d3",  # SKIP_KEYS
            "tool_use_id": "80e7bce1-817f-4dcd-ad90-9fa2796dd4d3",  # SKIP
        }],
    }]
    assert strict_public_check(trajectory=safe_traj).ok is True

    leaky_traj = [{
        "role": "user",
        "content": "see session 80e7bce1-817f-4dcd-ad90-9fa2796dd4d3",
    }]
    assert strict_public_check(trajectory=leaky_traj).ok is False


def test_summary_aggregates_per_rule():
    traj = [
        {"role": "user", "content": "file:///a/b file:///c/d"},
        {"role": "assistant", "content": "see /etc/x and /var/y"},
    ]
    res = strict_public_check(trajectory=traj)
    assert res.ok is False
    assert res.summary["file_uri"] == 2
    assert res.summary["absolute_system_path"] == 2


def test_card_fields_scanned():
    card = {
        "query": "open this file:///Users/alice/code/app.py",
        "intent": "review code",
        "outcome": "saw it",
        "steps": ["read file", "noticed leak in /var/log/x"],
    }
    res = strict_public_check(card=card)
    assert res.ok is False
    rules = {h.rule for h in res.hits}
    assert "file_uri" in rules
    assert "absolute_system_path" in rules


def test_reject_payload_caps_hits_at_50():
    huge_traj = [{
        "role": "user",
        "content": " ".join(f"file:///x/{i}" for i in range(200)),
    }]
    res = strict_public_check(trajectory=huge_traj)
    payload = res.reject_payload()
    assert payload["ok"] is False
    assert payload["hit_count"] == 200
    assert len(payload["hits"]) == 50


# --------------------------------------------------------------------------
# Sanity for operator endpoint
# --------------------------------------------------------------------------


def test_list_rules_returns_metadata():
    rules = list_rules()
    assert len(rules) >= 8
    for r in rules:
        assert {"rule", "reason", "pattern"} <= set(r.keys())
