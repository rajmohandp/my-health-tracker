"""SQLite persistence tests using isolated temporary databases."""

from datetime import date, time

from activity_data import ActivityRecord, HeartRateRecord
from database.connection import database_connection
from database.repository import HealthGoals, HealthRepository
from google_health_integration import GoogleHealthDataBundle
from services.health_sync_service import HealthSyncService
from sleep_data import SleepRecord
from weight_data import WeightRecord


def repository(tmp_path):
    return HealthRepository(tmp_path / "health.db")


def test_schema_initialization_is_idempotent(tmp_path):
    database = tmp_path / "health.db"
    HealthRepository(database)
    HealthRepository(database)
    with database_connection(database) as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "activity", "sleep", "weight", "heart_rate", "health_goals",
        "data_sync_history", "schema_migrations",
    } <= tables


def test_upserts_prevent_duplicates_and_return_typed_records(tmp_path):
    repo = repository(tmp_path)
    day = date(2026, 8, 15)
    repo.upsert_activity([ActivityRecord(day, 8000, 40, 2100, 4.0, 65)], "google_health")
    repo.upsert_activity([ActivityRecord(day, 9000, 45, 2200, 4.5, 63)], "google_health")
    repo.upsert_sleep(
        [SleepRecord(day, time(23), time(6), 7, 1.2, 1.5, 4.3)],
        "google_health",
    )
    repo.upsert_weight([WeightRecord(day, 160.0)], "google_health")

    activity = repo.get_activity("google_health")
    assert len(activity) == 1
    assert activity[0].steps == 9000
    assert activity[0].resting_heart_rate == 63
    assert repo.get_sleep("google_health")[0].duration_hours == 7
    assert repo.get_weight("google_health")[0].weight_lb == 160
    repo.upsert_heart_rate([HeartRateRecord(day, 61)], "google_health")
    assert repo.get_heart_rate("google_health")[0].resting_bpm == 61


def test_date_range_queries_and_source_isolation(tmp_path):
    repo = repository(tmp_path)
    records = [
        WeightRecord(date(2026, 1, 1), 165),
        WeightRecord(date(2026, 2, 1), 163),
        WeightRecord(date(2026, 3, 1), 161),
    ]
    repo.upsert_weight(records, "google_health")
    repo.upsert_weight([WeightRecord(date(2026, 2, 1), 170)], "csv")
    selected = repo.get_weight(
        "google_health", date(2026, 2, 1), date(2026, 3, 1)
    )
    assert [record.weight_lb for record in selected] == [163, 161]
    assert repo.get_weight("csv")[0].weight_lb == 170


def test_health_goals_and_sync_history_are_persistent(tmp_path):
    repo = repository(tmp_path)
    goals = HealthGoals(12_000, 8.0, 150.0, True)
    repo.save_goals(goals)
    assert repo.get_goals() == goals

    sync_id = repo.start_sync(
        "google_health", date(2026, 1, 1), date(2026, 1, 31)
    )
    repo.finish_sync(sync_id, "SUCCESS", {"activity": 31, "heart_rate": 30})
    sync = repo.last_successful_sync("google_health")
    assert sync["id"] == sync_id
    assert sync["activity_records"] == 31
    assert sync["heart_rate_records"] == 30


def test_sync_service_persists_normalized_bundle(tmp_path):
    repo = repository(tmp_path)
    day = date.today()
    bundle = GoogleHealthDataBundle(
        activity=[ActivityRecord(day, 10_000, 50, 2300, 5.0, 60)],
        sleep=[SleepRecord(day, time(23), time(6), 7, 1.2, 1.5, 4.3)],
        weight=[WeightRecord(day, 159.5)],
    )

    class FakeGoogleHealthClient:
        calls = 0

        def fetch_historical_data(self, days=365):
            self.calls += 1
            return bundle

    client = FakeGoogleHealthClient()
    result = HealthSyncService(repo).sync_google_health(client)

    assert client.calls == 1
    assert result.activity_records == 1
    assert repo.get_activity("google_health")[0].steps == 10_000
    assert repo.get_sleep("google_health")[0].duration_hours == 7
    assert repo.get_weight("google_health")[0].weight_lb == 159.5
    assert repo.last_successful_sync("google_health")["id"] == result.sync_id
