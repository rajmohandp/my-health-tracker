"""Sample weight generation and reusable weight calculations."""

from dataclasses import dataclass
from datetime import date, timedelta
from random import Random


@dataclass(frozen=True)
class WeightRecord:
    """Weight measurement for one day."""

    day: date
    weight_lb: float


def generate_weight_data(days: int = 90, seed: int = 18) -> list[WeightRecord]:
    """Generate deterministic daily weight data with a gradual downward trend."""
    random = Random(seed)
    start_day = date.today() - timedelta(days=days - 1)
    starting_weight = 165.0
    records = []

    for index in range(days):
        trend = -5.0 * index / (days - 1)
        variation = random.uniform(-0.45, 0.45)
        records.append(
            WeightRecord(
                day=start_day + timedelta(days=index),
                weight_lb=round(starting_weight + trend + variation, 1),
            )
        )

    records[0] = WeightRecord(day=start_day, weight_lb=starting_weight)
    records[-1] = WeightRecord(day=date.today(), weight_lb=160.0)
    return records


def average_weight(records: list[WeightRecord], days: int) -> float:
    """Calculate average weight over the most recent requested days."""
    recent = records[-days:]
    return round(sum(record.weight_lb for record in recent) / len(recent), 1)


def calculate_weight_metrics(records: list[WeightRecord]) -> dict[str, float]:
    """Calculate values displayed in the Weight metric cards."""
    current = records[-1].weight_lb
    starting = records[0].weight_lb
    return {
        "current_weight": current,
        "starting_weight": starting,
        "weight_change": round(current - starting, 1),
        "thirty_day_average": average_weight(records, 30),
    }


def moving_average(values: list[float], window: int) -> list[float]:
    """Calculate a rolling weight average using available early values."""
    averages = []
    for index in range(len(values)):
        current_window = values[max(0, index - window + 1) : index + 1]
        averages.append(round(sum(current_window) / len(current_window), 2))
    return averages


def target_progress(starting_weight: float, current_weight: float, target_weight: float) -> float:
    """Return progress from starting weight toward a target, bounded from 0 to 1."""
    total_change_needed = target_weight - starting_weight
    if total_change_needed == 0:
        return 1.0

    progress = (current_weight - starting_weight) / total_change_needed
    return max(0.0, min(1.0, progress))


WEIGHT_DATA = generate_weight_data()
