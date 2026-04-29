"""Skill upload, search, install, credit-assignment.

A skill is a reusable bundle: a `SKILL.md` (with YAML frontmatter) plus any
support files in the same directory. Agents can:

  * push_skill(bundle_dir)           -> validate, sanitize, hash, store, embed
  * search_skills(query, ...)         -> mixed-rank like experience search
  * get_skill(name, version=None)     -> latest by default
  * install_skill(name, target_dir)   -> roundtrip the bundle to disk

Skills are first-class in credit assignment. When a child experience push
declares `--uses-skill foo,bar`, those skill rows are wired to the experience
via `experience_skill_uses`, and when the experience earns its judge reward
the linked skills' Q values move via the same one-hop α·c update rule used
for parent experiences. This means skills the pool decides are *useful* (in
practice, not by author claim) rise in search rankings.

Sanitization: every text artifact in the bundle gets the same three-layer
treatment as a trajectory. A skill that triggers high-severity findings is
stored with `sanitization_status='human_review'` and held out of search.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import tarfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .embeddings import cosine, embed, from_blob, to_blob
from .ranking import Candidate, q_scalar, score_candidates
from .sanitize import RuleSet, layer1_text


def sanitize_text(text: str, rules: RuleSet) -> tuple[str, dict[str, int], bool]:
    """Thin convenience wrapper around `layer1_text` so this module's API
    reads cleanly. Returns (sanitized_text, redaction_counts, triggered_high_severity)."""
    return layer1_text(text, rules)

# Hard limits — defenders against tarbomb / oversized bundles.
MAX_BUNDLE_BYTES = 5 * 1024 * 1024       # 5 MB total post-sanitization
MAX_FILE_BYTES = 1 * 1024 * 1024         # 1 MB per individual file
MAX_FILE_COUNT = 200
MAX_PATH_LEN = 200

# Files we never include in a bundle even if present in the source directory.
IGNORED_NAMES = frozenset({
    ".git", ".DS_Store", "__pycache__", ".venv", "node_modules",
    ".pytest_cache", ".mypy_cache",
})


@dataclass(frozen=True)
class SkillFrontmatter:
    name: str
    description: str
    version: str = "0.1.0"
    triggers: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    raw: dict[str, Any] | None = None


@dataclass
class SkillBundle:
    """In-memory representation of an uploaded bundle, post-sanitization."""
    frontmatter: SkillFrontmatter
    content_md: str           # the SKILL.md body, sanitized
    bundle_bytes: bytes       # tar.gz
    bundle_sha256: str
    file_count: int
    sanitization_status: str
    redactions: dict[str, int]


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<yaml>.*?)\n---\s*\n(?P<body>.*)\Z",
    re.DOTALL,
)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body). Empty dict if no frontmatter."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    import yaml  # lazy

    try:
        data = yaml.safe_load(m.group("yaml")) or {}
    except yaml.YAMLError as e:  # noqa: PERF203
        raise ValueError(f"invalid YAML frontmatter: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("frontmatter must be a YAML mapping")
    return data, m.group("body")


def normalize_frontmatter(data: dict[str, Any]) -> SkillFrontmatter:
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()
    if not name:
        raise ValueError("SKILL.md frontmatter missing required field: name")
    if not re.match(r"^[a-z0-9][a-z0-9._\-:]{1,63}$", name):
        raise ValueError(
            f"skill name {name!r} must match [a-z0-9][a-z0-9._\\-:]{{1,63}}"
        )
    if not description:
        raise ValueError("SKILL.md frontmatter missing required field: description")
    version = str(data.get("version", "0.1.0")).strip() or "0.1.0"
    triggers_raw = data.get("triggers") or data.get("trigger") or []
    if isinstance(triggers_raw, str):
        triggers_raw = [t.strip() for t in triggers_raw.split(",") if t.strip()]
    deps_raw = data.get("dependencies") or data.get("depends_on") or []
    if isinstance(deps_raw, str):
        deps_raw = [d.strip() for d in deps_raw.split(",") if d.strip()]
    return SkillFrontmatter(
        name=name,
        description=description,
        version=version,
        triggers=tuple(str(t) for t in triggers_raw),
        dependencies=tuple(str(d) for d in deps_raw),
        raw=data,
    )


# ---------------------------------------------------------------------------
# Bundle building (directory -> tar.gz, with sanitization)
# ---------------------------------------------------------------------------

def _walk_bundle_dir(root: Path) -> list[Path]:
    """List files inside `root`, skipping ignored names and symlinks."""
    out: list[Path] = []
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            continue
        if any(part in IGNORED_NAMES for part in p.parts):
            continue
        if p.is_file():
            out.append(p)
    return out


def _is_text_file(path: Path) -> bool:
    if path.suffix.lower() in {
        ".md", ".txt", ".py", ".sh", ".yaml", ".yml", ".json",
        ".toml", ".ini", ".cfg", ".js", ".ts", ".tsx", ".html",
    }:
        return True
    try:
        with path.open("rb") as fh:
            head = fh.read(2048)
    except OSError:
        return False
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def build_bundle(
    bundle_dir: Path,
    rules: RuleSet,
    *,
    sensitivity: str = "medium",
) -> SkillBundle:
    bundle_dir = bundle_dir.resolve()
    if not bundle_dir.is_dir():
        raise ValueError(f"not a directory: {bundle_dir}")

    skill_md = bundle_dir / "SKILL.md"
    if not skill_md.exists():
        raise ValueError(f"missing SKILL.md in bundle: {bundle_dir}")

    files = _walk_bundle_dir(bundle_dir)
    if not files:
        raise ValueError("empty bundle")
    if len(files) > MAX_FILE_COUNT:
        raise ValueError(f"too many files ({len(files)} > {MAX_FILE_COUNT})")

    # Read SKILL.md, parse + sanitize.
    raw_md = skill_md.read_text(encoding="utf-8")
    front_dict, body = parse_frontmatter(raw_md)
    fm = normalize_frontmatter(front_dict)

    sanitized_body, body_counts, body_high = sanitize_text(body, rules)
    sanitized_desc, desc_counts, desc_high = sanitize_text(fm.description, rules)
    fm = SkillFrontmatter(
        name=fm.name, description=sanitized_desc, version=fm.version,
        triggers=fm.triggers, dependencies=fm.dependencies, raw=fm.raw,
    )

    # Build sanitized SKILL.md back.
    rebuilt_md = _rebuild_md(fm.raw or {}, sanitized_desc, sanitized_body)

    redactions: dict[str, int] = {}
    for k, v in {**body_counts, **desc_counts}.items():
        redactions[k] = redactions.get(k, 0) + v

    triggered_high = body_high or desc_high

    # Build tar.gz of the (sanitized) bundle.
    buf = io.BytesIO()
    total = 0
    file_count = 0
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in files:
            rel = path.relative_to(bundle_dir).as_posix()
            if len(rel) > MAX_PATH_LEN:
                raise ValueError(f"path too long: {rel}")
            try:
                raw_size = path.stat().st_size
            except OSError:
                raw_size = 0
            if raw_size > MAX_FILE_BYTES:
                raise ValueError(
                    f"file {rel} is {raw_size} bytes, exceeds per-file cap of {MAX_FILE_BYTES}"
                )
            if rel == "SKILL.md":
                data = rebuilt_md.encode("utf-8")
            elif _is_text_file(path):
                txt = path.read_text(encoding="utf-8", errors="replace")
                redacted, counts, hi = sanitize_text(txt, rules)
                for k, v in counts.items():
                    redactions[k] = redactions.get(k, 0) + v
                triggered_high = triggered_high or hi
                data = redacted.encode("utf-8")
            else:
                # Binary files pass through unchanged.
                data = path.read_bytes()
            if len(data) > MAX_FILE_BYTES:
                raise ValueError(
                    f"file {rel} is {len(data)} bytes after sanitization, exceeds per-file cap"
                )
            total += len(data)
            if total > MAX_BUNDLE_BYTES:
                raise ValueError(
                    f"bundle exceeds {MAX_BUNDLE_BYTES} bytes after sanitization"
                )
            info = tarfile.TarInfo(name=rel)
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
            file_count += 1

    bundle_bytes = buf.getvalue()
    sha = hashlib.sha256(bundle_bytes).hexdigest()

    if triggered_high:
        status = "human_review"
    elif redactions:
        status = "flagged"
    else:
        status = "done"
    if sensitivity == "high":
        status = "human_review"

    return SkillBundle(
        frontmatter=fm,
        content_md=rebuilt_md,
        bundle_bytes=bundle_bytes,
        bundle_sha256=sha,
        file_count=file_count,
        sanitization_status=status,
        redactions=redactions,
    )


def _rebuild_md(frontmatter: dict[str, Any], description: str, body: str) -> str:
    import yaml  # lazy
    fm = dict(frontmatter)
    fm["description"] = description
    head = yaml.safe_dump(fm, sort_keys=False).strip()
    return f"---\n{head}\n---\n{body}"


# ---------------------------------------------------------------------------
# Pool helpers (operate on a sqlite3.Connection — no ExperiencePool import to
# avoid a circular dep; the pool calls these from skills_api.py)
# ---------------------------------------------------------------------------

def _new_id() -> str:
    return str(uuid.uuid4())


def store_skill(
    conn: sqlite3.Connection,
    *,
    bundle_root: Path,
    agent_name: str,
    bundle: SkillBundle,
    acl: str = "private",
    tags: list[str] | None = None,
    sensitivity: str = "medium",
) -> dict[str, Any]:
    cur = conn.execute("SELECT agent_id FROM agents WHERE name = ?", (agent_name,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"unknown agent: {agent_name}")
    agent_id = row["agent_id"]

    # Validate declared dependencies: warn (don't fail) if a dep doesn't resolve
    # to an existing skill in this pool.
    dependency_warnings: list[str] = []
    for dep in bundle.frontmatter.dependencies:
        if "@" in dep:
            dn, dv = dep.split("@", 1)
        else:
            dn, dv = dep, None
        if resolve_skill(conn, dn, dv) is None:
            dependency_warnings.append(dep)

    # Reject if the (name, version) tuple already exists.
    cur = conn.execute(
        "SELECT skill_id FROM skills WHERE name = ? AND version = ?",
        (bundle.frontmatter.name, bundle.frontmatter.version),
    )
    existing = cur.fetchone()
    if existing:
        raise ValueError(
            f"skill {bundle.frontmatter.name}@{bundle.frontmatter.version} "
            f"already exists (skill_id={existing['skill_id']}). Bump --version."
        )

    skill_id = _new_id()
    bundles_dir = bundle_root / "skills"
    bundles_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = bundles_dir / f"{skill_id}.tar.gz"
    bundle_path.write_bytes(bundle.bundle_bytes)

    review_status = "auto_approved"
    if bundle.sanitization_status == "human_review":
        review_status = "pending"

    conn.execute(
        """
        INSERT INTO skills (
            skill_id, agent_id, name, version, description, content_md,
            bundle_path, bundle_sha256, bundle_size_bytes, file_count,
            trigger_keywords, dependencies, frontmatter_json,
            sanitization_status, review_status, acl, tags, sensitivity
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            skill_id, agent_id,
            bundle.frontmatter.name, bundle.frontmatter.version,
            bundle.frontmatter.description, bundle.content_md,
            str(bundle_path), bundle.bundle_sha256, len(bundle.bundle_bytes),
            bundle.file_count,
            json.dumps(list(bundle.frontmatter.triggers)),
            json.dumps(list(bundle.frontmatter.dependencies)),
            json.dumps(bundle.frontmatter.raw or {}),
            bundle.sanitization_status, review_status, acl,
            json.dumps(tags or []), sensitivity,
        ),
    )

    # Embed name + description for retrieval.
    intent_text = f"{bundle.frontmatter.name}: {bundle.frontmatter.description}"
    intent_vec = embed(intent_text)
    content_vec = embed(bundle.content_md)
    payload = json.dumps({"kind": "skill", "name": bundle.frontmatter.name, "acl": acl})
    conn.execute(
        "INSERT OR REPLACE INTO vectors (experience_id, kind, payload, vector) VALUES (?, ?, ?, ?)",
        (skill_id, "skill_intent", payload, to_blob(intent_vec)),
    )
    conn.execute(
        "INSERT OR REPLACE INTO vectors (experience_id, kind, payload, vector) VALUES (?, ?, ?, ?)",
        (skill_id, "skill_content", payload, to_blob(content_vec)),
    )

    conn.execute(
        "INSERT INTO audit_log (actor, actor_kind, action, target_id, payload) VALUES (?, ?, ?, ?, ?)",
        (agent_name, "agent", "push_skill", skill_id,
         json.dumps({"name": bundle.frontmatter.name,
                     "version": bundle.frontmatter.version,
                     "redactions": bundle.redactions,
                     "sanitization_status": bundle.sanitization_status})),
    )
    conn.commit()
    return {
        "skill_id": skill_id,
        "name": bundle.frontmatter.name,
        "version": bundle.frontmatter.version,
        "sanitization_status": bundle.sanitization_status,
        "review_status": review_status,
        "redactions": bundle.redactions,
        "bundle_sha256": bundle.bundle_sha256,
        "bundle_size_bytes": len(bundle.bundle_bytes),
        "file_count": bundle.file_count,
        "dependency_warnings": dependency_warnings,
    }


def search_skills(
    conn: sqlite3.Connection,
    query: str,
    *,
    top_k: int = 5,
    w_similarity: float = 0.55,
    w_q: float = 0.35,
    c_exploration: float = 0.10,
) -> list[dict[str, Any]]:
    from .fts import escape_query, rank_to_signal

    qvec = embed(query)

    # FTS5 keyword signal blended into vector cosine.
    fts_signal: dict[str, float] = {}
    fts_expr = escape_query(query)
    if fts_expr:
        try:
            fts_cur = conn.execute(
                "SELECT skill_id FROM skills_fts WHERE skills_fts MATCH ? ORDER BY rank LIMIT 200",
                (fts_expr,),
            )
            fts_ids = [r["skill_id"] for r in fts_cur.fetchall()]
            for rank, sid in enumerate(fts_ids):
                fts_signal[sid] = rank_to_signal(rank, len(fts_ids))
        except sqlite3.OperationalError:
            fts_signal = {}

    cur = conn.execute(
        """
        SELECT v.experience_id AS skill_id, v.vector, s.name, s.version,
               s.description, s.q_outcome, s.q_intent, s.q_execution,
               s.q_orchestration, s.q_expression, s.invoke_count,
               s.install_count, s.review_status, s.q_update_count
        FROM vectors v
        JOIN skills s ON s.skill_id = v.experience_id
        WHERE v.kind = 'skill_intent'
          AND s.review_status IN ('approved', 'auto_approved', 'edited')
        """
    )
    rows = cur.fetchall()
    if not rows:
        return []
    cands: list[tuple[Candidate, sqlite3.Row, float]] = []
    VEC_W, FTS_W = 0.7, 0.3
    for r in rows:
        cos = cosine(qvec, from_blob(r["vector"]))
        fts = fts_signal.get(r["skill_id"], 0.0)
        sim = VEC_W * cos + FTS_W * fts
        cands.append((
            Candidate(
                experience_id=r["skill_id"],
                similarity=sim,
                q_outcome=r["q_outcome"] or 0.0,
                q_intent=r["q_intent"] or 0.0,
                q_execution=r["q_execution"] or 0.0,
                q_orchestration=r["q_orchestration"] or 0.0,
                q_expression=r["q_expression"] or 0.0,
                visit_count=(r["invoke_count"] or 0) + (r["install_count"] or 0),
            ),
            r, sim,
        ))
    ranked = score_candidates(
        [c for c, _, _ in cands],
        w_similarity=w_similarity, w_q=w_q, c_exploration=c_exploration,
    )[:top_k]
    by_id = {c.experience_id: r for c, r, _ in cands}
    out = []
    for cand, total, sim_c, q_c, ucb_c in ranked:
        r = by_id[cand.experience_id]
        out.append({
            "skill_id": cand.experience_id,
            "name": r["name"],
            "version": r["version"],
            "description": r["description"],
            "q_scalar": q_scalar(cand),
            "q_breakdown": {
                "outcome": cand.q_outcome, "intent": cand.q_intent,
                "execution": cand.q_execution, "orchestration": cand.q_orchestration,
                "expression": cand.q_expression,
            },
            "invoke_count": r["invoke_count"] or 0,
            "install_count": r["install_count"] or 0,
            "q_update_count": r["q_update_count"] or 0,
            "score": total,
            "score_components": {
                "similarity": sim_c, "q_value": q_c, "exploration": ucb_c,
            },
        })
    return out


def resolve_skill(
    conn: sqlite3.Connection, name: str, version: str | None = None
) -> sqlite3.Row | None:
    if version:
        cur = conn.execute(
            "SELECT * FROM skills WHERE name = ? AND version = ?",
            (name, version),
        )
    else:
        cur = conn.execute(
            "SELECT * FROM skills WHERE name = ? ORDER BY created_at DESC LIMIT 1",
            (name,),
        )
    return cur.fetchone()


def install_skill(
    conn: sqlite3.Connection, name: str, target_dir: Path,
    *, version: str | None = None, agent_name: str = "anonymous",
) -> dict[str, Any]:
    row = resolve_skill(conn, name, version)
    if row is None:
        raise ValueError(f"skill not found: {name} (version={version})")
    bundle_path = Path(row["bundle_path"])
    if not bundle_path.exists():
        raise ValueError(f"bundle file missing on disk: {bundle_path}")
    target_dir = target_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[str] = []
    with tarfile.open(bundle_path, "r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile():
                # Path traversal guard.
                target_path = (target_dir / member.name).resolve()
                if not str(target_path).startswith(str(target_dir)):
                    raise ValueError(f"unsafe path in bundle: {member.name}")
                target_path.parent.mkdir(parents=True, exist_ok=True)
                fobj = tar.extractfile(member)
                if fobj is None:
                    continue
                target_path.write_bytes(fobj.read())
                extracted.append(member.name)
    conn.execute(
        "UPDATE skills SET install_count = install_count + 1 WHERE skill_id = ?",
        (row["skill_id"],),
    )
    conn.execute(
        "INSERT INTO audit_log (actor, actor_kind, action, target_id, payload) VALUES (?, ?, ?, ?, ?)",
        (agent_name, "agent", "install_skill", row["skill_id"],
         json.dumps({"name": name, "version": row["version"], "to": str(target_dir)})),
    )
    conn.commit()
    return {
        "skill_id": row["skill_id"],
        "name": row["name"],
        "version": row["version"],
        "extracted": extracted,
        "target_dir": str(target_dir),
    }


# ---------------------------------------------------------------------------
# Wiring + credit assignment
# ---------------------------------------------------------------------------

def link_experience_uses(
    conn: sqlite3.Connection,
    experience_id: str,
    skill_names: list[str],
) -> list[str]:
    """Resolve names -> skill_ids and record usage. Returns the resolved IDs."""
    resolved: list[str] = []
    for name in skill_names:
        # Allow `name@version` form.
        if "@" in name:
            n, v = name.split("@", 1)
        else:
            n, v = name, None
        row = resolve_skill(conn, n, v)
        if row is None:
            # Skip silently — logging would be tempting but this is an agent-
            # supplied list and we don't want one typo to fail the whole push.
            continue
        sid = row["skill_id"]
        conn.execute(
            "INSERT OR IGNORE INTO experience_skill_uses (experience_id, skill_id) VALUES (?, ?)",
            (experience_id, sid),
        )
        conn.execute(
            "UPDATE skills SET invoke_count = invoke_count + 1 WHERE skill_id = ?",
            (sid,),
        )
        resolved.append(sid)
    conn.commit()
    return resolved


def apply_skill_credit(
    conn: sqlite3.Connection,
    experience_id: str,
    *,
    alpha: float = 0.2,
) -> int:
    """One-hop credit from a child experience's reward to every skill it used."""
    DIMS = ("outcome", "intent", "execution", "orchestration", "expression")
    cur = conn.execute(
        """
        SELECT r_outcome, r_intent, r_execution, r_orchestration, r_expression, confidence
        FROM rewards WHERE experience_id = ? ORDER BY created_at DESC LIMIT 1
        """,
        (experience_id,),
    )
    reward = cur.fetchone()
    if reward is None:
        return 0
    cur = conn.execute(
        "SELECT skill_id FROM experience_skill_uses WHERE experience_id = ? AND credit_applied = 0",
        (experience_id,),
    )
    skill_ids = [r["skill_id"] for r in cur.fetchall()]
    if not skill_ids:
        return 0

    conf = float(reward["confidence"])
    eff = alpha * conf
    n = 0
    for sid in skill_ids:
        cur = conn.execute(
            "SELECT q_outcome, q_intent, q_execution, q_orchestration, q_expression FROM skills WHERE skill_id = ?",
            (sid,),
        )
        sk = cur.fetchone()
        if sk is None:
            continue
        new_q = {}
        deltas = {}
        for d in DIMS:
            old = float(sk[f"q_{d}"] or 0.0)
            r = float(reward[f"r_{d}"])
            new = (1 - eff) * old + eff * r
            new_q[d] = new
            deltas[d] = new - old
        conn.execute(
            """
            UPDATE skills SET
              q_outcome = ?, q_intent = ?, q_execution = ?,
              q_orchestration = ?, q_expression = ?,
              q_update_count = q_update_count + 1
            WHERE skill_id = ?
            """,
            (new_q["outcome"], new_q["intent"], new_q["execution"],
             new_q["orchestration"], new_q["expression"], sid),
        )
        conn.execute(
            """
            INSERT INTO skill_q_updates (
                update_id, skill_id, triggered_by_experience, alpha, confidence,
                delta_outcome, delta_intent, delta_execution,
                delta_orchestration, delta_expression
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (_new_id(), sid, experience_id, alpha, conf,
             deltas["outcome"], deltas["intent"], deltas["execution"],
             deltas["orchestration"], deltas["expression"]),
        )
        conn.execute(
            "UPDATE experience_skill_uses SET credit_applied = 1 WHERE experience_id = ? AND skill_id = ?",
            (experience_id, sid),
        )
        n += 1
    conn.execute(
        "INSERT INTO audit_log (actor, actor_kind, action, target_id, payload) VALUES (?, ?, ?, ?, ?)",
        ("credit_assigner", "system", "skill_credit_applied", experience_id,
         json.dumps({"skills_updated": n})),
    )
    conn.commit()
    return n
