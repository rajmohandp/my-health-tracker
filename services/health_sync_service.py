"""Coordinate external retrieval and durable normalized storage."""

from dataclasses import dataclass
from datetime import date, timedelta

from database.repository import HealthRepository
from google_health_integration import GoogleHealthDataBundle


@dataclass(frozen=True)
class SyncResult:
    sync_id: int
    activity_records: int
    sleep_records: int
    weight_records: int
    heart_rate_records: int


class HealthSyncService:
    """Persist data bundles and record sanitized synchronization outcomes."""

    def __init__(self, repository: HealthRepository) -> None:
        self.repository = repository

    def sync_google_health(self, client, days: int = 365) -> SyncResult:
        end_date = date.today()
        start_date = end_date - timedelta(days=days - 1)
        sync_id = self.repository.start_sync("google_health", start_date, end_date)
        try:
            bundle = client.fetch_historical_data(days=days)
            return self.persist_bundle(sync_id, bundle, "google_health")
        except Exception as error:
            self.repository.finish_sync(
                sync_id, "FAILED", error=f"{error.__class__.__name__}: {error}"
            )
            raise

    def persist_bundle(
        self, sync_id: int, bundle: GoogleHealthDataBundle, source: str
    ) -> SyncResult:
        activity_count = self.repository.upsert_activity(bundle.activity, source)
        sleep_count = self.repository.upsert_sleep(bundle.sleep, source)
        weight_count = self.repository.upsert_weight(bundle.weight, source)
        heart_count = sum(
            record.resting_heart_rate is not None for record in bundle.activity
        )
        counts = {
            "activity": activity_count,
            "sleep": sleep_count,
            "weight": weight_count,
            "heart_rate": heart_count,
        }
        self.repository.finish_sync(sync_id, "SUCCESS", counts)
        return SyncResult(
            sync_id, activity_count, sleep_count, weight_count, heart_count
        )

    def import_records(self, category: str, records) -> int:
        """Persist validated CSV records using category-specific upserts."""
        method = {
            "activity": self.repository.upsert_activity,
            "sleep": self.repository.upsert_sleep,
            "weight": self.repository.upsert_weight,
        }.get(category)
        if method is None:
            raise ValueError(f"Unsupported health category: {category}")
        return method(records, "csv")
