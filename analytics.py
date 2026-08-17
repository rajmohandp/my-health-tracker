"""Reusable cross-domain health analytics calculations."""

from math import sqrt
from typing import Any

from activity_data import ActivityRecord, step_goal_summary
from sleep_data import SleepRecord, sleep_goal_summary
from weight_data import WeightRecord


def average(values: list[float | int]) -> float | None:
    """Return the arithmetic mean, or None when no values are available."""
    return sum(values) / len(values) if values else None


def percentage_change(current: float, previous: float) -> float | None:
    """Calculate percentage change while safely handling a zero baseline."""
    if previous == 0:
        return None
    return (current - previous) / previous * 100


def period_over_period_steps_change(
    records: list[ActivityRecord], period_days: int
) -> float | None:
    """Compare average steps in the latest period with the preceding period."""
    if len(records) < period_days * 2:
        return None
    current = average([record.steps for record in records[-period_days:]])
    previous = average([record.steps for record in records[-period_days * 2 : -period_days]])
    return percentage_change(current, previous)


def week_over_week_steps_change(records: list[ActivityRecord]) -> float | None:
    """Calculate week-over-week average steps percentage change."""
    return period_over_period_steps_change(records, 7)


def month_over_month_steps_change(records: list[ActivityRecord]) -> float | None:
    """Calculate 30-day-over-30-day average steps percentage change."""
    return period_over_period_steps_change(records, 30)


def average_sleep_change(records: list[SleepRecord]) -> float | None:
    """Calculate latest 7-night average sleep minus the preceding 7 nights."""
    if len(records) < 14:
        return None
    current = average([record.duration_hours for record in records[-7:]])
    previous = average([record.duration_hours for record in records[-14:-7]])
    return current - previous


def weight_change(records: list[WeightRecord]) -> float | None:
    """Calculate weight change from the first to last record."""
    if len(records) < 2:
        return None
    return records[-1].weight_lb - records[0].weight_lb


def activity_extremes(
    records: list[ActivityRecord],
) -> tuple[ActivityRecord | None, ActivityRecord | None]:
    """Return the highest-step and lowest-step activity records."""
    if not records:
        return None, None
    return max(records, key=lambda record: record.steps), min(
        records, key=lambda record: record.steps
    )


def step_goal_percentage(records: list[ActivityRecord], goal: int) -> float:
    """Return the percentage of activity days meeting the step goal."""
    return float(step_goal_summary(records, goal)["achievement_rate"]) * 100


def sleep_goal_percentage(records: list[SleepRecord], goal: float) -> float:
    """Return the percentage of sleep days meeting the sleep goal."""
    return float(sleep_goal_summary(records, goal)["achievement_rate"]) * 100


def pearson_correlation(pairs: list[tuple[float, float]]) -> float | None:
    """Calculate Pearson correlation for paired values."""
    if len(pairs) < 2:
        return None
    x_values, y_values = zip(*pairs)
    x_mean, y_mean = average(list(x_values)), average(list(y_values))
    x_delta = [value - x_mean for value in x_values]
    y_delta = [value - y_mean for value in y_values]
    denominator = sqrt(sum(value**2 for value in x_delta) * sum(value**2 for value in y_delta))
    if denominator == 0:
        return None
    return sum(x * y for x, y in zip(x_delta, y_delta)) / denominator


def correlation_by_date(
    left_records: list[Any],
    right_records: list[Any],
    left_attribute: str,
    right_attribute: str,
) -> float | None:
    """Correlate two measurements using only dates present in both datasets."""
    left_by_date = {record.day: getattr(record, left_attribute) for record in left_records}
    right_by_date = {record.day: getattr(record, right_attribute) for record in right_records}
    shared_dates = sorted(left_by_date.keys() & right_by_date.keys())
    pairs = [(left_by_date[day], right_by_date[day]) for day in shared_dates]
    return pearson_correlation(pairs)


def health_correlations(
    activity_records: list[ActivityRecord],
    sleep_records: list[SleepRecord],
    weight_records: list[WeightRecord],
) -> dict[str, float | None]:
    """Calculate all requested date-aligned health correlations."""
    return {
        "steps_sleep": correlation_by_date(
            activity_records, sleep_records, "steps", "duration_hours"
        ),
        "steps_weight": correlation_by_date(
            activity_records, weight_records, "steps", "weight_lb"
        ),
        "sleep_weight": correlation_by_date(
            sleep_records, weight_records, "duration_hours", "weight_lb"
        ),
    }
