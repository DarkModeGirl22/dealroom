"""One private copy of the demo per visitor (a cookie), deleted after 24 hours.

So judges never see each other's rooms, and a visitor can always start a story again.
"""

from __future__ import annotations

import re
import secrets
import threading
import time

from . import db

KEEP_SECONDS = 24 * 3600
_conns: dict[str, tuple] = {}
_guard = threading.Lock()


def new_token() -> str:
    return secrets.token_urlsafe(12)


def valid(token: str | None) -> bool:
    return bool(token and re.fullmatch(r"[A-Za-z0-9_-]{8,40}", token))


def path(token: str):
    return db.DATA_DIR / "visitors" / f"{token}.sqlite3"


def get(token: str):
    """(connection, lock) for this visitor, opened once and reused."""
    with _guard:
        if token not in _conns:
            _conns[token] = (db.connect(path(token)), threading.RLock())
        return _conns[token]


def exists(token: str) -> bool:
    return valid(token) and path(token).exists()


def cleanup():
    """Delete copies older than 24 hours (called when a new visitor arrives)."""
    folder = db.DATA_DIR / "visitors"
    if not folder.exists():
        return
    cutoff = time.time() - KEEP_SECONDS
    for f in folder.glob("*.sqlite3*"):
        if f.stat().st_mtime < cutoff:
            tok = f.name.split(".")[0]
            with _guard:
                c = _conns.pop(tok, None)
            if c:
                c[0].close()
            f.unlink(missing_ok=True)
