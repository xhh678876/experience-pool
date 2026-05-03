"""Identity + ACL.

Two concerns kept separate:

1. **Identity**: agents authenticate with an HMAC-signed credential stored at
   ~/.experience-pool/credentials/<agent_name>.json. This is good enough for
   single-tenant local deployments. For multi-tenant we'd swap to JWT signed
   by an enterprise SSO; the call-site interface is the same.

2. **ACL**: each experience has acl in {private, team:<dept>, public/org}.
   - private  : only the originating agent can read
   - team:<X> : agents on team X can read
   - public   : everyone can read (`org` is kept as a legacy alias)
   `can_read(viewer_agent, experience_row)` returns bool.

A pool method `pool.search_with_acl(agent_name, query, ...)` filters the
search results through this check. The original `pool.search` is kept so
unit tests don't have to set up agents.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

def _default_credentials_dir() -> Path:
    """If EXP_CREDENTIALS_DIR is set, honor it. Otherwise derive from EXP_ROOT
    (so the server and CLI agree when both run with the same EXP_ROOT)."""
    explicit = os.getenv("EXP_CREDENTIALS_DIR")
    if explicit:
        return Path(explicit)
    root = os.getenv("EXP_ROOT")
    if root:
        return Path(root) / "credentials"
    return Path.home() / ".experience-pool" / "credentials"


def credentials_dir() -> Path:
    """Re-resolve every call so tests / runtime env changes are honored."""
    return _default_credentials_dir()


# Backward-compat module-level constant (snapshot at import time).
CREDENTIALS_DIR = _default_credentials_dir()


@dataclass
class Credential:
    agent_id: str
    agent_name: str
    team: str
    secret: str  # 32-byte hex, used to sign requests in the FastAPI gateway

    def to_dict(self) -> dict[str, str]:
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "team": self.team,
            "secret": self.secret,
        }


def issue_credential(agent_id: str, agent_name: str, team: str) -> Credential:
    credentials_dir().mkdir(parents=True, exist_ok=True)
    secret = secrets.token_hex(32)
    cred = Credential(agent_id=agent_id, agent_name=agent_name, team=team, secret=secret)
    path = credentials_dir() / f"{agent_name}.json"
    path.write_text(json.dumps(cred.to_dict(), indent=2))
    os.chmod(path, 0o600)
    return cred


def load_credential(agent_name: str) -> Credential | None:
    path = credentials_dir() / f"{agent_name}.json"
    if not path.exists():
        return None
    d = json.loads(path.read_text())
    return Credential(**d)


def sign_request(cred: Credential, method: str, path: str, body: bytes) -> str:
    """HMAC-SHA256 over the canonical request string."""
    canonical = b"\n".join([method.upper().encode(), path.encode(), body])
    return hmac.new(cred.secret.encode(), canonical, hashlib.sha256).hexdigest()


def verify_signature(secret: str, method: str, path: str, body: bytes, signature: str) -> bool:
    canonical = b"\n".join([method.upper().encode(), path.encode(), body])
    expected = hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def parse_acl(acl: str) -> tuple[str, str | None]:
    """Returns (kind, team). kind in {'private', 'team', 'public'}.

    `org` is accepted as a legacy alias for `public` so existing pools remain
    readable while the MVP-facing terminology stays private/team/public.
    """
    if acl == "private":
        return ("private", None)
    if acl in {"public", "org"}:
        return ("public", None)
    if acl.startswith("team:"):
        return ("team", acl.split(":", 1)[1])
    # Unknown → treat as private to fail closed.
    return ("private", None)


def can_read(viewer_agent_id: str, viewer_team: str, owner_agent_id: str, acl: str) -> bool:
    kind, team = parse_acl(acl)
    if kind == "public":
        return True
    if kind == "private":
        return viewer_agent_id == owner_agent_id
    if kind == "team":
        return team == viewer_team
    return False


def filter_visible_rows(
    viewer_agent_id: str, viewer_team: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        if can_read(viewer_agent_id, viewer_team, row.get("agent_id", ""), row.get("acl", "private")):
            out.append(row)
    return out
