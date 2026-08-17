"""Source-neutral health record persistence and retrieval."""

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path

from activity_data import ActivityRecord, HeartRateRecord
from database.connection import database_connection
from database.migrations import initialize_database
from sleep_data import SleepRecord
from weight_data import WeightRecord


@dataclass(frozen=True)
class HealthGoals:
    steps_goal: int = 10_000
    sleep_goal_hours: float = 7.0
    target_weight_lb: float = 155.0
    target_weight_enabled: bool = False


class HealthRepository:
    """Persist normalized records without exposing SQLite to the UI."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = path
        initialize_database(path)

    def upsert_activity(self, records, source: str) -> int:
        values = [
            (
                record.day.isoformat(), record.steps, record.active_minutes,
                record.calories, record.distance, source,
            )
            for record in records
        ]
        with database_connection(self.path) as connection:
            connection.executemany(
                """
                INSERT INTO activity(date, steps, active_minutes, calories, distance, source)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, source) DO UPDATE SET
                    steps=excluded.steps, active_minutes=excluded.active_minutes,
                    calories=excluded.calories, distance=excluded.distance,
                    updated_at=CURRENT_TIMESTAMP
                """,
                values,
            )
            heart_values = [
                (record.day.isoformat(), record.resting_heart_rate, source)
                for record in records
                if record.resting_heart_rate is not None
            ]
            connection.executemany(
                """
                INSERT INTO heart_rate(date, resting_bpm, source) VALUES (?, ?, ?)
                ON CONFLICT(date, source) DO UPDATE SET
                    resting_bpm=excluded.resting_bpm, updated_at=CURRENT_TIMESTAMP
                """,
                heart_values,
            )
        return len(values)

    def upsert_sleep(self, records, source: str) -> int:
        values = [
            (
                record.day.isoformat(), record.bedtime.isoformat(timespec="minutes"),
                record.wake_time.isoformat(timespec="minutes"), record.duration_hours,
                record.deep_sleep_hours * 60, record.rem_sleep_hours * 60,
                record.light_sleep_hours * 60, source,
            )
            for record in records
        ]
        with database_connection(self.path) as connection:
            connection.executemany(
                """
                INSERT INTO sleep(
                    date, bedtime, wake_time, sleep_duration_hours,
                    deep_sleep_minutes, rem_sleep_minutes, light_sleep_minutes, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, source) DO UPDATE SET
                    bedtime=excluded.bedtime, wake_time=excluded.wake_time,
                    sleep_duration_hours=excluded.sleep_duration_hours,
                    deep_sleep_minutes=excluded.deep_sleep_minutes,
                    rem_sleep_minutes=excluded.rem_sleep_minutes,
                    light_sleep_minutes=excluded.light_sleep_minutes,
                    updated_at=CURRENT_TIMESTAMP
                """,
                values,
            )
        return len(values)

    def upsert_heart_rate(self, records, source: str) -> int:
        values = [
            (record.day.isoformat(), record.resting_bpm, source)
            for record in records
        ]
        with database_connection(self.path) as connection:
            connection.executemany(
                """
                INSERT INTO heart_rate(date, resting_bpm, source) VALUES (?, ?, ?)
                ON CONFLICT(date, source) DO UPDATE SET
                    resting_bpm=excluded.resting_bpm, updated_at=CURRENT_TIMESTAMP
                """,
                values,
            )
        return len(values)

    def upsert_weight(self, records, source: str) -> int:
        values = [(record.day.isoformat(), record.weight_lb, source) for record in records]
        with database_connection(self.path) as connection:
            connection.executemany(
                """
                INSERT INTO weight(date, weight_lb, source) VALUES (?, ?, ?)
                ON CONFLICT(date, source) DO UPDATE SET
                    weight_lb=excluded.weight_lb, updated_at=CURRENT_TIMESTAMP
                """,
                values,
            )
        return len(values)

    def get_activity(self, source: str, start: date | None = None, end: date | None = None):
        clauses, values = ["a.source = ?"], [source]
        if start:
            clauses.append("a.date >= ?")
            values.append(start.isoformat())
        if end:
            clauses.append("a.date <= ?")
            values.append(end.isoformat())
        with database_connection(self.path) as connection:
            rows = connection.execute(
                f"""
                SELECT a.*, h.resting_bpm FROM activity a
                LEFT JOIN heart_rate h ON h.date = a.date AND h.source = a.source
                WHERE {' AND '.join(clauses)} ORDER BY a.date
                """,
                values,
            ).fetchall()
        return [
            ActivityRecord(
                day=date.fromisoformat(row["date"]), steps=row["steps"],
                active_minutes=row["active_minutes"], calories=row["calories"],
                distance=row["distance"], resting_heart_rate=row["resting_bpm"],
            )
            for row in rows
        ]

    def get_sleep(self, source: str, start: date | None = None, end: date | None = None):
        clauses, values = ["source = ?"], [source]
        if start:
            clauses.append("date >= ?")
            values.append(start.isoformat())
        if end:
            clauses.append("date <= ?")
            values.append(end.isoformat())
        with database_connection(self.path) as connection:
            rows = connection.execute(
                f"SELECT * FROM sleep WHERE {' AND '.join(clauses)} ORDER BY date", values
            ).fetchall()
        return [
            SleepRecord(
                day=date.fromisoformat(row["date"]),
                bedtime=time.fromisoformat(row["bedtime"]),
                wake_time=time.fromisoformat(row["wake_time"]),
                duration_hours=row["sleep_duration_hours"],
                deep_sleep_hours=row["deep_sleep_minutes"] / 60,
                rem_sleep_hours=row["rem_sleep_minutes"] / 60,
                light_sleep_hours=row["light_sleep_minutes"] / 60,
            )
            for row in rows
        ]

    def get_weight(self, source: str, start: date | None = None, end: date | None = None):
        clauses, values = ["source = ?"], [source]
        if start:
            clauses.append("date >= ?")
            values.append(start.isoformat())
        if end:
            clauses.append("date <= ?")
            values.append(end.isoformat())
        with database_connection(self.path) as connection:
            rows = connection.execute(
                f"SELECT * FROM weight WHERE {' AND '.join(clauses)} ORDER BY date", values
            ).fetchall()
        return [
            WeightRecord(day=date.fromisoformat(row["date"]), weight_lb=row["weight_lb"])
            for row in rows
        ]

    def get_heart_rate(
        self, source: str, start: date | None = None, end: date | None = None
    ):
        clauses, values = ["source = ?"], [source]
        if start:
            clauses.append("date >= ?")
            values.append(start.isoformat())
        if end:
            clauses.append("date <= ?")
            values.append(end.isoformat())
        with database_connection(self.path) as connection:
            rows = connection.execute(
                f"SELECT * FROM heart_rate WHERE {' AND '.join(clauses)} ORDER BY date",
                values,
            ).fetchall()
        return [
            HeartRateRecord(
                day=date.fromisoformat(row["date"]), resting_bpm=row["resting_bpm"]
            )
            for row in rows
        ]

    def available_sources(self, table: str) -> set[str]:
        if table not in {"activity", "sleep", "weight", "heart_rate"}:
            raise ValueError(f"Unsupported table: {table}")
        with database_connection(self.path) as connection:
            return {row["source"] for row in connection.execute(f"SELECT DISTINCT source FROM {table}")}

    def get_goals(self) -> HealthGoals:
        with database_connection(self.path) as connection:
            row = connection.execute("SELECT * FROM health_goals WHERE id = 1").fetchone()
        if not row:
            return HealthGoals()
        return HealthGoals(
            steps_goal=row["steps_goal"], sleep_goal_hours=row["sleep_goal_hours"],
            target_weight_lb=row["target_weight_lb"] or 155.0,
            target_weight_enabled=bool(row["target_weight_enabled"]),
        )

    def save_goals(self, goals: HealthGoals) -> None:
        with database_connection(self.path) as connection:
            connection.execute(
                """
                INSERT INTO health_goals(
                    id, steps_goal, sleep_goal_hours, target_weight_lb,
                    target_weight_enabled
                ) VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    steps_goal=excluded.steps_goal,
                    sleep_goal_hours=excluded.sleep_goal_hours,
                    target_weight_lb=excluded.target_weight_lb,
                    target_weight_enabled=excluded.target_weight_enabled,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    goals.steps_goal, goals.sleep_goal_hours, goals.target_weight_lb,
                    int(goals.target_weight_enabled),
                ),
            )

    def start_sync(self, source: str, range_start: date, range_end: date) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with database_connection(self.path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO data_sync_history(source, started_at, status, range_start, range_end)
                VALUES (?, ?, 'RUNNING', ?, ?)
                """,
                (source, now, range_start.isoformat(), range_end.isoformat()),
            )
            return int(cursor.lastrowid)

    def finish_sync(self, sync_id: int, status: str, counts=None, error: str | None = None):
        counts = counts or {}
        if status not in {"SUCCESS", "PARTIAL", "FAILED"}:
            raise ValueError(f"Invalid terminal sync status: {status}")
        with database_connection(self.path) as connection:
            connection.execute(
                """
                UPDATE data_sync_history SET completed_at=?, status=?,
                    activity_records=?, sleep_records=?, weight_records=?,
                    heart_rate_records=?, sanitized_error=? WHERE id=?
                """,
                (
                    datetime.now(timezone.utc).isoformat(), status,
                    counts.get("activity", 0), counts.get("sleep", 0),
                    counts.get("weight", 0), counts.get("heart_rate", 0),
                    error[:500] if error else None, sync_id,
                ),
            )

    def last_successful_sync(self, source: str):
        with database_connection(self.path) as connection:
            return connection.execute(
                """
                SELECT * FROM data_sync_history
                WHERE source=? AND status='SUCCESS'
                ORDER BY completed_at DESC LIMIT 1
                """,
                (source,),
            ).fetchone()
