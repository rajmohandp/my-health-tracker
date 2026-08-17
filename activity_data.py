"""Sample activity generation and reusable activity calculations."""

from dataclasses import dataclass
from datetime import date, timedelta
from random import Random


DAILY_STEP_GOAL = 10_000


@dataclass(frozen=True)
class ActivityRecord:
    """Activity measurements for one day."""

    day: date
    steps: int
    active_minutes: int
    calories: float | None = None
    distance: float | None = None
    resting_heart_rate: int | None = None


@dataclass(frozen=True)
class HeartRateRecord:
    """Resting heart-rate measurement for one day."""

    day: date
    resting_bpm: int


def generate_activity_data(days: int = 90, seed: int = 42) -> list[ActivityRecord]:
    """Generate deterministic local activity history ending today."""
    random = Random(seed)
    start = date.today() - timedelta(days=days - 1)
    records = [
        ActivityRecord(
            day=start + timedelta(days=index),
            steps=random.randint(5_500, 12_500),
            active_minutes=random.randint(20, 75),
            calories=round(random.uniform(1_800, 2_700)),
            distance=round(random.uniform(2.5, 6.5), 2),
            resting_heart_rate=random.randint(58, 72),
        )
        for index in range(days)
    ]
    records[-1] = ActivityRecord(
        day=date.today(),
        steps=8_450,
        active_minutes=42,
        calories=2_150,
        distance=4.1,
        resting_heart_rate=64,
    )
    return records


def filter_recent_days(
    records: list[ActivityRecord], days: int
) -> list[ActivityRecord]:
    """Return the most recent requested number of records."""
    return records[-days:]


def average_steps(records: list[ActivityRecord]) -> int:
    """Calculate rounded average daily steps for a collection of records."""
    return round(sum(record.steps for record in records) / len(records))


def calculate_activity_metrics(records: list[ActivityRecord]) -> dict[str, int]:
    """Calculate the values displayed in the Activity metric cards."""
    return {
        "today_steps": records[-1].steps,
        "seven_day_average": average_steps(filter_recent_days(records, 7)),
        "monthly_average": average_steps(filter_recent_days(records, 30)),
        "active_minutes_today": records[-1].active_minutes,
    }


def moving_average(values: list[int], window: int = 7) -> list[float]:
    """Calculate a rolling average, using available values at the beginning."""
    averages = []
    for index in range(len(values)):
        current_window = values[max(0, index - window + 1) : index + 1]
        averages.append(round(sum(current_window) / len(current_window), 1))
    return averages


def goal_achievement(
    records: list[ActivityRecord], goal: int = DAILY_STEP_GOAL
) -> list[bool]:
    """Return whether the step goal was achieved for each record."""
    return [record.steps >= goal for record in records]


def step_goal_summary(
    records: list[ActivityRecord], goal: int = DAILY_STEP_GOAL
) -> dict[str, float | int]:
    """Calculate achieved days and percentage for a daily step goal."""
    achieved_days = sum(goal_achievement(records, goal))
    return {
        "achieved_days": achieved_days,
        "total_days": len(records),
        "achievement_rate": achieved_days / len(records) if records else 0.0,
    }


ACTIVITY_DATA = generate_activity_data()
