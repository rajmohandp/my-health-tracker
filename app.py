"""My Health Tracker Streamlit application."""

import hashlib
from datetime import datetime

import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv


load_dotenv()

from ai_coach import (
    ask_health_coach,
    build_health_summary,
    groq_is_configured,
    weekly_summary_text,
)
from analytics import (
    activity_extremes,
    average_sleep_change,
    health_correlations,
    month_over_month_steps_change,
    sleep_goal_percentage,
    step_goal_percentage,
    week_over_week_steps_change,
    weight_change as analytics_weight_change,
)
from activity_data import (
    ACTIVITY_DATA,
    calculate_activity_metrics,
    goal_achievement,
    moving_average,
    step_goal_summary,
)
from date_filters import (
    DATE_RANGE_OPTIONS,
    date_bounds,
    filter_records_by_date,
    records_through_date,
)
from database.repository import HealthGoals, HealthRepository
from google_health_integration import (
    GoogleHealthClient,
    GoogleHealthConfigurationError,
    oauth_state,
    valid_oauth_state,
)
from health_data import parse_activity_csv, parse_sleep_csv, parse_weight_csv
from services.health_sync_service import HealthSyncService
from sleep_data import (
    SLEEP_DATA,
    bedtime_hour,
    calculate_sleep_metrics,
    format_clock_time,
    rolling_average as sleep_rolling_average,
    sleep_goal_summary,
)
from weight_data import (
    WEIGHT_DATA,
    calculate_weight_metrics,
    moving_average as weight_moving_average,
    target_progress,
)


def persist_uploaded_csv(
    uploaded_file, parser, sync_service, category: str, label: str
):
    """Validate and persist an uploaded CSV, returning whether import succeeded."""
    if uploaded_file is None:
        return False
    digest = hashlib.sha256(uploaded_file.getvalue()).hexdigest()
    digest_key = f"{category}_csv_import_digest"
    if st.session_state.get(digest_key) == digest:
        return True
    result = parser(uploaded_file)
    if not result.is_valid:
        for message in result.errors:
            st.error(f"{label}: {message}")
        return False

    for message in result.warnings:
        st.warning(f"{label}: {message}")
    sync_service.import_records(category, result.records)
    st.session_state[digest_key] = digest
    st.success(f"{label}: saved {len(result.records)} CSV rows")
    return True


def persisted_or_demo(repository, category: str, demo_records, label: str):
    """Prefer persisted Google Health records, then CSV records, then demo data."""
    getter = getattr(repository, f"get_{category}")
    sources = repository.available_sources(category)
    for source, description in (
        ("google_health", "stored Google Health"),
        ("csv", "stored CSV"),
    ):
        if source in sources:
            records = getter(source)
            if records:
                st.success(f"{label}: using {len(records)} {description} records")
                return records
    st.caption(f"{label}: using demo data")
    return demo_records


def safe_google_health_error(error: Exception, client: GoogleHealthClient) -> str:
    """Return useful OAuth/API feedback without exposing configured secrets."""
    message = str(error) or error.__class__.__name__
    for sensitive_value in (
        client.config.client_secret,
        client.config.client_id,
    ):
        if sensitive_value:
            message = message.replace(sensitive_value, "[redacted]")
    return message[:500]


def line_chart(
    dates: list,
    values: list[float] | list[int],
    *,
    title: str,
    y_axis_title: str,
    color: str,
) -> go.Figure:
    """Build a consistently styled health trend chart."""
    figure = go.Figure(
        go.Scatter(
            x=dates,
            y=values,
            mode="lines+markers",
            line={"color": color, "width": 3},
            marker={"size": 7},
            hovertemplate=f"%{{x|%b %d}}<br>%{{y}} {y_axis_title.lower()}<extra></extra>",
        )
    )
    figure.update_layout(
        title={"text": title, "font": {"size": 20}},
        xaxis_title="Date",
        yaxis_title=y_axis_title,
        margin={"l": 20, "r": 20, "t": 55, "b": 20},
        height=350,
        hovermode="x unified",
    )
    figure.update_xaxes(showgrid=False)
    figure.update_yaxes(gridcolor="rgba(128, 128, 128, 0.15)")
    return figure


st.set_page_config(
    page_title="My Health Tracker",
    page_icon="🩺",
    layout="wide",
)

health_repository = HealthRepository()
health_sync_service = HealthSyncService(health_repository)
stored_goals = health_repository.get_goals()

st.session_state.setdefault("steps_goal", stored_goals.steps_goal)
st.session_state.setdefault("sleep_goal", stored_goals.sleep_goal_hours)
st.session_state.setdefault("weight_goal_enabled", stored_goals.target_weight_enabled)
st.session_state.setdefault("target_weight", stored_goals.target_weight_lb)
st.session_state.setdefault("coach_messages", [])

with st.sidebar:
    st.title("🩺 My Health Tracker")
    st.write("Track your activity, sleep, weight, and overall health trends.")
    st.divider()
    st.header("Health Data")
    st.caption("Google Health is primary. CSV uploads and demo data remain fallbacks.")

    st.subheader("Google Health")
    google_health_client = None
    try:
        google_health_client = GoogleHealthClient.from_environment()
    except GoogleHealthConfigurationError:
        st.caption(
            "Set GOOGLE_HEALTH_CLIENT_ID, GOOGLE_HEALTH_CLIENT_SECRET, and "
            "GOOGLE_HEALTH_REDIRECT_URI to enable Google Health."
        )

    if google_health_client:
        oauth_error = st.query_params.get("error")
        authorization_code = st.query_params.get("code")
        received_state = st.query_params.get("state")
        if oauth_error:
            st.error(f"Google authorization was not completed: {oauth_error}")
            st.query_params.clear()
        elif authorization_code:
            expected_state = st.session_state.get("google_health_oauth_state")
            if not valid_oauth_state(
                received_state,
                expected_state,
                google_health_client.config.client_secret,
            ):
                st.error("Google authorization state did not match. Please reconnect.")
                st.query_params.clear()
            else:
                try:
                    with st.spinner("Connecting Google Health..."):
                        google_health_client.exchange_code(
                            authorization_code, received_state
                        )
                    st.query_params.clear()
                    st.session_state.pop("google_health_oauth_state", None)
                except Exception as error:
                    st.query_params.clear()
                    st.error(
                        "Google authorization or secure token storage failed: "
                        f"{safe_google_health_error(error, google_health_client)}"
                    )
                else:
                    try:
                        with st.spinner("Syncing Google Health data..."):
                            health_sync_service.sync_google_health(
                                google_health_client
                            )
                    except Exception as error:
                        st.error(
                            "Google connected, but data sync failed: "
                            f"{safe_google_health_error(error, google_health_client)}"
                        )
                    else:
                        st.rerun()

        try:
            google_health_connected = google_health_client.is_connected()
        except Exception:
            google_health_connected = False
            st.error("The operating system credential store is unavailable.")

        if google_health_connected:
            st.success("Google Health connected")
            sync_column, disconnect_column = st.columns(2)
            if sync_column.button("Sync Health", width="stretch"):
                try:
                    with st.spinner("Retrieving Google Health history..."):
                        health_sync_service.sync_google_health(
                            google_health_client
                        )
                    st.rerun()
                except Exception as error:
                    st.error(
                        "Google Health sync failed: "
                        f"{safe_google_health_error(error, google_health_client)}"
                    )
            if disconnect_column.button("Disconnect", width="stretch"):
                try:
                    google_health_client.disconnect()
                finally:
                    st.session_state.pop("google_health_oauth_state", None)
                st.rerun()
            if last_sync := health_repository.last_successful_sync("google_health"):
                completed_at = datetime.fromisoformat(last_sync["completed_at"])
                st.caption(f"Last synced: {completed_at:%b %d, %Y %I:%M %p}")
        else:
            st.session_state.google_health_oauth_state = oauth_state(
                google_health_client.config.client_secret
            )
            st.link_button(
                "Connect Google Health",
                google_health_client.authorization_url(
                    st.session_state.google_health_oauth_state
                ),
                width="stretch",
            )

    st.subheader("CSV fallback")

    activity_upload = st.file_uploader("Activity CSV", type="csv", key="activity_csv")
    with st.expander("Expected Activity CSV format"):
        st.code(
            "date,steps,active_minutes,calories,distance\n"
            "2026-08-15,8450,42,2150,4.1",
            language="csv",
        )

    sleep_upload = st.file_uploader("Sleep CSV", type="csv", key="sleep_csv")
    with st.expander("Expected Sleep CSV format"):
        st.code(
            "date,sleep_duration_hours,bedtime,wake_time,deep_sleep_minutes,rem_sleep_minutes,light_sleep_minutes\n"
            "2026-08-15,6.75,23:15,06:00,75,87,243",
            language="csv",
        )

    weight_upload = st.file_uploader("Weight CSV", type="csv", key="weight_csv")
    with st.expander("Expected Weight CSV format"):
        st.code("date,weight\n2026-08-15,160.0", language="csv")

    persist_uploaded_csv(
        activity_upload, parse_activity_csv, health_sync_service, "activity", "Activity"
    )
    persist_uploaded_csv(
        sleep_upload, parse_sleep_csv, health_sync_service, "sleep", "Sleep"
    )
    persist_uploaded_csv(
        weight_upload, parse_weight_csv, health_sync_service, "weight", "Weight"
    )

    activity_records = persisted_or_demo(
        health_repository, "activity", ACTIVITY_DATA, "Activity"
    )
    sleep_records = persisted_or_demo(
        health_repository, "sleep", SLEEP_DATA, "Sleep"
    )
    weight_records = persisted_or_demo(
        health_repository, "weight", WEIGHT_DATA, "Weight"
    )
    activity_history = activity_records
    sleep_history = sleep_records

    st.divider()
    st.header("Health Goals")
    st.number_input(
        "Daily Steps Goal",
        min_value=1_000,
        max_value=100_000,
        step=500,
        key="steps_goal",
    )
    st.number_input(
        "Daily Sleep Goal (hours)",
        min_value=1.0,
        max_value=12.0,
        step=0.25,
        key="sleep_goal",
    )
    st.checkbox("Set a Target Weight", key="weight_goal_enabled")
    if st.session_state.weight_goal_enabled:
        st.number_input(
            "Target Weight (lb)",
            min_value=50.0,
            max_value=500.0,
            step=0.5,
            key="target_weight",
        )
    selected_goals = HealthGoals(
        steps_goal=st.session_state.steps_goal,
        sleep_goal_hours=st.session_state.sleep_goal,
        target_weight_lb=st.session_state.target_weight,
        target_weight_enabled=st.session_state.weight_goal_enabled,
    )
    if selected_goals != stored_goals:
        health_repository.save_goals(selected_goals)

    st.divider()
    st.header("Historical Filters")
    date_range = st.selectbox(
        "Date range",
        DATE_RANGE_OPTIONS,
        index=1,
        key="common_date_range",
    )
    custom_start = None
    custom_end = None
    if date_range == "Custom Date Range":
        all_dates = [
            record.day
            for records in (activity_records, sleep_records, weight_records)
            for record in records
        ]
        earliest_date, latest_date = min(all_dates), max(all_dates)
        custom_start = st.date_input(
            "Start date", value=earliest_date, min_value=earliest_date,
            max_value=latest_date, key="custom_start_date"
        )
        custom_end = st.date_input(
            "End date", value=latest_date, min_value=earliest_date,
            max_value=latest_date, key="custom_end_date"
        )
        if custom_start > custom_end:
            st.warning("Start date is after end date; the dates were automatically reordered.")

    activity_records = filter_records_by_date(
        activity_records, date_range, custom_start, custom_end
    )
    sleep_records = filter_records_by_date(
        sleep_records, date_range, custom_start, custom_end
    )
    weight_records = filter_records_by_date(
        weight_records, date_range, custom_start, custom_end
    )

    empty_sources = [
        label
        for label, records in (
            ("Activity", activity_records),
            ("Sleep", sleep_records),
            ("Weight", weight_records),
        )
        if not records
    ]
    if empty_sources:
        st.warning(
            f"No {', '.join(empty_sources)} data exists in this date range. "
            "Choose a range containing data."
        )
        st.stop()

    filtered_start, filtered_end = date_bounds(
        date_range,
        max(
            records[-1].day
            for records in (activity_records, sleep_records, weight_records)
            if records
        ),
        custom_start,
        custom_end,
    )
    st.caption(f"Showing {filtered_start:%b %d, %Y} – {filtered_end:%b %d, %Y}")

st.title("My Health Tracker")
st.caption("A simple home for your personal health journey.")

dashboard_tab, activity_tab, sleep_tab, weight_tab, coach_tab = st.tabs(
    ["Dashboard", "Activity", "Sleep", "Weight", "AI Health Coach"]
)

with dashboard_tab:
    st.header("Dashboard")

    steps_card, sleep_card, weight_card, heart_rate_card = st.columns(4)
    steps_card.metric("Today's Steps", f"{activity_records[-1].steps:,}")
    sleep_card.metric("Last Night Sleep", f"{sleep_records[-1].duration_hours:.2f} hr")
    weight_card.metric("Current Weight", f"{weight_records[-1].weight_lb:.1f} lb")
    latest_resting_heart_rate = next(
        (
            record.resting_heart_rate
            for record in reversed(activity_records)
            if record.resting_heart_rate is not None
        ),
        None,
    )
    heart_rate_card.metric(
        "Resting Heart Rate",
        "Not available"
        if latest_resting_heart_rate is None
        else f"{latest_resting_heart_rate} bpm",
    )

    activity_goal = step_goal_summary(activity_records, st.session_state.steps_goal)
    sleep_goal = sleep_goal_summary(sleep_records, st.session_state.sleep_goal)
    st.subheader("Goal Progress")
    steps_progress, sleep_progress, weight_progress = st.columns(3)
    with steps_progress:
        st.metric("Steps Goal Achieved", f"{activity_goal['achievement_rate']:.0%}")
        st.progress(activity_goal["achievement_rate"])
        st.caption(
            f"{activity_goal['achieved_days']} of {activity_goal['total_days']} days"
        )
    with sleep_progress:
        st.metric("Sleep Goal Achieved", f"{sleep_goal['achievement_rate']:.0%}")
        st.progress(sleep_goal["achievement_rate"])
        st.caption(
            f"{sleep_goal['achieved_nights']} of {sleep_goal['total_nights']} nights"
        )
    with weight_progress:
        if st.session_state.weight_goal_enabled:
            dashboard_weight_progress = target_progress(
                weight_records[0].weight_lb,
                weight_records[-1].weight_lb,
                st.session_state.target_weight,
            )
            st.metric("Weight Goal Progress", f"{dashboard_weight_progress:.0%}")
            st.progress(dashboard_weight_progress)
            st.caption(f"Target: {st.session_state.target_weight:.1f} lb")
        else:
            st.metric("Weight Goal Progress", "Not set")
            st.caption("Set a target weight in the sidebar.")

    st.subheader("Health Overview")

    steps_chart, sleep_chart = st.columns(2)
    with steps_chart:
        st.plotly_chart(
            line_chart(
                [record.day for record in activity_records],
                [record.steps for record in activity_records],
                title=f"Daily Steps — {date_range}",
                y_axis_title="Steps",
                color="#14B8A6",
            ),
            width="stretch",
            config={"displayModeBar": False},
        )

    with sleep_chart:
        st.plotly_chart(
            line_chart(
                [record.day for record in sleep_records],
                [record.duration_hours for record in sleep_records],
                title=f"Sleep Duration — {date_range}",
                y_axis_title="Hours",
                color="#6366F1",
            ),
            width="stretch",
            config={"displayModeBar": False},
        )

    st.plotly_chart(
        line_chart(
            [record.day for record in weight_records],
            [record.weight_lb for record in weight_records],
            title=f"Weight Trend — {date_range}",
            y_axis_title="Weight (lb)",
            color="#F59E0B",
        ),
        width="stretch",
        config={"displayModeBar": False},
    )

    st.subheader("Health Insights")
    activity_comparison_history = records_through_date(
        activity_history, activity_records[-1].day
    )
    sleep_comparison_history = records_through_date(
        sleep_history, sleep_records[-1].day
    )
    weekly_steps_change = week_over_week_steps_change(activity_comparison_history)
    monthly_steps_change = month_over_month_steps_change(activity_comparison_history)
    sleep_change = average_sleep_change(sleep_comparison_history)
    selected_weight_change = analytics_weight_change(weight_records)
    best_activity, lowest_activity = activity_extremes(activity_records)
    correlations = health_correlations(
        activity_records, sleep_records, weight_records
    )

    week_change_card, month_change_card, sleep_change_card, weight_change_card = st.columns(4)
    week_change_card.metric(
        "Week-over-Week Steps",
        "N/A" if weekly_steps_change is None else f"{weekly_steps_change:+.1f}%",
    )
    month_change_card.metric(
        "Month-over-Month Steps",
        "N/A" if monthly_steps_change is None else f"{monthly_steps_change:+.1f}%",
    )
    sleep_change_card.metric(
        "Average Sleep Change",
        "N/A" if sleep_change is None else f"{sleep_change:+.2f} hr",
    )
    weight_change_card.metric(
        "Weight Change",
        "N/A" if selected_weight_change is None else f"{selected_weight_change:+.1f} lb",
    )

    best_day_card, lowest_day_card, steps_goal_card, sleep_goal_card = st.columns(4)
    best_day_card.metric(
        "Best Activity Day",
        "N/A" if best_activity is None else f"{best_activity.steps:,} steps",
    )
    if best_activity:
        best_day_card.caption(best_activity.day.strftime("%b %d, %Y"))
    lowest_day_card.metric(
        "Lowest Activity Day",
        "N/A" if lowest_activity is None else f"{lowest_activity.steps:,} steps",
    )
    if lowest_activity:
        lowest_day_card.caption(lowest_activity.day.strftime("%b %d, %Y"))
    steps_goal_card.metric(
        "Days Reaching Steps Goal",
        f"{step_goal_percentage(activity_records, st.session_state.steps_goal):.0f}%",
    )
    sleep_goal_card.metric(
        "Days Reaching Sleep Goal",
        f"{sleep_goal_percentage(sleep_records, st.session_state.sleep_goal):.0f}%",
    )

    st.markdown("##### Correlations")
    steps_sleep_card, steps_weight_card, sleep_weight_card = st.columns(3)
    for card, label, key in (
        (steps_sleep_card, "Steps and Sleep", "steps_sleep"),
        (steps_weight_card, "Steps and Weight", "steps_weight"),
        (sleep_weight_card, "Sleep and Weight", "sleep_weight"),
    ):
        value = correlations[key]
        card.metric(label, "N/A" if value is None else f"{value:+.2f}")
    st.info(
        "Correlation describes how two measurements move together; correlation "
        "does not imply causation."
    )

with activity_tab:
    st.header("Activity")
    activity_metrics = calculate_activity_metrics(activity_records)
    activity_goal = step_goal_summary(activity_records, st.session_state.steps_goal)

    today_steps, seven_day_average, monthly_average, active_minutes = st.columns(4)
    today_steps.metric("Today's Steps", f"{activity_metrics['today_steps']:,}")
    seven_day_average.metric(
        "7-Day Average Steps", f"{activity_metrics['seven_day_average']:,}"
    )
    monthly_average.metric(
        "Monthly Average Steps", f"{activity_metrics['monthly_average']:,}"
    )
    active_minutes.metric(
        "Active Minutes Today", f"{activity_metrics['active_minutes_today']} min"
    )

    achieved_days_card, achievement_rate_card = st.columns(2)
    achieved_days_card.metric(
        "Days Step Goal Achieved",
        f"{activity_goal['achieved_days']} of {activity_goal['total_days']}",
    )
    achievement_rate_card.metric(
        "Steps Goal Achievement", f"{activity_goal['achievement_rate']:.0%}"
    )
    st.progress(activity_goal["achievement_rate"])

    dates = [record.day for record in activity_records]
    steps = [record.steps for record in activity_records]
    active_minutes_values = [record.active_minutes for record in activity_records]
    seven_day_averages = moving_average(steps)
    achieved_goal = goal_achievement(activity_records, st.session_state.steps_goal)

    daily_steps_figure = line_chart(
        dates,
        steps,
        title="Daily Steps",
        y_axis_title="Steps",
        color="#14B8A6",
    )
    daily_steps_figure.add_hline(
        y=st.session_state.steps_goal,
        line_dash="dash",
        line_color="#64748B",
        annotation_text=f"{st.session_state.steps_goal:,} step goal",
    )

    moving_average_figure = line_chart(
        dates,
        seven_day_averages,
        title="7-Day Moving Average",
        y_axis_title="Average Steps",
        color="#6366F1",
    )

    active_minutes_figure = go.Figure(
        go.Bar(x=dates, y=active_minutes_values, marker_color="#0EA5E9")
    )
    active_minutes_figure.update_layout(
        title="Active Minutes by Day",
        xaxis_title="Date",
        yaxis_title="Active Minutes",
        height=350,
        margin={"l": 20, "r": 20, "t": 55, "b": 20},
    )

    goal_figure = go.Figure(
        go.Bar(
            x=dates,
            y=steps,
            marker_color=["#22C55E" if achieved else "#CBD5E1" for achieved in achieved_goal],
            customdata=["Achieved" if achieved else "Not achieved" for achieved in achieved_goal],
            hovertemplate="%{x|%b %d}<br>%{y:,} steps<br>%{customdata}<extra></extra>",
        )
    )
    goal_figure.add_hline(
        y=st.session_state.steps_goal,
        line_dash="dash",
        line_color="#64748B",
        annotation_text=f"{st.session_state.steps_goal:,} step goal",
    )
    goal_figure.update_layout(
        title="Steps Goal Achievement",
        xaxis_title="Date",
        yaxis_title="Steps",
        height=350,
        margin={"l": 20, "r": 20, "t": 55, "b": 20},
    )

    chart_config = {"displayModeBar": False}
    steps_column, average_column = st.columns(2)
    with steps_column:
        st.plotly_chart(daily_steps_figure, width="stretch", config=chart_config)
    with average_column:
        st.plotly_chart(moving_average_figure, width="stretch", config=chart_config)

    minutes_column, goal_column = st.columns(2)
    with minutes_column:
        st.plotly_chart(active_minutes_figure, width="stretch", config=chart_config)
    with goal_column:
        st.plotly_chart(goal_figure, width="stretch", config=chart_config)

with sleep_tab:
    st.header("Sleep")
    sleep_target = st.session_state.sleep_goal
    sleep_metrics = calculate_sleep_metrics(sleep_records)
    sleep_goal = sleep_goal_summary(sleep_records, sleep_target)

    last_night, average_sleep, average_bedtime, average_wake_time = st.columns(4)
    last_night.metric("Last Night Sleep", sleep_metrics["last_night"])
    average_sleep.metric("7-Day Average Sleep", sleep_metrics["seven_day_average"])
    average_bedtime.metric("Average Bedtime", sleep_metrics["average_bedtime"])
    average_wake_time.metric("Average Wake Time", sleep_metrics["average_wake_time"])

    achieved_nights_card, sleep_rate_card = st.columns(2)
    achieved_nights_card.metric(
        "Nights Sleep Goal Achieved",
        f"{sleep_goal['achieved_nights']} of {sleep_goal['total_nights']}",
    )
    sleep_rate_card.metric(
        "Sleep Goal Achievement", f"{sleep_goal['achievement_rate']:.0%}"
    )
    st.progress(sleep_goal["achievement_rate"])

    sleep_dates = [record.day for record in sleep_records]
    durations = [record.duration_hours for record in sleep_records]
    below_target = [duration < sleep_target for duration in durations]

    duration_figure = go.Figure(
        go.Bar(
            x=sleep_dates,
            y=durations,
            marker_color=["#F87171" if below else "#14B8A6" for below in below_target],
            customdata=["Below target" if below else "Target met" for below in below_target],
            hovertemplate="%{x|%b %d}<br>%{y:.2f} hours<br>%{customdata}<extra></extra>",
        )
    )
    duration_figure.add_hline(
        y=sleep_target,
        line_dash="dash",
        line_color="#64748B",
        annotation_text=f"{sleep_target:g}-hour target",
    )
    duration_figure.update_layout(
        title="Sleep Duration by Day",
        xaxis_title="Date",
        yaxis_title="Sleep Duration (hours)",
        height=350,
        margin={"l": 20, "r": 20, "t": 55, "b": 20},
    )

    average_figure = line_chart(
        sleep_dates,
        sleep_rolling_average(durations),
        title="7-Day Average Sleep",
        y_axis_title="Sleep Duration (hours)",
        color="#6366F1",
    )
    average_figure.add_hline(
        y=sleep_target,
        line_dash="dash",
        line_color="#64748B",
        annotation_text=f"{sleep_target:g}-hour target",
    )

    stage_figure = go.Figure()
    for stage_name, values, color in [
        ("Deep Sleep", [record.deep_sleep_hours for record in sleep_records], "#312E81"),
        ("REM Sleep", [record.rem_sleep_hours for record in sleep_records], "#7C3AED"),
        ("Light Sleep", [record.light_sleep_hours for record in sleep_records], "#A5B4FC"),
    ]:
        stage_figure.add_bar(name=stage_name, x=sleep_dates, y=values, marker_color=color)
    stage_figure.update_layout(
        barmode="stack",
        title="Sleep-Stage Distribution",
        xaxis_title="Date",
        yaxis_title="Duration (hours)",
        height=350,
        margin={"l": 20, "r": 20, "t": 55, "b": 20},
        legend_title_text="Sleep Stage",
    )

    bedtime_values = [bedtime_hour(record.bedtime) for record in sleep_records]
    bedtime_labels = [format_clock_time(record.bedtime) for record in sleep_records]
    bedtime_figure = go.Figure(
        go.Scatter(
            x=sleep_dates,
            y=bedtime_values,
            mode="lines+markers",
            line={"color": "#F59E0B", "width": 3},
            customdata=bedtime_labels,
            hovertemplate="%{x|%b %d}<br>Bedtime: %{customdata}<extra></extra>",
        )
    )
    bedtime_figure.update_layout(
        title="Bedtime Trend",
        xaxis_title="Date",
        yaxis_title="Bedtime",
        height=350,
        margin={"l": 20, "r": 20, "t": 55, "b": 20},
    )
    bedtime_figure.update_yaxes(
        tickmode="array",
        tickvals=[22, 23, 24, 25],
        ticktext=["10 PM", "11 PM", "12 AM", "1 AM"],
    )

    sleep_chart_config = {"displayModeBar": False}
    duration_column, average_column = st.columns(2)
    with duration_column:
        st.plotly_chart(duration_figure, width="stretch", config=sleep_chart_config)
    with average_column:
        st.plotly_chart(average_figure, width="stretch", config=sleep_chart_config)

    stages_column, bedtime_column = st.columns(2)
    with stages_column:
        st.plotly_chart(stage_figure, width="stretch", config=sleep_chart_config)
    with bedtime_column:
        st.plotly_chart(bedtime_figure, width="stretch", config=sleep_chart_config)

with weight_tab:
    st.header("Weight")

    weight_metrics = calculate_weight_metrics(weight_records)
    current_weight, starting_weight, weight_change, average_weight = st.columns(4)
    current_weight.metric("Current Weight", f"{weight_metrics['current_weight']:.1f} lb")
    starting_weight.metric("Starting Weight", f"{weight_metrics['starting_weight']:.1f} lb")
    weight_change.metric(
        "Weight Change",
        f"{weight_metrics['weight_change']:+.1f} lb",
    )
    average_weight.metric(
        "30-Day Average", f"{weight_metrics['thirty_day_average']:.1f} lb"
    )

    if st.session_state.weight_goal_enabled:
        target_weight = st.session_state.target_weight
        progress = target_progress(
            weight_metrics["starting_weight"],
            weight_metrics["current_weight"],
            target_weight,
        )
        st.write(f"**Target progress: {progress:.0%}**")
        st.progress(progress)
        remaining = abs(weight_metrics["current_weight"] - target_weight)
        if progress >= 1:
            st.success("Target reached.")
        else:
            st.caption(f"{remaining:.1f} lb remaining to reach {target_weight:.1f} lb.")

    weight_dates = [record.day for record in weight_records]
    weights = [record.weight_lb for record in weight_records]

    daily_weight_figure = line_chart(
        weight_dates,
        weights,
        title="Daily Weight",
        y_axis_title="Weight (lb)",
        color="#14B8A6",
    )
    seven_day_weight_figure = line_chart(
        weight_dates,
        weight_moving_average(weights, 7),
        title="7-Day Moving Average",
        y_axis_title="Weight (lb)",
        color="#6366F1",
    )
    thirty_day_weight_figure = line_chart(
        weight_dates,
        weight_moving_average(weights, 30),
        title="30-Day Moving Average",
        y_axis_title="Weight (lb)",
        color="#F59E0B",
    )

    weight_chart_config = {"displayModeBar": False}
    daily_weight_column, weekly_weight_column = st.columns(2)
    with daily_weight_column:
        st.plotly_chart(daily_weight_figure, width="stretch", config=weight_chart_config)
    with weekly_weight_column:
        st.plotly_chart(
            seven_day_weight_figure, width="stretch", config=weight_chart_config
        )
    st.plotly_chart(
        thirty_day_weight_figure, width="stretch", config=weight_chart_config
    )

with coach_tab:
    st.header("AI Health Coach")
    st.caption(
        "Explore observations from aggregated health statistics. Raw daily records "
        "are not sent to Groq."
    )

    coach_summary = build_health_summary(
        activity_records,
        sleep_records,
        weight_records,
        st.session_state.steps_goal,
        st.session_state.sleep_goal,
    )
    weekly_summary = build_health_summary(
        activity_records[-7:],
        sleep_records[-7:],
        weight_records[-7:],
        st.session_state.steps_goal,
        st.session_state.sleep_goal,
    )

    st.subheader("Weekly Health Summary")
    st.write(weekly_summary_text(weekly_summary))
    st.caption(
        "This is an automated wellness summary of available recent data, not "
        "medical advice."
    )

    st.subheader("Ask the AI Health Coach")
    st.markdown(
        "Try asking: *How was my health this week?*, *Am I becoming more active?*, "
        "or *What patterns do you notice between activity and sleep?*"
    )

    api_ready = groq_is_configured()
    if not api_ready:
        st.warning(
            "AI responses are unavailable until the GROQ_API_KEY environment "
            "variable is configured."
        )

    for message in st.session_state.coach_messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    coach_question = st.chat_input(
        "Ask about your health trends",
        key="coach_question",
        disabled=not api_ready,
    )
    if coach_question:
        prior_messages = st.session_state.coach_messages.copy()
        st.session_state.coach_messages.append(
            {"role": "user", "content": coach_question}
        )
        with st.chat_message("user"):
            st.markdown(coach_question)

        with st.chat_message("assistant"):
            with st.spinner("Reviewing your aggregated health trends..."):
                try:
                    coach_response = ask_health_coach(
                        coach_question,
                        coach_summary,
                        conversation=prior_messages,
                    )
                except Exception:
                    coach_response = (
                        "I couldn't reach the AI service. Check the Groq API key and "
                        "network connection, then try again."
                    )
                st.markdown(coach_response)
        st.session_state.coach_messages.append(
            {"role": "assistant", "content": coach_response}
        )

    st.info(
        "AI responses provide general wellness observations only. They are not a "
        "diagnosis, treatment plan, medication recommendation, or substitute for "
        "advice from a qualified healthcare professional."
    )
