"""User-scoped API keys.

Lets a logged-in user mint Bearer tokens that the FastAPI gateway accepts
in place of HMAC-signed requests. Each key is tied to a single agent_name
(usually the user's default agent) so the existing per-row ACL semantics
keep working — an experience created via a key looks identical to one
created via HMAC under that same agent.

The raw key is returned to the caller exactly once on creation. We store
only sha256(raw_key); on lookup we re-hash the inbound bearer and compare.

Key format: ``expk_<32 url-safe random chars>``. The ``expk_`` prefix is
purely cosmetic so users and log greppers can recognize one at a glance.

Concurrency note: all WRITES use a short-lived connection opened against
the same DB file rather than the long-lived `pool.conn`. The shared
connection often holds an implicit transaction across requests (FastAPI
worker reuses it between calls), which under multi-worker uvicorn can
make even a 30-second busy_timeout insufficient. A fresh connection per
write avoids that whole class of "database is locked" errors.
"""

from __future__ import annotations

import contextlib
import hashlib
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

API_KEYS_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    key_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    key_prefix TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT,
    revoked_at TEXT
);

CREATE INDEX IF NOT EXISTS api_keys_user_idx
    ON api_keys (user_id, revoked_at);

CREATE INDEX IF NOT EXISTS api_keys_lookup_idx
    ON api_keys (key_hash) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS plugin_pairing_codes (
    pair_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    name TEXT NOT NULL,
    code_hash TEXT NOT NULL UNIQUE,
    code_prefix TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    claimed_at TEXT,
    claimed_key_id TEXT
);

CREATE INDEX IF NOT EXISTS plugin_pairing_user_idx
    ON plugin_pairing_codes (user_id, claimed_at, expires_at);

CREATE INDEX IF NOT EXISTS plugin_pairing_lookup_idx
    ON plugin_pairing_codes (code_hash) WHERE claimed_at IS NULL;
"""

KEY_PREFIX = "expk_"
KEY_RANDOM_LEN = 32  # url-safe chars after the prefix
MAX_KEYS_PER_USER = 5
PAIRING_CODE_PREFIX = "expair_"
PAIRING_CODE_RANDOM_LEN = 24
PAIRING_CODE_DEFAULT_TTL_SECONDS = 10 * 60
PAIRING_CODE_MAX_TTL_SECONDS = 60 * 60


@dataclass
class ApiKeyRow:
    key_id: str
    user_id: str
    agent_name: str
    name: str
    key_prefix: str
    created_at: str
    last_used_at: str | None
    revoked_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key_id": self.key_id,
            "user_id": self.user_id,
            "agent_name": self.agent_name,
            "name": self.name,
            "key_prefix": self.key_prefix,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "revoked": self.revoked_at is not None,
            "revoked_at": self.revoked_at,
        }


@dataclass
class PairingCodeRow:
    pair_id: str
    user_id: str
    agent_name: str
    name: str
    code_prefix: str
    created_at: str
    expires_at: str
    claimed_at: str | None
    claimed_key_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "user_id": self.user_id,
            "agent_name": self.agent_name,
            "name": self.name,
            "code_prefix": self.code_prefix,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "claimed": self.claimed_at is not None,
            "claimed_at": self.claimed_at,
            "claimed_key_id": self.claimed_key_id,
        }


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(API_KEYS_SCHEMA)


@contextlib.contextmanager
def _write_conn(read_conn: sqlite3.Connection):
    """Yield a fresh write connection bound to the same DB file.

    Avoids "database is locked" errors caused by stale implicit
    transactions on the long-lived `pool.conn` under concurrent worker
    load. Uses WAL mode (inherits per-db) and a 30s busy_timeout so
    competing writers wait their turn.
    """
    db_path = _db_path_from(read_conn)
    write = sqlite3.connect(db_path, timeout=30.0)
    try:
        write.execute("PRAGMA busy_timeout = 30000")
        yield write
        write.commit()
    finally:
        write.close()


def _db_path_from(conn: sqlite3.Connection) -> str:
    """Recover the source DB path from a connection. Falls back to ':memory:'
    if SQLite reports an empty filename (in-memory DB used by tests).
    """
    row = conn.execute("PRAGMA database_list").fetchone()
    # PRAGMA database_list returns (seq, name, file). `name` for the
    # default is 'main'; `file` is the absolute path or '' for :memory:.
    if row is None:
        return ":memory:"
    # sqlite3.Row supports both index and key access; plain tuple only index.
    try:
        file_str = row["file"]  # type: ignore[index]
    except (TypeError, IndexError):
        file_str = row[2]
    return file_str or ":memory:"


def _retry_locked(func, *, attempts: int = 3, delay: float = 0.2):
    """Run a SQLite write op with backoff if 'database is locked' fires."""
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return func()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or i == attempts - 1:
                raise
            last_exc = exc
            time.sleep(delay * (2 ** i))
    if last_exc:
        raise last_exc


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _generate_raw() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(KEY_RANDOM_LEN)[:KEY_RANDOM_LEN]


def _generate_pairing_code() -> str:
    return PAIRING_CODE_PREFIX + secrets.token_urlsafe(PAIRING_CODE_RANDOM_LEN)[:PAIRING_CODE_RANDOM_LEN]


def _utc_now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def _utc_after_str(seconds: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + seconds))


class ApiKeyError(Exception):
    """Raised on policy violations (quota, missing key, etc.)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def create(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    agent_name: str,
    name: str,
) -> tuple[str, ApiKeyRow]:
    """Mint a new key. Returns (raw_key, row). Raw key is shown ONCE."""
    name = (name or "").strip()
    if not name:
        raise ApiKeyError("invalid_name", "name must not be empty")
    if len(name) > 64:
        raise ApiKeyError("invalid_name", "name too long (max 64)")

    active = conn.execute(
        "SELECT COUNT(*) FROM api_keys WHERE user_id = ? AND revoked_at IS NULL",
        (user_id,),
    ).fetchone()[0]
    if active >= MAX_KEYS_PER_USER:
        raise ApiKeyError(
            "quota_exceeded",
            f"each user can have at most {MAX_KEYS_PER_USER} active keys; "
            f"revoke one first",
        )

    raw = _generate_raw()
    key_hash = _hash_key(raw)
    key_id = str(uuid.uuid4())
    key_prefix = raw[: len(KEY_PREFIX) + 4]

    def _do_insert() -> None:
        with _write_conn(conn) as w:
            w.execute(
                "INSERT INTO api_keys (key_id, user_id, agent_name, name, key_hash, key_prefix) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key_id, user_id, agent_name, name, key_hash, key_prefix),
            )

    _retry_locked(_do_insert)
    row = _select_by_id(conn, key_id)
    assert row is not None
    return raw, row


def list_for_user(conn: sqlite3.Connection, user_id: str) -> list[ApiKeyRow]:
    rows = conn.execute(
        "SELECT key_id, user_id, agent_name, name, key_prefix, "
        "       created_at, last_used_at, revoked_at "
        "FROM api_keys WHERE user_id = ? "
        "ORDER BY (revoked_at IS NOT NULL), created_at DESC",
        (user_id,),
    ).fetchall()
    return [_row_to_obj(r) for r in rows]


def revoke(conn: sqlite3.Connection, key_id: str, user_id: str) -> bool:
    """Soft-revoke. Returns True if the row was found and not already revoked."""
    rowcount = 0

    def _do_update() -> None:
        nonlocal rowcount
        with _write_conn(conn) as w:
            cur = w.execute(
                "UPDATE api_keys "
                "SET revoked_at = datetime('now') "
                "WHERE key_id = ? AND user_id = ? AND revoked_at IS NULL",
                (key_id, user_id),
            )
            rowcount = cur.rowcount

    _retry_locked(_do_update)
    return rowcount > 0


def create_pairing_code(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    agent_name: str,
    name: str,
    ttl_seconds: int = PAIRING_CODE_DEFAULT_TTL_SECONDS,
) -> tuple[str, PairingCodeRow]:
    """Create a short-lived, one-time code a plugin can exchange for a key.

    The raw API key is not created until claim time, so the browser never has
    to display or copy an ``expk_...`` secret for the pairing flow.
    """
    name = (name or "plugin-pairing").strip()
    if not name:
        raise ApiKeyError("invalid_name", "name must not be empty")
    if len(name) > 64:
        raise ApiKeyError("invalid_name", "name too long (max 64)")
    ttl_seconds = max(60, min(int(ttl_seconds or PAIRING_CODE_DEFAULT_TTL_SECONDS), PAIRING_CODE_MAX_TTL_SECONDS))

    raw_code = _generate_pairing_code()
    pair_id = str(uuid.uuid4())
    code_hash = _hash_key(raw_code)
    code_prefix = raw_code[: len(PAIRING_CODE_PREFIX) + 4]
    expires_at = _utc_after_str(ttl_seconds)

    def _do_insert() -> None:
        with _write_conn(conn) as w:
            w.execute(
                "INSERT INTO plugin_pairing_codes "
                "(pair_id, user_id, agent_name, name, code_hash, code_prefix, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pair_id, user_id, agent_name, name, code_hash, code_prefix, expires_at),
            )

    _retry_locked(_do_insert)
    row = _select_pairing_by_id(conn, pair_id)
    assert row is not None
    return raw_code, row


def claim_pairing_code(
    conn: sqlite3.Connection,
    *,
    code: str,
    agent_name: str = "",
) -> tuple[str, ApiKeyRow]:
    """Exchange a one-time pairing code for a newly minted API key."""
    code = (code or "").strip()
    if not code.startswith(PAIRING_CODE_PREFIX):
        raise ApiKeyError("invalid_code", "invalid pairing code")
    code_hash = _hash_key(code)
    key_id = str(uuid.uuid4())
    raw_key = _generate_raw()
    key_hash = _hash_key(raw_key)
    key_prefix = raw_key[: len(KEY_PREFIX) + 4]
    now = _utc_now_str()
    claimed_row: tuple[Any, ...] | None = None

    def _do_claim() -> None:
        nonlocal claimed_row
        with _write_conn(conn) as w:
            row = w.execute(
                "SELECT pair_id, user_id, agent_name, name, expires_at, claimed_at "
                "FROM plugin_pairing_codes WHERE code_hash = ?",
                (code_hash,),
            ).fetchone()
            if row is None:
                raise ApiKeyError("invalid_code", "invalid pairing code")
            if row[5] is not None:
                raise ApiKeyError("already_claimed", "pairing code was already used")
            if str(row[4]) <= now:
                raise ApiKeyError("expired_code", "pairing code expired")

            user_id = str(row[1])
            resolved_agent = (agent_name or "").strip() or str(row[2])
            key_name = str(row[3])
            active = w.execute(
                "SELECT COUNT(*) FROM api_keys WHERE user_id = ? AND revoked_at IS NULL",
                (user_id,),
            ).fetchone()[0]
            if active >= MAX_KEYS_PER_USER:
                raise ApiKeyError(
                    "quota_exceeded",
                    f"each user can have at most {MAX_KEYS_PER_USER} active keys; revoke one first",
                )

            w.execute(
                "INSERT INTO api_keys "
                "(key_id, user_id, agent_name, name, key_hash, key_prefix) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key_id, user_id, resolved_agent, key_name, key_hash, key_prefix),
            )
            cur = w.execute(
                "UPDATE plugin_pairing_codes "
                "SET claimed_at = ?, claimed_key_id = ? "
                "WHERE pair_id = ? AND claimed_at IS NULL",
                (now, key_id, row[0]),
            )
            if cur.rowcount != 1:
                raise ApiKeyError("already_claimed", "pairing code was already used")
            claimed_row = (key_id, user_id, resolved_agent, key_name, key_prefix, now, None, None)

    _retry_locked(_do_claim)
    if claimed_row is None:
        raise ApiKeyError("invalid_code", "invalid pairing code")
    return raw_key, _row_to_obj(claimed_row)


def lookup(conn: sqlite3.Connection, raw_key: str) -> ApiKeyRow | None:
    """Validate an incoming bearer token. Touches last_used_at on hit.

    The last_used_at UPDATE is best-effort: if another worker holds the
    write lock, we skip the timestamp bump rather than fail the request.
    Authentication doesn't depend on the write succeeding.
    """
    if not raw_key or not raw_key.startswith(KEY_PREFIX):
        return None
    key_hash = _hash_key(raw_key)
    row = conn.execute(
        "SELECT key_id, user_id, agent_name, name, key_prefix, "
        "       created_at, last_used_at, revoked_at "
        "FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL",
        (key_hash,),
    ).fetchone()
    if row is None:
        return None
    try:
        with _write_conn(conn) as w:
            w.execute(
                "UPDATE api_keys SET last_used_at = datetime('now') WHERE key_id = ?",
                (row[0],),
            )
    except sqlite3.OperationalError:
        # Lock contention under heavy multi-worker load — silently skip the
        # last_used_at bump; auth still succeeds.
        pass
    return _row_to_obj(row)


def _select_by_id(conn: sqlite3.Connection, key_id: str) -> ApiKeyRow | None:
    row = conn.execute(
        "SELECT key_id, user_id, agent_name, name, key_prefix, "
        "       created_at, last_used_at, revoked_at "
        "FROM api_keys WHERE key_id = ?",
        (key_id,),
    ).fetchone()
    return _row_to_obj(row) if row else None


def _select_pairing_by_id(conn: sqlite3.Connection, pair_id: str) -> PairingCodeRow | None:
    row = conn.execute(
        "SELECT pair_id, user_id, agent_name, name, code_prefix, "
        "       created_at, expires_at, claimed_at, claimed_key_id "
        "FROM plugin_pairing_codes WHERE pair_id = ?",
        (pair_id,),
    ).fetchone()
    return _pairing_row_to_obj(row) if row else None


def _row_to_obj(row: tuple[Any, ...]) -> ApiKeyRow:
    return ApiKeyRow(
        key_id=row[0],
        user_id=row[1],
        agent_name=row[2],
        name=row[3],
        key_prefix=row[4],
        created_at=row[5],
        last_used_at=row[6],
        revoked_at=row[7],
    )


def _pairing_row_to_obj(row: tuple[Any, ...]) -> PairingCodeRow:
    return PairingCodeRow(
        pair_id=row[0],
        user_id=row[1],
        agent_name=row[2],
        name=row[3],
        code_prefix=row[4],
        created_at=row[5],
        expires_at=row[6],
        claimed_at=row[7],
        claimed_key_id=row[8],
    )
