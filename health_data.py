"""CSV parsing and validation for uploaded health data."""

from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, BinaryIO

import pandas as pd

from activity_data import ActivityRecord
from sleep_data import SleepRecord
from weight_data import WeightRecord


ACTIVITY_COLUMNS = ["date", "steps", "active_minutes", "calories", "distance"]
SLEEP_COLUMNS = [
    "date",
    "sleep_duration_hours",
    "bedtime",
    "wake_time",
    "deep_sleep_minutes",
    "rem_sleep_minutes",
    "light_sleep_minutes",
]
WEIGHT_COLUMNS = ["date", "weight"]


@dataclass
class ParseResult:
    """Validated records plus user-facing validation feedback."""

    records: list[Any] | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return self.records is not None and not self.errors


def read_csv(uploaded_file: BinaryIO) -> tuple[pd.DataFrame | None, str | None]:
    """Read an uploaded CSV without depending on its current stream position."""
    try:
        content = uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else uploaded_file.read()
        frame = pd.read_csv(BytesIO(content))
    except (OSError, UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as error:
        return None, f"Could not read this CSV: {error}"
    return frame, None


def validate_columns(frame: pd.DataFrame, required: list[str]) -> list[str]:
    """Return required columns that are absent from a dataframe."""
    return [column for column in required if column not in frame.columns]


def empty_data_error(frame: pd.DataFrame) -> ParseResult | None:
    """Return a validation error when a CSV has headers but no data rows."""
    if frame.empty:
        return ParseResult(errors=["The CSV contains no data rows."])
    return None


def invalid_row_numbers(mask: pd.Series) -> str:
    """Format invalid dataframe indexes as human-friendly CSV row numbers."""
    rows = [str(index + 2) for index in mask[mask].index[:8]]
    suffix = "…" if int(mask.sum()) > 8 else ""
    return ", ".join(rows) + suffix


def finish_records(records: list[Any]) -> ParseResult:
    """Sort records, resolve duplicate dates, and return validation feedback."""
    by_date = {record.day: record for record in records}
    warnings = []
    if len(by_date) < len(records):
        warnings.append("Duplicate dates were found; the last row for each date was used.")
    return ParseResult(records=sorted(by_date.values(), key=lambda record: record.day), warnings=warnings)


def parse_activity_csv(uploaded_file: BinaryIO) -> ParseResult:
    """Validate and convert an Activity CSV into activity records."""
    frame, read_error = read_csv(uploaded_file)
    if read_error:
        return ParseResult(errors=[read_error])
    missing = validate_columns(frame, ACTIVITY_COLUMNS)
    if missing:
        return ParseResult(errors=[f"Missing required columns: {', '.join(missing)}."])
    if error := empty_data_error(frame):
        return error

    dates = pd.to_datetime(frame["date"], errors="coerce")
    numeric_columns = ["steps", "active_minutes", "calories", "distance"]
    numeric = frame[numeric_columns].apply(pd.to_numeric, errors="coerce")
    invalid = dates.isna() | numeric.isna().any(axis=1) | (numeric < 0).any(axis=1)
    if invalid.any():
        return ParseResult(errors=[f"Invalid or missing activity values on CSV row(s): {invalid_row_numbers(invalid)}."])

    records = [
        ActivityRecord(
            day=dates.iloc[index].date(),
            steps=int(numeric.iloc[index]["steps"]),
            active_minutes=int(numeric.iloc[index]["active_minutes"]),
            calories=float(numeric.iloc[index]["calories"]),
            distance=float(numeric.iloc[index]["distance"]),
        )
        for index in range(len(frame))
    ]
    return finish_records(records)


def parse_sleep_csv(uploaded_file: BinaryIO) -> ParseResult:
    """Validate and convert a Sleep CSV into sleep records."""
    frame, read_error = read_csv(uploaded_file)
    if read_error:
        return ParseResult(errors=[read_error])
    missing = validate_columns(frame, SLEEP_COLUMNS)
    if missing:
        return ParseResult(errors=[f"Missing required columns: {', '.join(missing)}."])
    if error := empty_data_error(frame):
        return error

    dates = pd.to_datetime(frame["date"], errors="coerce")
    bedtimes = pd.to_datetime(frame["bedtime"], format="mixed", errors="coerce")
    wake_times = pd.to_datetime(frame["wake_time"], format="mixed", errors="coerce")
    numeric_columns = [
        "sleep_duration_hours", "deep_sleep_minutes", "rem_sleep_minutes", "light_sleep_minutes"
    ]
    numeric = frame[numeric_columns].apply(pd.to_numeric, errors="coerce")
    invalid = (
        dates.isna() | bedtimes.isna() | wake_times.isna() | numeric.isna().any(axis=1)
        | (numeric < 0).any(axis=1) | (numeric["sleep_duration_hours"] <= 0)
    )
    if invalid.any():
        return ParseResult(errors=[f"Invalid or missing sleep values on CSV row(s): {invalid_row_numbers(invalid)}."])

    records = [
        SleepRecord(
            day=dates.iloc[index].date(),
            bedtime=bedtimes.iloc[index].time(),
            wake_time=wake_times.iloc[index].time(),
            duration_hours=float(numeric.iloc[index]["sleep_duration_hours"]),
            deep_sleep_hours=float(numeric.iloc[index]["deep_sleep_minutes"]) / 60,
            rem_sleep_hours=float(numeric.iloc[index]["rem_sleep_minutes"]) / 60,
            light_sleep_hours=float(numeric.iloc[index]["light_sleep_minutes"]) / 60,
        )
        for index in range(len(frame))
    ]
    return finish_records(records)


def parse_weight_csv(uploaded_file: BinaryIO) -> ParseResult:
    """Validate and convert a Weight CSV into weight records."""
    frame, read_error = read_csv(uploaded_file)
    if read_error:
        return ParseResult(errors=[read_error])
    missing = validate_columns(frame, WEIGHT_COLUMNS)
    if missing:
        return ParseResult(errors=[f"Missing required columns: {', '.join(missing)}."])
    if error := empty_data_error(frame):
        return error

    dates = pd.to_datetime(frame["date"], errors="coerce")
    weights = pd.to_numeric(frame["weight"], errors="coerce")
    invalid = dates.isna() | weights.isna() | (weights <= 0)
    if invalid.any():
        return ParseResult(errors=[f"Invalid or missing weight values on CSV row(s): {invalid_row_numbers(invalid)}."])

    records = [
        WeightRecord(day=dates.iloc[index].date(), weight_lb=float(weights.iloc[index]))
        for index in range(len(frame))
    ]
    return finish_records(records)
