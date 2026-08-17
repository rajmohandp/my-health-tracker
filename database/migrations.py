"""Versioned SQLite schema migrations."""

from pathlib import Path

from database.connection import database_connection


SCHEMA_VERSION = 1


MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    steps INTEGER NOT NULL CHECK (steps >= 0),
    active_minutes INTEGER NOT NULL CHECK (active_minutes >= 0),
    calories REAL,
    distance REAL,
    source TEXT NOT NULL,
    external_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, source)
);

CREATE TABLE IF NOT EXISTS sleep (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    bedtime TEXT NOT NULL,
    wake_time TEXT NOT NULL,
    sleep_duration_hours REAL NOT NULL CHECK (sleep_duration_hours > 0),
    deep_sleep_minutes REAL NOT NULL CHECK (deep_sleep_minutes >= 0),
    rem_sleep_minutes REAL NOT NULL CHECK (rem_sleep_minutes >= 0),
    light_sleep_minutes REAL NOT NULL CHECK (light_sleep_minutes >= 0),
    source TEXT NOT NULL,
    external_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, source)
);

CREATE TABLE IF NOT EXISTS weight (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    weight_lb REAL NOT NULL CHECK (weight_lb > 0),
    source TEXT NOT NULL,
    external_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, source)
);

CREATE TABLE IF NOT EXISTS heart_rate (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    resting_bpm INTEGER NOT NULL CHECK (resting_bpm > 0),
    source TEXT NOT NULL,
    external_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, source)
);

CREATE TABLE IF NOT EXISTS health_goals (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    steps_goal INTEGER NOT NULL CHECK (steps_goal > 0),
    sleep_goal_hours REAL NOT NULL CHECK (sleep_goal_hours > 0),
    target_weight_lb REAL,
    target_weight_enabled INTEGER NOT NULL DEFAULT 0 CHECK (target_weight_enabled IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS data_sync_history (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCESS', 'PARTIAL', 'FAILED')),
    range_start TEXT,
    range_end TEXT,
    activity_records INTEGER NOT NULL DEFAULT 0,
    sleep_records INTEGER NOT NULL DEFAULT 0,
    weight_records INTEGER NOT NULL DEFAULT 0,
    heart_rate_records INTEGER NOT NULL DEFAULT 0,
    sanitized_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_activity_date ON activity(date);
CREATE INDEX IF NOT EXISTS idx_sleep_date ON sleep(date);
CREATE INDEX IF NOT EXISTS idx_weight_date ON weight(date);
CREATE INDEX IF NOT EXISTS idx_heart_rate_date ON heart_rate(date);
CREATE INDEX IF NOT EXISTS idx_sync_source_started ON data_sync_history(source, started_at DESC);
"""


def initialize_database(path: str | Path | None = None) -> None:
    """Apply all outstanding database migrations."""
    with database_connection(path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        applied = {
            row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations")
        }
        if 1 not in applied:
            connection.executescript(MIGRATION_1)
            connection.execute(
                "INSERT INTO schema_migrations(version) VALUES (?)", (SCHEMA_VERSION,)
            )
