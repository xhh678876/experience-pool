import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _clean_env(**values: str) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("EXP_", "EXPOOL_"))
    }
    env.update(values)
    return env


def _read_profile(**values: str) -> dict[str, str]:
    command = """
      . config/env.sh
      printf '%s\\n' \
        "$EXP_ENV" "$EXP_ROOT" "$EXP_DB_PATH" \
        "$EXP_PLUGIN_REPO" "$EXPOOL_PORTAL_ROOT" \
        "$EXP_API_PORT" "$EXP_UI_PORT" "$EXP_GATEWAY_PORT"
    """
    completed = subprocess.run(
        ["bash", "-c", command],
        cwd=REPO_ROOT,
        env=_clean_env(**values),
        check=True,
        capture_output=True,
        text=True,
    )
    keys = (
        "env",
        "root",
        "db",
        "plugin_repo",
        "portal_root",
        "api_port",
        "ui_port",
        "gateway_port",
    )
    return dict(zip(keys, completed.stdout.splitlines(), strict=True))


def test_development_profile_is_isolated_from_production_data():
    config = _read_profile(EXP_ENV="development")

    assert config["env"] == "development"
    assert config["root"] == str(REPO_ROOT / ".experience-pool-dev")
    assert config["db"] == str(REPO_ROOT / ".experience-pool-dev" / "pool.db")
    assert config["plugin_repo"] == str(REPO_ROOT.parent / "expool-mcp-plugin")
    assert config["portal_root"] == str(REPO_ROOT)
    assert (config["api_port"], config["ui_port"], config["gateway_port"]) == (
        "8080",
        "3000",
        "3080",
    )


def test_exported_values_override_profile_defaults(tmp_path):
    config = _read_profile(
        EXP_ENV="development",
        EXP_ROOT=str(tmp_path / "custom"),
        EXP_API_PORT="19080",
    )

    assert config["root"] == str(tmp_path / "custom")
    assert config["db"] == str(tmp_path / "custom" / "pool.db")
    assert config["api_port"] == "19080"


def test_unknown_profile_fails_closed():
    completed = subprocess.run(
        ["bash", "config/env.sh"],
        cwd=REPO_ROOT,
        env=_clean_env(EXP_ENV="staging/../../bad"),
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "unsupported EXP_ENV" in completed.stderr
