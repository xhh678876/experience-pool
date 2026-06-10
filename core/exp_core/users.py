"""Email/password user accounts on top of the agent identity layer.

Each user can own one or more agents. A user signs up with an email and a
self-chosen password; the server creates:
  1. A row in `users` with pwd_hash (PBKDF2-SHA256, 600k iters)
  2. A default agent (name derived from email local-part) with HMAC secret
  3. A session token (HMAC-signed) returned as a cookie

The intent is that users go to the web portal once, register, copy the
displayed `curl ... | bash` bind command into any agent terminal, and from
then on every trace that agent uploads is owned by them.

Password hashing notes: PBKDF2-SHA256 is stdlib; argon2id would be preferred
but adds a dependency. Iter count = 600k (~250ms on a modern CPU). Swap to
argon2id by changing _hash_password / _verify_password if/when the lib is
available.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass

from .identity import issue_credential, load_credential

USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    pwd_hash TEXT NOT NULL,           -- "pbkdf2_sha256$<iter>$<salt_b64>$<hash_b64>"
    default_agent_name TEXT NOT NULL, -- FK-ish to agents.name, but not enforced
    display_name TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS user_sessions (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    issued_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    user_agent TEXT,
    ip TEXT,
    revoked INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS user_sessions_user_idx
    ON user_sessions (user_id, revoked, expires_at);
"""

PBKDF2_ITERATIONS = 600_000
PBKDF2_KEYLEN = 32
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days
ALLOWED_EMAIL_DOMAIN = os.getenv("EXP_EMAIL_DOMAIN", "")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

# Module-level: a server secret used to HMAC-sign session tokens. Loaded from
# EXP_SESSION_SECRET, or persisted in the user data dir on first run.
_SESSION_SECRET_CACHE: bytes | None = None


@dataclass
class User:
    user_id: str
    email: str
    default_agent_name: str
    display_name: str | None
    created_at: str


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(USERS_SCHEMA)


# ---------- password hashing ----------------------------------------------

def _hash_password(plain: str) -> str:
    if not plain or len(plain) < 8:
        raise ValueError("password must be at least 8 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt,
                                 PBKDF2_ITERATIONS, dklen=PBKDF2_KEYLEN)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
    )


def _verify_password(plain: str, stored: str) -> bool:
    try:
        algo, iters_s, salt_b64, hash_b64 = stored.split("$", 3)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    try:
        iters = int(iters_s)
    except ValueError:
        return False
    pad = "=" * ((4 - len(salt_b64) % 4) % 4)
    salt = base64.urlsafe_b64decode(salt_b64 + pad)
    pad2 = "=" * ((4 - len(hash_b64) % 4) % 4)
    expected = base64.urlsafe_b64decode(hash_b64 + pad2)
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt,
                                 iters, dklen=len(expected))
    return hmac.compare_digest(digest, expected)


# ---------- session tokens ------------------------------------------------

def _session_secret() -> bytes:
    global _SESSION_SECRET_CACHE
    if _SESSION_SECRET_CACHE is not None:
        return _SESSION_SECRET_CACHE
    env = os.getenv("EXP_SESSION_SECRET")
    if env:
        _SESSION_SECRET_CACHE = env.encode("utf-8")
        return _SESSION_SECRET_CACHE
    root = os.getenv("EXP_ROOT")
    from pathlib import Path
    base = Path(root) if root else Path.home() / ".experience-pool"
    base.mkdir(parents=True, exist_ok=True)
    p = base / "session_secret"
    if p.exists():
        _SESSION_SECRET_CACHE = p.read_bytes().strip()
    else:
        _SESSION_SECRET_CACHE = secrets.token_bytes(32)
        p.write_bytes(_SESSION_SECRET_CACHE)
        try:
            p.chmod(0o600)
        except OSError:
            pass
    return _SESSION_SECRET_CACHE


def _new_session_token(user_id: str) -> str:
    payload = f"{user_id}.{int(time.time())}.{secrets.token_urlsafe(16)}"
    sig = hmac.new(_session_secret(), payload.encode("utf-8"),
                   hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _validate_session_token(token: str) -> str | None:
    """Return user_id if HMAC matches and token is well-formed. Existence in
    user_sessions table is checked separately (via DB)."""
    parts = token.rsplit(".", 1)
    if len(parts) != 2:
        return None
    payload, sig_hex = parts
    expected = hmac.new(_session_secret(), payload.encode("utf-8"),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig_hex, expected):
        return None
    bits = payload.split(".")
    if len(bits) < 3:
        return None
    return bits[0]


# ---------- registration / login -------------------------------------------

def _email_local_part(email: str) -> str:
    return email.split("@", 1)[0].lower()


def _agent_name_for_email(email: str) -> str:
    """Generate a stable agent name from the email local part."""
    local = _email_local_part(email)
    safe = re.sub(r"[^a-z0-9._-]+", "-", local)
    return f"user-{safe}"


def validate_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError("invalid email format")
    if ALLOWED_EMAIL_DOMAIN and not email.endswith("@" + ALLOWED_EMAIL_DOMAIN):
        raise ValueError(f"email must end with @{ALLOWED_EMAIL_DOMAIN}")
    return email


def register_user(
    conn: sqlite3.Connection,
    *,
    email: str,
    password: str,
    display_name: str | None = None,
) -> tuple[User, dict[str, str]]:
    """Create a user + their default agent. Returns (User, credential_dict).

    The credential dict has agent_name + secret + agent_id, which the caller
    can pass back to the client (this is the *only* time the secret is shown
    in plaintext from the server's POV).
    """
    email = validate_email(email)
    pwd_hash = _hash_password(password)
    user_id = str(uuid.uuid4())
    agent_name = _agent_name_for_email(email)

    # Create the agent first (so we have a name to record on the user row).
    # If an agent with this name already exists (re-registration after a
    # crash, or two users sharing an email local-part), we re-use it.
    existing = conn.execute(
        "SELECT agent_id FROM agents WHERE name = ?", (agent_name,)
    ).fetchone()
    if existing:
        agent_id = existing["agent_id"] if isinstance(existing, sqlite3.Row) else existing[0]
    else:
        agent_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO agents (agent_id, name, team) VALUES (?, ?, ?)",
            (agent_id, agent_name, "default"),
        )

    # Owner column was added later; if present, stamp it.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(agents)").fetchall()}
    if "owner" in cols:
        conn.execute("UPDATE agents SET owner = ? WHERE agent_id = ?",
                     (email, agent_id))

    # Issue an HMAC credential (writes to credentials/<agent_name>.json on
    # disk so the existing auth middleware finds it).
    cred = issue_credential(agent_id, agent_name, "default")

    try:
        conn.execute(
            """
            INSERT INTO users (user_id, email, pwd_hash, default_agent_name,
                               display_name)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, email, pwd_hash, agent_name, display_name),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("email already registered") from exc
    conn.commit()

    return (
        User(user_id=user_id, email=email,
             default_agent_name=agent_name,
             display_name=display_name, created_at=""),
        cred.to_dict(),
    )


def authenticate(
    conn: sqlite3.Connection, *, email: str, password: str,
) -> User | None:
    email = (email or "").strip().lower()
    row = conn.execute(
        "SELECT user_id, email, pwd_hash, default_agent_name, display_name, "
        "created_at FROM users WHERE email = ?",
        (email,),
    ).fetchone()
    if row is None:
        return None
    if not _verify_password(password, row["pwd_hash"] if isinstance(row, sqlite3.Row) else row[2]):
        return None
    conn.execute("UPDATE users SET last_login_at = datetime('now') WHERE user_id = ?",
                 (row["user_id"],))
    conn.commit()
    return User(
        user_id=row["user_id"],
        email=row["email"],
        default_agent_name=row["default_agent_name"],
        display_name=row["display_name"],
        created_at=row["created_at"],
    )


def create_session(
    conn: sqlite3.Connection, *, user_id: str,
    user_agent: str | None = None, ip: str | None = None,
) -> tuple[str, int]:
    """Returns (token, expires_at_unix)."""
    token = _new_session_token(user_id)
    expires = int(time.time()) + SESSION_TTL_SECONDS
    conn.execute(
        """
        INSERT INTO user_sessions (token, user_id, expires_at, user_agent, ip)
        VALUES (?, ?, datetime(?, 'unixepoch'), ?, ?)
        """,
        (token, user_id, expires, user_agent, ip),
    )
    conn.commit()
    return token, expires


def lookup_session(conn: sqlite3.Connection, token: str) -> User | None:
    if not token:
        return None
    user_id = _validate_session_token(token)
    if user_id is None:
        return None
    row = conn.execute(
        """
        SELECT u.user_id, u.email, u.default_agent_name, u.display_name,
               u.created_at, s.expires_at, s.revoked
        FROM user_sessions s
        JOIN users u ON u.user_id = s.user_id
        WHERE s.token = ?
        """,
        (token,),
    ).fetchone()
    if row is None:
        return None
    if (row["revoked"] if isinstance(row, sqlite3.Row) else row[6]):
        return None
    expires_at = row["expires_at"] if isinstance(row, sqlite3.Row) else row[5]
    if expires_at and expires_at < time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()):
        return None
    return User(
        user_id=row["user_id"],
        email=row["email"],
        default_agent_name=row["default_agent_name"],
        display_name=row["display_name"],
        created_at=row["created_at"],
    )


def revoke_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("UPDATE user_sessions SET revoked = 1 WHERE token = ?", (token,))
    conn.commit()


# ---------- bind script ----------------------------------------------------

def render_bind_script(*, base_url: str, agent_name: str, secret: str,
                       team: str = "default", agent_id: str | None = None) -> str:
    """One-liner the user pastes into any agent terminal. Downloads the
    installer rewritten to point at THIS server, pre-seeds credentials
    so no `register` HTTP call happens, and wires hooks."""
    base_url = base_url.rstrip("/")
    parts = [
        f"curl -sSL {base_url}/install |",
        f"EXP_AGENT_NAME='{agent_name}'",
        f"EXP_AGENT_SECRET='{secret}'",
        f"EXP_TEAM='{team}'",
        f"EXP_NONINTERACTIVE=1",
        f"EXP_BASE_URL='{base_url}'",
    ]
    if agent_id:
        parts.append(f"EXP_AGENT_ID='{agent_id}'")
    parts.append("bash")
    return " ".join(parts)


def get_credential_for_user(user: User) -> dict[str, str] | None:
    """Look up the on-disk credential file for the user's default agent.
    Returns the parsed dict (with secret) or None if not found.

    Note: credentials are written to disk as JSON when issue_credential() is
    called during registration. We re-load them here so we can include the
    secret in the bind script. This means the API server must have read
    access to the credentials directory — it does, since it's the same
    process that wrote them.
    """
    cred = load_credential(user.default_agent_name)
    if cred is None:
        return None
    return cred.to_dict()
