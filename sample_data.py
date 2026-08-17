"""Hardcoded sample data displayed by the dashboard."""

from datetime import date, timedelta


def recent_dates(days: int) -> list[date]:
    """Return an ascending sequence ending on today's date."""
    today = date.today()
    return [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]


DAILY_STEPS = {
    "dates": recent_dates(7),
    "values": [6_250, 7_100, 8_900, 7_650, 9_300, 10_150, 8_450],
}

SLEEP_DURATION = {
    "dates": recent_dates(7),
    "values": [7.2, 6.8, 7.5, 6.4, 7.0, 7.3, 6.75],
}

WEIGHT_TREND = {
    "dates": recent_dates(30),
    "values": [
        163.2, 163.0, 163.1, 162.8, 162.9, 162.6, 162.5, 162.7, 162.3, 162.4,
        162.0, 162.2, 161.9, 161.8, 161.6, 161.7, 161.4, 161.5, 161.2, 161.0,
        161.1, 160.9, 160.8, 160.6, 160.7, 160.4, 160.3, 160.2, 160.1, 160.0,
    ],
}
