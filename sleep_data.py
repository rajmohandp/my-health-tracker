"""Sample sleep generation and reusable sleep calculations."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from random import Random


@dataclass(frozen=True)
class SleepRecord:
    """Sleep measurements for one night."""

    day: date
    bedtime: time
    wake_time: time
    duration_hours: float
    deep_sleep_hours: float
    rem_sleep_hours: float
    light_sleep_hours: float


def generate_sleep_data(days: int = 30, seed: int = 24) -> list[SleepRecord]:
    """Generate deterministic local sleep history ending today."""
    random = Random(seed)
    start = date.today() - timedelta(days=days - 1)
    records = []

    for index in range(days):
        duration = round(random.uniform(5.8, 8.4), 2)
        bedtime_minutes = random.randint(22 * 60, 24 * 60 + 20)
        bedtime = time((bedtime_minutes // 60) % 24, bedtime_minutes % 60)
        bedtime_date = start + timedelta(days=index - 1)
        bedtime_datetime = datetime.combine(bedtime_date, bedtime)
        wake_datetime = bedtime_datetime + timedelta(hours=duration)

        deep = round(duration * random.uniform(0.16, 0.22), 2)
        rem = round(duration * random.uniform(0.20, 0.26), 2)
        light = round(duration - deep - rem, 2)
        records.append(
            SleepRecord(
                day=start + timedelta(days=index),
                bedtime=bedtime,
                wake_time=wake_datetime.time().replace(second=0, microsecond=0),
                duration_hours=duration,
                deep_sleep_hours=deep,
                rem_sleep_hours=rem,
                light_sleep_hours=light,
            )
        )

    records[-1] = SleepRecord(
        day=date.today(),
        bedtime=time(23, 15),
        wake_time=time(6, 0),
        duration_hours=6.75,
        deep_sleep_hours=1.25,
        rem_sleep_hours=1.45,
        light_sleep_hours=4.05,
    )
    return records


def average_duration(records: list[SleepRecord], days: int = 7) -> float:
    """Calculate average sleep duration over the most recent nights."""
    recent = records[-days:]
    return round(sum(record.duration_hours for record in recent) / len(recent), 2)


def time_to_minutes(value: time) -> int:
    """Convert a time to minutes after midnight."""
    return value.hour * 60 + value.minute


def average_clock_time(values: list[time], *, bedtime: bool = False) -> time:
    """Average clock times, accounting for bedtimes that cross midnight."""
    minutes = [time_to_minutes(value) for value in values]
    if bedtime:
        minutes = [minute + 24 * 60 if minute < 12 * 60 else minute for minute in minutes]
    average_minutes = round(sum(minutes) / len(minutes)) % (24 * 60)
    return time(average_minutes // 60, average_minutes % 60)


def format_clock_time(value: time) -> str:
    """Format a time for metric display."""
    return value.strftime("%I:%M %p").lstrip("0")


def calculate_sleep_metrics(records: list[SleepRecord]) -> dict[str, str]:
    """Calculate the values displayed in the Sleep metric cards."""
    recent = records[-7:]
    return {
        "last_night": f"{records[-1].duration_hours:.2f} hr",
        "seven_day_average": f"{average_duration(records):.2f} hr",
        "average_bedtime": format_clock_time(
            average_clock_time([record.bedtime for record in recent], bedtime=True)
        ),
        "average_wake_time": format_clock_time(
            average_clock_time([record.wake_time for record in recent])
        ),
    }


def rolling_average(values: list[float], window: int = 7) -> list[float]:
    """Calculate a rolling average using available nights at the beginning."""
    averages = []
    for index in range(len(values)):
        current_window = values[max(0, index - window + 1) : index + 1]
        averages.append(round(sum(current_window) / len(current_window), 2))
    return averages


def bedtime_hour(value: time) -> float:
    """Convert bedtime to a continuous hour scale spanning midnight."""
    hour = value.hour + value.minute / 60
    return hour + 24 if hour < 12 else hour


def sleep_goal_summary(
    records: list[SleepRecord], target_hours: float
) -> dict[str, float | int]:
    """Calculate achieved nights and percentage for a sleep target."""
    achieved_nights = sum(
        record.duration_hours >= target_hours for record in records
    )
    return {
        "achieved_nights": achieved_nights,
        "total_nights": len(records),
        "achievement_rate": achieved_nights / len(records) if records else 0.0,
    }


SLEEP_DATA = generate_sleep_data()
