"""End-to-end tests for skill upload, search, install, and credit assignment.
Uses EXP_LLM=mock so no real LLM calls."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("EXP_LLM", "mock")

import pytest  # noqa: E402

from exp_core.pool import ExperiencePool, PoolConfig  # noqa: E402
from exp_core.skills import (  # noqa: E402
    build_bundle,
    normalize_frontmatter,
    parse_frontmatter,
)
from exp_core.sanitize import load_rules  # noqa: E402


# ---------- Fixture builders ----------

def write_skill(tmp: Path, name: str, *, description: str = "Help with CSV files",
                version: str = "0.1.0",
                extra_files: dict[str, str] | None = None,
                body: str | None = None) -> Path:
    skill_dir = tmp / name
    skill_dir.mkdir(parents=True)
    body = body or "## Usage\n\nRun this skill when you have a CSV.\n"
    md = (
        f"---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"version: {version}\n"
        f"triggers:\n  - csv\n  - sales\n"
        f"---\n"
        f"{body}"
    )
    (skill_dir / "SKILL.md").write_text(md)
    for fn, content in (extra_files or {}).items():
        path = skill_dir / fn
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return skill_dir


# ---------- Frontmatter parsing ----------

def test_parse_frontmatter_basic():
    text = "---\nname: foo\ndescription: bar\n---\nbody here\n"
    fm, body = parse_frontmatter(text)
    assert fm == {"name": "foo", "description": "bar"}
    assert body == "body here\n"


def test_parse_frontmatter_missing_returns_empty():
    fm, body = parse_frontmatter("just a body, no frontmatter")
    assert fm == {}
    assert body.startswith("just a body")


def test_normalize_rejects_missing_name():
    with pytest.raises(ValueError, match="name"):
        normalize_frontmatter({"description": "x"})


def test_normalize_rejects_invalid_name_chars():
    with pytest.raises(ValueError, match="must match"):
        normalize_frontmatter({"name": "Has Spaces", "description": "x"})


def test_normalize_accepts_string_triggers():
    fm = normalize_frontmatter({"name": "ok", "description": "x", "triggers": "a, b ,c"})
    assert fm.triggers == ("a", "b", "c")


# ---------- Bundle building ----------

def test_build_bundle_clean(tmp_path):
    skill_dir = write_skill(tmp_path, "csv-helper",
                            extra_files={"helpers/parse.py": "def parse(x):\n    return x\n"})
    rules = load_rules()
    bundle = build_bundle(skill_dir, rules)
    assert bundle.frontmatter.name == "csv-helper"
    assert bundle.file_count == 2
    assert bundle.sanitization_status == "done"
    assert bundle.bundle_sha256


def test_build_bundle_redacts_secrets(tmp_path):
    skill_dir = write_skill(
        tmp_path, "leaky",
        extra_files={"config.py": 'API_KEY = "sk-ant-AAAAAAAAAAAAAAAAAAAAA"\n'},
    )
    rules = load_rules()
    bundle = build_bundle(skill_dir, rules)
    assert bundle.sanitization_status == "human_review", bundle.redactions
    assert any(k in bundle.redactions for k in ("anthropic_key", "generic_api_key"))


def test_build_bundle_rejects_oversized(tmp_path, monkeypatch):
    # Force a tiny limit so we can trigger the check without writing 5 MB.
    from exp_core import skills
    monkeypatch.setattr(skills, "MAX_BUNDLE_BYTES", 256)
    skill_dir = write_skill(
        tmp_path, "big",
        extra_files={"data.txt": "x" * 4096},
    )
    with pytest.raises(ValueError, match="exceeds"):
        build_bundle(skill_dir, load_rules())


# ---------- Pool integration ----------

def trajectory(extra: str = "") -> list[dict]:
    return [
        {"role": "user", "content": f"task {extra}"},
        {"role": "assistant", "content": "did the task"},
    ]


def test_push_search_install_roundtrip(tmp_path):
    skill_dir = write_skill(tmp_path, "csv-helper")
    pool = ExperiencePool(PoolConfig(root=tmp_path / "pool"))
    pool.register_agent("alice", "platform")

    info = pool.push_skill("alice", skill_dir, sensitivity="low", acl="org")
    assert info["sanitization_status"] == "done"
    assert info["review_status"] == "auto_approved"
    assert info["file_count"] >= 1

    hits = pool.search_skills("csv parsing helper", top_k=5)
    assert hits, "skill search returned nothing"
    assert hits[0]["name"] == "csv-helper"
    assert "score_components" in hits[0]

    target = tmp_path / "installed"
    install = pool.install_skill("csv-helper", target, agent_name="alice")
    assert (target / "SKILL.md").exists()
    assert install["extracted"] == ["SKILL.md"]
    pool.close()


def test_uses_skill_credit_propagates(tmp_path):
    skill_dir = write_skill(tmp_path, "csv-helper")
    pool = ExperiencePool(PoolConfig(root=tmp_path / "pool"))
    pool.register_agent("alice", "platform")

    pool.push_skill("alice", skill_dir, sensitivity="low", acl="org")
    # Q starts at 0 (skills don't get an initial reward).
    cur = pool.conn.execute(
        "SELECT q_outcome, q_update_count, invoke_count FROM skills WHERE name='csv-helper'"
    )
    row = cur.fetchone()
    assert row["q_update_count"] == 0
    assert row["invoke_count"] == 0

    # Push an experience that declares it used the skill.
    pool.push("alice", "csv_analysis", "stub", trajectory("a"),
              uses_skills=["csv-helper"], sensitivity="low")

    cur = pool.conn.execute(
        "SELECT q_outcome, q_update_count, invoke_count FROM skills WHERE name='csv-helper'"
    )
    after = cur.fetchone()
    assert after["invoke_count"] == 1, "invoke_count should bump on link"
    assert after["q_update_count"] == 1, "skill credit should fire after experience reward"
    # With mock judge giving r_outcome=0.7 and skill q starting at 0, the
    # update is (1 - 0.2 * 0.8) * 0 + 0.2 * 0.8 * 0.7 = 0.112.
    assert 0.05 < after["q_outcome"] < 0.2, after["q_outcome"]
    pool.close()


def test_install_unknown_skill_raises(tmp_path):
    pool = ExperiencePool(PoolConfig(root=tmp_path / "pool"))
    pool.register_agent("alice", "platform")
    with pytest.raises(ValueError, match="not found"):
        pool.install_skill("does-not-exist", tmp_path / "x")
    pool.close()


def test_uses_skills_with_unknown_name_silently_skipped(tmp_path):
    """Agent typo'd a skill name. We don't fail the push; we just don't link."""
    pool = ExperiencePool(PoolConfig(root=tmp_path / "pool"))
    pool.register_agent("alice", "platform")
    result = pool.push("alice", "csv_analysis", "stub", trajectory("a"),
                       uses_skills=["typo-not-real"], sensitivity="low")
    assert result["extraction_status"] == "done"
    cur = pool.conn.execute("SELECT COUNT(*) AS n FROM experience_skill_uses")
    assert cur.fetchone()["n"] == 0
    pool.close()


def test_per_file_cap_rejected(tmp_path, monkeypatch):
    from exp_core import skills
    monkeypatch.setattr(skills, "MAX_FILE_BYTES", 100)
    skill_dir = write_skill(tmp_path, "fat-file",
                            extra_files={"helpers.py": "x" * 4096})
    with pytest.raises(ValueError, match="per-file cap"):
        skills.build_bundle(skill_dir, load_rules())


def test_dependency_warning_for_missing(tmp_path):
    skill_dir = write_skill(tmp_path, "needs-dep",
                            body="Something\n",
                            extra_files=None)
    # Re-write the SKILL.md to add a dependency that doesn't exist.
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: needs-dep\n"
        "description: Depends on something else\n"
        "version: 0.1.0\n"
        "dependencies:\n  - does-not-exist\n  - also-missing@1.0.0\n"
        "---\n"
        "body\n"
    )
    pool = ExperiencePool(PoolConfig(root=tmp_path / "pool"))
    pool.register_agent("alice", "platform")
    info = pool.push_skill("alice", skill_dir, sensitivity="low")
    assert set(info["dependency_warnings"]) == {"does-not-exist", "also-missing@1.0.0"}
    pool.close()


def test_fts_keyword_search(tmp_path):
    """A query that has nothing in common with the hash-based vector should
    still match via FTS keywords if the words appear in description."""
    skill_dir = write_skill(
        tmp_path, "kafka-tools",
        description="Manage Kafka topic partitions and consumer offsets safely.",
    )
    pool = ExperiencePool(PoolConfig(root=tmp_path / "pool"))
    pool.register_agent("alice", "platform")
    pool.push_skill("alice", skill_dir, sensitivity="low", acl="org")

    # Add some noise so FTS has competition.
    other = write_skill(
        tmp_path, "html-renderer",
        description="Render HTML templates with sandboxed evaluation.",
    )
    pool.push_skill("alice", other, sensitivity="low", acl="org")

    hits = pool.search_skills("kafka consumer offsets", top_k=5)
    assert hits[0]["name"] == "kafka-tools", [h["name"] for h in hits]
    pool.close()


def test_duplicate_name_version_rejected(tmp_path):
    skill_dir = write_skill(tmp_path, "csv-helper")
    pool = ExperiencePool(PoolConfig(root=tmp_path / "pool"))
    pool.register_agent("alice", "platform")
    pool.push_skill("alice", skill_dir, sensitivity="low")
    with pytest.raises(ValueError, match="already exists"):
        pool.push_skill("alice", skill_dir, sensitivity="low")
    pool.close()
