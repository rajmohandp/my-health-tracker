"""SQLite connection configuration."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


DEFAULT_DATABASE_PATH = Path("data") / "my_health_tracker.db"


def database_path() -> Path:
    """Return the configured local database path."""
    return Path(os.environ.get("HEALTH_DATABASE_PATH", DEFAULT_DATABASE_PATH))


@contextmanager
def database_connection(path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open a configured SQLite connection and commit or roll back atomically."""
    resolved = Path(path) if path is not None else database_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(resolved, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
