"""Google Health OAuth, historical retrieval, and record normalization."""

import json
import base64
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
import keyring
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from activity_data import ActivityRecord
from sleep_data import SleepRecord
from weight_data import WeightRecord


GOOGLE_HEALTH_API_URL = "https://health.googleapis.com"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_HEALTH_SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
]
TOKEN_SERVICE = "my-health-tracker-google-health"
PENDING_AUTH_SUFFIX = ":pending-oauth"
ACCESS_TOKEN_SUFFIX = ":access-token"
REFRESH_TOKEN_SUFFIX = ":refresh-token"
TOKEN_METADATA_SUFFIX = ":token-metadata"
GRAMS_PER_POUND = 453.59237
MILLIMETERS_PER_MILE = 1_609_344


class GoogleHealthError(RuntimeError):
    """Base exception for Google Health configuration, OAuth, and API failures."""


class GoogleHealthConfigurationError(GoogleHealthError):
    """Raised when required Google Health environment variables are absent."""


@dataclass(frozen=True)
class GoogleHealthConfig:
    """Google Health OAuth application configuration."""

    client_id: str
    client_secret: str
    redirect_uri: str

    @classmethod
    def from_environment(cls) -> "GoogleHealthConfig":
        client_id = os.environ.get("GOOGLE_HEALTH_CLIENT_ID") or os.environ.get(
            "FITBIT_CLIENT_ID"
        )
        client_secret = os.environ.get(
            "GOOGLE_HEALTH_CLIENT_SECRET"
        ) or os.environ.get("FITBIT_CLIENT_SECRET")
        redirect_uri = os.environ.get(
            "GOOGLE_HEALTH_REDIRECT_URI"
        ) or os.environ.get("FITBIT_REDIRECT_URI")
        missing = [
            name
            for name, value in (
                ("GOOGLE_HEALTH_CLIENT_ID", client_id),
                ("GOOGLE_HEALTH_CLIENT_SECRET", client_secret),
                ("GOOGLE_HEALTH_REDIRECT_URI", redirect_uri),
            )
            if not value
        ]
        if missing:
            raise GoogleHealthConfigurationError(
                f"Missing environment variables: {', '.join(missing)}"
            )
        return cls(client_id, client_secret, redirect_uri)

    def oauth_client_config(self) -> dict[str, Any]:
        """Return Google OAuth client-library configuration."""
        return {
            "web": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_uri": GOOGLE_AUTH_URL,
                "token_uri": GOOGLE_TOKEN_URL,
                "redirect_uris": [self.redirect_uri],
            }
        }


class KeyringTokenStore:
    """Store OAuth tokens in the operating system credential vault."""

    def __init__(self, client_id: str) -> None:
        self.client_id = client_id

    def load(self) -> dict[str, Any] | None:
        serialized = keyring.get_password(
            TOKEN_SERVICE, self.client_id + TOKEN_METADATA_SUFFIX
        )
        if not serialized:
            return None
        token = json.loads(serialized)
        token["access_token"] = keyring.get_password(
            TOKEN_SERVICE, self.client_id + ACCESS_TOKEN_SUFFIX
        )
        token["refresh_token"] = keyring.get_password(
            TOKEN_SERVICE, self.client_id + REFRESH_TOKEN_SUFFIX
        )
        return token if token["access_token"] and token["refresh_token"] else None

    def save(self, token: dict[str, Any]) -> None:
        access_token = token.get("access_token")
        refresh_token = token.get("refresh_token")
        if not access_token or not refresh_token:
            raise GoogleHealthError("Google returned incomplete OAuth credentials.")
        metadata = {
            key: value
            for key, value in token.items()
            if key not in {"access_token", "refresh_token"}
        }
        # Windows Credential Manager has a small per-entry payload limit. Google
        # access tokens can be large, so keep each secret in a separate entry.
        keyring.set_password(
            TOKEN_SERVICE, self.client_id + ACCESS_TOKEN_SUFFIX, access_token
        )
        keyring.set_password(
            TOKEN_SERVICE, self.client_id + REFRESH_TOKEN_SUFFIX, refresh_token
        )
        keyring.set_password(
            TOKEN_SERVICE,
            self.client_id + TOKEN_METADATA_SUFFIX,
            json.dumps(metadata),
        )

    def delete(self) -> None:
        for username in (
            self.client_id,
            self.client_id + ACCESS_TOKEN_SUFFIX,
            self.client_id + REFRESH_TOKEN_SUFFIX,
            self.client_id + TOKEN_METADATA_SUFFIX,
        ):
            try:
                keyring.delete_password(TOKEN_SERVICE, username)
            except keyring.errors.PasswordDeleteError:
                pass

    def load_pending(self) -> dict[str, Any] | None:
        serialized = keyring.get_password(
            TOKEN_SERVICE, self.client_id + PENDING_AUTH_SUFFIX
        )
        return json.loads(serialized) if serialized else None

    def save_pending(self, pending: dict[str, Any]) -> None:
        keyring.set_password(
            TOKEN_SERVICE,
            self.client_id + PENDING_AUTH_SUFFIX,
            json.dumps(pending),
        )

    def delete_pending(self) -> None:
        try:
            keyring.delete_password(
                TOKEN_SERVICE, self.client_id + PENDING_AUTH_SUFFIX
            )
        except keyring.errors.PasswordDeleteError:
            pass


@dataclass(frozen=True)
class GoogleHealthDataBundle:
    """Google Health data normalized to application record types."""

    activity: list[ActivityRecord]
    sleep: list[SleepRecord]
    weight: list[WeightRecord]


def oauth_state(signing_secret: str) -> str:
    """Create a short-lived, signed OAuth state that survives a new browser tab."""
    payload = f"{int(time.time())}.{secrets.token_urlsafe(24)}"
    signature = hmac.new(
        signing_secret.encode(), payload.encode(), hashlib.sha256
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{payload}.{encoded_signature}"


def valid_oauth_state(
    received: str | None,
    expected: str | None,
    signing_secret: str,
    max_age_seconds: int = 600,
) -> bool:
    """Validate session state or a signed state with a bounded lifetime."""
    if not received:
        return False
    if expected and secrets.compare_digest(received, expected):
        return True
    try:
        timestamp_text, nonce, encoded_signature = received.split(".", 2)
        timestamp = int(timestamp_text)
        payload = f"{timestamp_text}.{nonce}"
        expected_signature = base64.urlsafe_b64encode(
            hmac.new(
                signing_secret.encode(), payload.encode(), hashlib.sha256
            ).digest()
        ).decode().rstrip("=")
    except (TypeError, ValueError):
        return False
    age = int(time.time()) - timestamp
    return 0 <= age <= max_age_seconds and secrets.compare_digest(
        encoded_signature, expected_signature
    )


def civil_date(value: dict[str, Any]) -> date:
    return date(int(value["year"]), int(value["month"]), int(value["day"]))


def civil_datetime(value: dict[str, Any]) -> datetime:
    date_value = value["date"]
    time_value = value.get("time", {})
    return datetime(
        int(date_value["year"]), int(date_value["month"]), int(date_value["day"]),
        int(time_value.get("hours", 0)), int(time_value.get("minutes", 0)),
        int(time_value.get("seconds", 0)),
    )


class GoogleHealthClient:
    """Google OAuth client with refresh and normalized Health API retrieval."""

    def __init__(self, config, token_store=None, http_client=None) -> None:
        self.config = config
        self.token_store = token_store or KeyringTokenStore(config.client_id)
        self.http = http_client or httpx.Client(timeout=30)

    @classmethod
    def from_environment(cls) -> "GoogleHealthClient":
        return cls(GoogleHealthConfig.from_environment())

    def _flow(
        self, state: str | None = None, code_verifier: str | None = None
    ) -> Flow:
        return Flow.from_client_config(
            self.config.oauth_client_config(), scopes=GOOGLE_HEALTH_SCOPES,
            state=state, redirect_uri=self.config.redirect_uri,
            code_verifier=code_verifier,
            autogenerate_code_verifier=code_verifier is None,
        )

    def authorization_url(self, state: str) -> str:
        code_verifier = secrets.token_urlsafe(64)
        self.token_store.save_pending(
            {
                "state": state,
                "code_verifier": code_verifier,
                "created_at": int(time.time()),
            }
        )
        url, _ = self._flow(state, code_verifier).authorization_url(
            access_type="offline", include_granted_scopes="true", prompt="consent"
        )
        return url

    @staticmethod
    def _token_from_credentials(credentials: Credentials) -> dict[str, Any]:
        expiry = credentials.expiry
        if expiry and expiry.tzinfo is not None:
            expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)
        return {
            "access_token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri or GOOGLE_TOKEN_URL,
            "scopes": list(credentials.scopes or GOOGLE_HEALTH_SCOPES),
            "expiry": expiry.isoformat() if expiry else None,
        }

    def exchange_code(self, code: str, state: str | None) -> dict[str, Any]:
        if not state:
            raise GoogleHealthError(
                "The OAuth callback state is missing. Start a new connection."
            )
        pending = self.token_store.load_pending()
        if (
            not pending
            or not secrets.compare_digest(pending.get("state", ""), state)
            or int(time.time()) - int(pending.get("created_at", 0)) > 600
        ):
            raise GoogleHealthError(
                "The PKCE verifier is missing or expired. Start a new connection."
            )
        flow = self._flow(state, pending["code_verifier"])
        try:
            flow.fetch_token(code=code)
        except Exception as error:
            raise GoogleHealthError(f"Google token exchange failed: {error}") from error
        finally:
            self.token_store.delete_pending()
        try:
            token = self._token_from_credentials(flow.credentials)
        except Exception as error:
            raise GoogleHealthError(
                f"Google token response processing failed: {error}"
            ) from error
        if not token["refresh_token"]:
            raise GoogleHealthError(
                "Google did not return a refresh token. Reconnect with consent."
            )
        try:
            self.token_store.save(token)
        except Exception as error:
            raise GoogleHealthError(f"Secure token storage failed: {error}") from error
        return token

    def _credentials(self, token: dict[str, Any]) -> Credentials:
        expiry = datetime.fromisoformat(token["expiry"]) if token.get("expiry") else None
        if expiry and expiry.tzinfo is not None:
            expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)
        return Credentials(
            token=token.get("access_token"), refresh_token=token.get("refresh_token"),
            token_uri=token.get("token_uri", GOOGLE_TOKEN_URL),
            client_id=self.config.client_id, client_secret=self.config.client_secret,
            scopes=token.get("scopes", GOOGLE_HEALTH_SCOPES), expiry=expiry,
        )

    def refresh_access_token(self, token: dict[str, Any]) -> dict[str, Any]:
        credentials = self._credentials(token)
        if not credentials.refresh_token:
            raise GoogleHealthError("The stored Google token has no refresh token.")
        credentials.refresh(GoogleAuthRequest())
        refreshed = self._token_from_credentials(credentials)
        refreshed["refresh_token"] = refreshed["refresh_token"] or token["refresh_token"]
        self.token_store.save(refreshed)
        return refreshed

    def access_token(self) -> str:
        token = self.token_store.load()
        if not token:
            raise GoogleHealthError("Google Health is not connected.")
        if not self._credentials(token).valid:
            token = self.refresh_access_token(token)
        return token["access_token"]

    def is_connected(self) -> bool:
        return self.token_store.load() is not None

    def disconnect(self) -> None:
        self.token_store.delete()

    def request_json(self, method, path, *, params=None, body=None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.access_token()}", "Accept": "application/json"}
        response = self.http.request(method, f"{GOOGLE_HEALTH_API_URL}{path}", headers=headers, params=params, json=body)
        if response.status_code == 401:
            stored = self.token_store.load()
            if not stored:
                raise GoogleHealthError("Google Health is not connected.")
            headers["Authorization"] = f"Bearer {self.refresh_access_token(stored)['access_token']}"
            response = self.http.request(method, f"{GOOGLE_HEALTH_API_URL}{path}", headers=headers, params=params, json=body)
        if not response.is_success:
            try:
                api_error = response.json().get("error", {})
                detail = api_error.get("message", "")
                status = api_error.get("status", "")
            except (ValueError, AttributeError):
                detail, status = "", ""
            description = ": ".join(value for value in (status, detail) if value)
            suffix = f" {description}" if description else ""
            raise GoogleHealthError(
                f"Google Health {method.upper()} {path} failed "
                f"({response.status_code}).{suffix}"
            )
        return response.json()

    @staticmethod
    def _civil_range(start_date: date, end_date: date) -> dict[str, Any]:
        def value(day):
            return {"date": {"year": day.year, "month": day.month, "day": day.day}, "time": {"hours": 0, "minutes": 0, "seconds": 0, "nanos": 0}}
        return {"start": value(start_date), "end": value(end_date + timedelta(days=1))}

    def _daily_rollups(self, data_type, start_date, end_date, chunk_days):
        points = []
        chunk_start = start_date
        while chunk_start <= end_date:
            chunk_end = min(chunk_start + timedelta(days=chunk_days - 1), end_date)
            payload = self.request_json(
                "POST", f"/v4/users/me/dataTypes/{data_type}/dataPoints:dailyRollUp",
                body={
                    "range": self._civil_range(chunk_start, chunk_end),
                    "windowSizeDays": 1,
                },
            )
            points.extend(payload.get("rollupDataPoints", []))
            chunk_start = chunk_end + timedelta(days=1)
        return points

    def _reconciled_points(
        self, data_type, filter_expression, *, page_size=1000
    ):
        points, page_token = [], None
        while True:
            params = {"filter": filter_expression, "pageSize": page_size}
            if page_token:
                params["pageToken"] = page_token
            payload = self.request_json("GET", f"/v4/users/me/dataTypes/{data_type}/dataPoints:reconcile", params=params)
            points.extend(payload.get("dataPoints", []))
            page_token = payload.get("nextPageToken")
            if not page_token:
                return points

    def fetch_historical_data(self, days=365) -> GoogleHealthDataBundle:
        end_date = date.today()
        start_date = end_date - timedelta(days=days - 1)
        return GoogleHealthDataBundle(
            activity=self._fetch_activity(start_date, end_date),
            sleep=self._fetch_sleep(start_date, end_date),
            weight=self._fetch_weight(start_date, end_date),
        )

    def _rollup_values(
        self, data_type, value_key, field, start_date, end_date, chunk_days=30
    ):
        return {
            civil_date(point["civilStartTime"]["date"]): float(point[value_key][field])
            for point in self._daily_rollups(data_type, start_date, end_date, chunk_days)
            if point.get(value_key, {}).get(field) is not None
        }

    def _fetch_activity(self, start_date, end_date):
        steps = self._rollup_values("steps", "steps", "countSum", start_date, end_date)
        distance_mm = self._rollup_values("distance", "distance", "millimetersSum", start_date, end_date)
        calories = self._rollup_values("total-calories", "totalCalories", "kcalSum", start_date, end_date, 14)
        active_points = self._daily_rollups("active-minutes", start_date, end_date, 14)
        active_minutes = {
            civil_date(point["civilStartTime"]["date"]): sum(
                int(level.get("activeMinutesSum", 0))
                for level in point.get("activeMinutes", {}).get("activeMinutesRollupByActivityLevel", [])
            ) for point in active_points
        }
        exclusive_end = end_date + timedelta(days=1)
        heart_filter = (
            f'daily_resting_heart_rate.date >= "{start_date}" AND '
            f'daily_resting_heart_rate.date < "{exclusive_end}"'
        )
        heart_points = self._reconciled_points("daily-resting-heart-rate", heart_filter)
        resting = {
            civil_date(point["dailyRestingHeartRate"]["date"]): int(point["dailyRestingHeartRate"]["beatsPerMinute"])
            for point in heart_points if point.get("dailyRestingHeartRate")
        }
        all_dates = sorted(set(steps) | set(distance_mm) | set(calories) | set(active_minutes) | set(resting))
        return [ActivityRecord(
            day=day, steps=int(steps.get(day, 0)), active_minutes=int(active_minutes.get(day, 0)),
            calories=calories.get(day),
            distance=distance_mm[day] / MILLIMETERS_PER_MILE if day in distance_mm else None,
            resting_heart_rate=resting.get(day),
        ) for day in all_dates]

    def _fetch_sleep(self, start_date, end_date):
        expression = f'sleep.interval.civil_end_time >= "{start_date}" AND sleep.interval.civil_end_time < "{end_date + timedelta(days=1)}"'
        points = self._reconciled_points("sleep", expression, page_size=25)
        by_day = {}
        for point in points:
            sleep = point.get("sleep", {})
            end_civil = sleep.get("interval", {}).get("civilEndTime")
            if not end_civil:
                continue
            day = civil_date(end_civil["date"])
            current = by_day.get(day)
            is_main = sleep.get("metadata", {}).get("isMainSleep", False)
            current_main = current.get("metadata", {}).get("isMainSleep", False) if current else False
            minutes = int(sleep.get("summary", {}).get("minutesAsleep", 0))
            current_minutes = int(current.get("summary", {}).get("minutesAsleep", 0)) if current else 0
            if current is None or (is_main and not current_main) or (is_main == current_main and minutes > current_minutes):
                by_day[day] = sleep
        records = []
        for day, sleep in sorted(by_day.items()):
            interval, summary = sleep["interval"], sleep.get("summary", {})
            start, end = civil_datetime(interval["civilStartTime"]), civil_datetime(interval["civilEndTime"])
            stages = {item["type"]: int(item.get("minutes", 0)) for item in summary.get("stagesSummary", [])}
            asleep = int(summary.get("minutesAsleep", 0))
            deep, rem = stages.get("DEEP", 0), stages.get("REM", 0)
            light = stages.get("LIGHT", stages.get("ASLEEP", 0))
            if not (deep or rem or light):
                light = asleep
            records.append(SleepRecord(
                day=day, bedtime=start.time(), wake_time=end.time(), duration_hours=asleep / 60,
                deep_sleep_hours=deep / 60, rem_sleep_hours=rem / 60, light_sleep_hours=light / 60,
            ))
        return records

    def _fetch_weight(self, start_date, end_date):
        weights = self._rollup_values("weight", "weight", "weightGramsAvg", start_date, end_date)
        return [WeightRecord(day=day, weight_lb=grams / GRAMS_PER_POUND) for day, grams in sorted(weights.items())]
