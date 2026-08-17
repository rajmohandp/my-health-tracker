"""Aggregated health context and Groq-powered wellness observations."""

import json
import os
from typing import Any

from groq import Groq

from activity_data import ActivityRecord, step_goal_summary
from analytics import (
    activity_extremes,
    health_correlations,
    week_over_week_steps_change,
    weight_change,
)
from sleep_data import SleepRecord, sleep_goal_summary
from weight_data import WeightRecord


GROQ_MODEL = "llama-3.3-70b-versatile"

COACH_SYSTEM_PROMPT = """You are a wellness trends assistant for My Health Tracker.
Use only the aggregated statistics supplied in the HEALTH SUMMARY. Focus on trends,
patterns, and neutral wellness observations. Never diagnose a disease, prescribe or
recommend medication, claim to be a doctor, or imply that your response replaces
professional care. Clearly label observations as observations, not medical advice.
Mention uncertainty and limited data when relevant. If the user asks for diagnosis,
treatment, medication, or urgent help, decline that part and encourage them to contact
an appropriate qualified healthcare professional. Keep answers concise and practical.
"""


class MissingGroqApiKey(RuntimeError):
    """Raised when Groq functionality is requested without configuration."""


def mean(values: list[float | int]) -> float | None:
    """Calculate an average when values are available."""
    return sum(values) / len(values) if values else None


def build_health_summary(
    activity_records: list[ActivityRecord],
    sleep_records: list[SleepRecord],
    weight_records: list[WeightRecord],
    steps_goal: int,
    sleep_goal: float,
) -> dict[str, Any]:
    """Build aggregate-only context; no daily records are included."""
    best_activity, lowest_activity = activity_extremes(activity_records)
    correlations = health_correlations(activity_records, sleep_records, weight_records)
    step_summary = step_goal_summary(activity_records, steps_goal)
    sleep_summary = sleep_goal_summary(sleep_records, sleep_goal)

    return {
        "period": {
            "start": min(
                records[0].day
                for records in (activity_records, sleep_records, weight_records)
                if records
            ).isoformat(),
            "end": max(
                records[-1].day
                for records in (activity_records, sleep_records, weight_records)
                if records
            ).isoformat(),
        },
        "activity": {
            "days": len(activity_records),
            "average_steps": round(mean([record.steps for record in activity_records]) or 0),
            "average_active_minutes": round(
                mean([record.active_minutes for record in activity_records]) or 0, 1
            ),
            "week_over_week_steps_change_percent": week_over_week_steps_change(
                activity_records
            ),
            "best_day_steps": best_activity.steps if best_activity else None,
            "lowest_day_steps": lowest_activity.steps if lowest_activity else None,
            "steps_goal": steps_goal,
            "goal_days_achieved": step_summary["achieved_days"],
            "goal_achievement_percent": round(
                float(step_summary["achievement_rate"]) * 100, 1
            ),
        },
        "sleep": {
            "nights": len(sleep_records),
            "average_duration_hours": round(
                mean([record.duration_hours for record in sleep_records]) or 0, 2
            ),
            "average_deep_sleep_hours": round(
                mean([record.deep_sleep_hours for record in sleep_records]) or 0, 2
            ),
            "average_rem_sleep_hours": round(
                mean([record.rem_sleep_hours for record in sleep_records]) or 0, 2
            ),
            "average_light_sleep_hours": round(
                mean([record.light_sleep_hours for record in sleep_records]) or 0, 2
            ),
            "sleep_goal_hours": sleep_goal,
            "goal_nights_achieved": sleep_summary["achieved_nights"],
            "goal_achievement_percent": round(
                float(sleep_summary["achievement_rate"]) * 100, 1
            ),
        },
        "weight": {
            "measurements": len(weight_records),
            "starting_weight_lb": weight_records[0].weight_lb if weight_records else None,
            "current_weight_lb": weight_records[-1].weight_lb if weight_records else None,
            "change_lb": weight_change(weight_records),
            "average_weight_lb": round(
                mean([record.weight_lb for record in weight_records]) or 0, 1
            ),
        },
        "correlations": {
            key: round(value, 3) if value is not None else None
            for key, value in correlations.items()
        },
    }


def weekly_summary_text(summary: dict[str, Any]) -> str:
    """Create an automatic factual weekly summary without an API call."""
    activity = summary["activity"]
    sleep = summary["sleep"]
    weight = summary["weight"]
    weight_change_value = weight["change_lb"]
    weight_text = (
        "not enough measurements to calculate weight change"
        if weight_change_value is None
        else f"weight changed {weight_change_value:+.1f} lb"
    )
    return (
        f"Average activity was **{activity['average_steps']:,} steps/day**, with the "
        f"step goal reached on **{activity['goal_achievement_percent']:.0f}% of days**. "
        f"Average sleep was **{sleep['average_duration_hours']:.2f} hours/night**, with "
        f"the sleep goal reached on **{sleep['goal_achievement_percent']:.0f}% of nights**. "
        f"Across available measurements, {weight_text}."
    )


def groq_is_configured() -> bool:
    """Return whether the Groq API key environment variable is present."""
    return bool(os.environ.get("GROQ_API_KEY"))


def ask_health_coach(
    question: str,
    summary: dict[str, Any],
    conversation: list[dict[str, str]] | None = None,
) -> str:
    """Ask Groq using aggregate-only health context and limited chat history."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise MissingGroqApiKey("GROQ_API_KEY is not configured.")

    messages = [
        {"role": "system", "content": COACH_SYSTEM_PROMPT},
        {
            "role": "system",
            "content": "HEALTH SUMMARY (aggregated statistics only):\n"
            + json.dumps(summary, indent=2),
        },
    ]
    if conversation:
        messages.extend(conversation[-6:])
    messages.append({"role": "user", "content": question})

    completion = Groq(api_key=api_key, timeout=30).chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        temperature=0.3,
    )
    return completion.choices[0].message.content or "No response was returned."
