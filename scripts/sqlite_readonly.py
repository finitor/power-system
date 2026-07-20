"""Safe read-only access to SQLite stores that may be live in WAL mode."""

from __future__ import annotations

import os
from pathlib import Path
import pwd
import sqlite3


def require_database_owner(path: str | Path) -> Path:
    """Refuse a SQLite open that could create WAL sidecars as another user.

    SQLite can create or update ``-wal`` and ``-shm`` files even when the URI
    uses ``mode=ro``.  If those files are created by an operator account with
    mode 0644, the service that owns the database can lose write access.
    """
    database = Path(path)
    if not database.exists():
        raise SystemExit(f"Database not found: {database}")

    owner_uid = database.stat().st_uid
    if os.geteuid() == owner_uid:
        return database

    try:
        owner = pwd.getpwuid(owner_uid).pw_name
    except KeyError:
        owner = str(owner_uid)
    raise SystemExit(
        f"Refusing to open {database} as uid {os.geteuid()}: the database is "
        f"owned by {owner} (uid {owner_uid}). Even a read-only WAL connection "
        "can create -wal/-shm files and block the owner from writing. "
        f"Re-run the command as the database owner, for example: sudo -u {owner} ..."
    )


def open_readonly_database(path: str | Path) -> sqlite3.Connection:
    """Open an owner-checked SQLite database with writes disabled."""
    database = require_database_owner(path).resolve()
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection
