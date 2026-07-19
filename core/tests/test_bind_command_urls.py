from __future__ import annotations

from types import SimpleNamespace

from exp_core import server as server_mod
from exp_core import users


def test_public_base_prefers_ui_proxy_when_bind_base_is_loopback(monkeypatch):
    monkeypatch.setenv("EXP_BIND_BASE_URL", "http://127.0.0.1:3080")
    monkeypatch.setenv(
        "EXP_UI_PUBLIC_URL",
        "https://nat2.example/ws/proj/user/vscode/id/session/proxy/3002",
    )
    monkeypatch.delenv("EXP_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("EXP_PUBLIC_API_BASE", raising=False)

    request = SimpleNamespace(headers={}, url=SimpleNamespace(scheme="http"))

    assert (
        server_mod._request_public_base_url(request)  # noqa: SLF001
        == "https://nat2.example/ws/proj/user/vscode/id/session/proxy/3080"
    )


def test_backfill_command_uses_env_before_bash_c():
    cmd = users.render_backfill_script(
        base_url="https://nat2.example/proxy/3080",
        agent_name="user-253208120278",
        secret="secret-with-'quote",
    )

    assert cmd.startswith("env EXP_AGENT_NAME=")
    assert " EXP_BASE_URL='https://nat2.example/proxy/3080' " in cmd
    assert " bash -c " in cmd
    assert 'curl -fsSL "$EXP_BASE_URL/session-extractor/run.sh" | bash' in cmd
    assert "| EXP_AGENT_NAME=" not in cmd


def test_bind_command_uses_public_base_in_env():
    cmd = users.render_bind_script(
        base_url="https://nat2.example/proxy/3080",
        agent_name="user-demo",
        secret="secret",
        team="default",
        agent_id="agent-1",
    )

    assert cmd.startswith("env EXP_AGENT_NAME=")
    assert " EXP_BASE_URL='https://nat2.example/proxy/3080' " in cmd
    assert " EXP_AGENT_ID='agent-1' " in cmd
    assert " bash -c " in cmd
    assert 'curl -fsSL "$EXP_BASE_URL/install" | bash' in cmd
    assert "| EXP_AGENT_NAME=" not in cmd
