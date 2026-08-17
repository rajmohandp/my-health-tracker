"""Reusable date-range calculations for health records."""

from datetime import date, timedelta
from typing import Any


DATE_RANGE_OPTIONS = [
    "Last 7 Days",
    "Last 30 Days",
    "Last 90 Days",
    "Last 1 Year",
    "Custom Date Range",
]

PRESET_DAYS = {
    "Last 7 Days": 7,
    "Last 30 Days": 30,
    "Last 90 Days": 90,
    "Last 1 Year": 365,
}


def date_bounds(
    selection: str,
    reference_date: date,
    custom_start: date | None = None,
    custom_end: date | None = None,
) -> tuple[date, date]:
    """Resolve a date-filter selection into inclusive start and end dates."""
    if selection in PRESET_DAYS:
        return reference_date - timedelta(days=PRESET_DAYS[selection] - 1), reference_date
    if selection == "Custom Date Range" and custom_start and custom_end:
        return min(custom_start, custom_end), max(custom_start, custom_end)
    raise ValueError(f"Unsupported or incomplete date range: {selection}")


def filter_records_by_date(
    records: list[Any],
    selection: str,
    custom_start: date | None = None,
    custom_end: date | None = None,
) -> list[Any]:
    """Filter records with a ``day`` field using a preset or custom range."""
    if not records:
        return []
    start_date, end_date = date_bounds(
        selection,
        reference_date=max(record.day for record in records),
        custom_start=custom_start,
        custom_end=custom_end,
    )
    return [record for record in records if start_date <= record.day <= end_date]


def records_through_date(records: list[Any], end_date: date) -> list[Any]:
    """Return records up to an inclusive endpoint for historical comparisons."""
    return [record for record in records if record.day <= end_date]
