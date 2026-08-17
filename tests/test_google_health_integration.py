"""Offline tests for Google Health OAuth, refresh, and normalization."""

from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from google.oauth2.credentials import Credentials

from google_health_integration import (
    GOOGLE_AUTH_URL,
    GOOGLE_HEALTH_SCOPES,
    GoogleHealthClient,
    GoogleHealthConfig,
    valid_oauth_state,
)


class MemoryTokenStore:
    def __init__(self, token=None):
        self.token = token
        self.pending = None

    def load(self):
        return self.token

    def save(self, token):
        self.token = token

    def delete(self):
        self.token = None

    def load_pending(self):
        return self.pending

    def save_pending(self, pending):
        self.pending = pending

    def delete_pending(self):
        self.pending = None


def config() -> GoogleHealthConfig:
    return GoogleHealthConfig(
        "client-id", "client-secret", "http://localhost:8501"
    )


def test_google_authorization_url_and_state():
    client = GoogleHealthClient(config(), token_store=MemoryTokenStore())
    signed_state = __import__("google_health_integration").oauth_state("secret")
    parsed = urlparse(client.authorization_url(signed_state))
    query = parse_qs(parsed.query)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == GOOGLE_AUTH_URL
    assert query["client_id"] == ["client-id"]
    assert query["state"] == [signed_state]
    assert query["access_type"] == ["offline"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"]
    assert client.token_store.pending["state"] == signed_state
    assert client.token_store.pending["code_verifier"]
    assert set(query["scope"][0].split()) == set(GOOGLE_HEALTH_SCOPES)
    assert valid_oauth_state(signed_state, None, "secret")
    assert valid_oauth_state("session-state", "session-state", "secret")
    assert not valid_oauth_state(signed_state, None, "wrong-secret")
    assert not valid_oauth_state("wrong", "session-state", "secret")


def test_expired_token_is_refreshed(monkeypatch):
    store = MemoryTokenStore(
        {
            "access_token": "old",
            "refresh_token": "refresh",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": GOOGLE_HEALTH_SCOPES,
            "expiry": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        }
    )

    def fake_refresh(credentials, request):
        credentials.token = "new"
        credentials.expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    monkeypatch.setattr(Credentials, "refresh", fake_refresh)
    client = GoogleHealthClient(config(), token_store=store)
    assert client.access_token() == "new"
    assert store.token["refresh_token"] == "refresh"


class NormalizationClient(GoogleHealthClient):
    def _daily_rollups(self, data_type, start_date, end_date, chunk_days):
        common = {
            "civilStartTime": {"date": {"year": 2026, "month": 1, "day": 1}}
        }
        values = {
            "steps": {"steps": {"countSum": "10100"}},
            "distance": {"distance": {"millimetersSum": "8046720"}},
            "total-calories": {"totalCalories": {"kcalSum": 2250}},
            "active-minutes": {
                "activeMinutes": {
                    "activeMinutesRollupByActivityLevel": [
                        {"activityLevel": "LIGHT", "activeMinutesSum": "60"},
                        {"activityLevel": "MODERATE", "activeMinutesSum": "25"},
                        {"activityLevel": "VIGOROUS", "activeMinutesSum": "15"},
                    ]
                }
            },
            "weight": {"weight": {"weightGramsAvg": 72347.2}},
        }
        return [{**common, **values[data_type]}]

    def _reconciled_points(self, data_type, filter_expression, *, page_size=1000):
        if data_type == "sleep":
            assert page_size == 25
        if data_type == "daily-resting-heart-rate":
            assert 'daily_resting_heart_rate.date >= "2026-01-01"' in filter_expression
            assert 'daily_resting_heart_rate.date < "2026-01-02"' in filter_expression
            assert "<=" not in filter_expression
            return [
                {
                    "dailyRestingHeartRate": {
                        "date": {"year": 2026, "month": 1, "day": 1},
                        "beatsPerMinute": "62",
                    }
                }
            ]
        if data_type == "sleep":
            return [
                {
                    "sleep": {
                        "interval": {
                            "civilStartTime": {
                                "date": {"year": 2025, "month": 12, "day": 31},
                                "time": {"hours": 23},
                            },
                            "civilEndTime": {
                                "date": {"year": 2026, "month": 1, "day": 1},
                                "time": {"hours": 6, "minutes": 30},
                            },
                        },
                        "metadata": {"isMainSleep": True},
                        "summary": {
                            "minutesAsleep": "420",
                            "stagesSummary": [
                                {"type": "DEEP", "minutes": "80"},
                                {"type": "REM", "minutes": "100"},
                                {"type": "LIGHT", "minutes": "240"},
                            ],
                        },
                    }
                }
            ]
        raise AssertionError(data_type)


def test_google_health_payloads_normalize_to_internal_records():
    client = NormalizationClient(config(), token_store=MemoryTokenStore())
    day = date(2026, 1, 1)
    activity = client._fetch_activity(day, day)
    sleep = client._fetch_sleep(day, day)
    weight = client._fetch_weight(day, day)
    assert activity[0].steps == 10_100
    assert activity[0].active_minutes == 100
    assert activity[0].calories == 2_250
    assert activity[0].distance == 5
    assert activity[0].resting_heart_rate == 62
    assert sleep[0].duration_hours == 7
    assert sleep[0].deep_sleep_hours == 80 / 60
    assert round(weight[0].weight_lb, 1) == 159.5


def test_api_error_identifies_failed_endpoint():
    class ErrorResponse:
        status_code = 400
        is_success = False

        @staticmethod
        def json():
            return {
                "error": {
                    "status": "INVALID_ARGUMENT",
                    "message": "Invalid argument in request.",
                }
            }

    class ErrorHttpClient:
        @staticmethod
        def request(*args, **kwargs):
            return ErrorResponse()

    client = GoogleHealthClient(
        config(), token_store=MemoryTokenStore(), http_client=ErrorHttpClient()
    )
    client.access_token = lambda: "test-token"

    try:
        client.request_json(
            "POST", "/v4/users/me/dataTypes/steps/dataPoints:dailyRollUp"
        )
    except Exception as error:
        message = str(error)
    else:
        raise AssertionError("Expected the request to fail")

    assert "POST" in message
    assert "dataTypes/steps/dataPoints:dailyRollUp" in message
    assert "INVALID_ARGUMENT" in message


def test_daily_rollups_use_conservative_chunks_without_page_size():
    class RecordingClient(GoogleHealthClient):
        def __init__(self):
            super().__init__(config(), token_store=MemoryTokenStore())
            self.requests = []

        def request_json(self, method, path, *, params=None, body=None):
            self.requests.append((method, path, body))
            return {"rollupDataPoints": []}

    client = RecordingClient()
    client._daily_rollups(
        "steps", date(2026, 1, 1), date(2026, 3, 6), chunk_days=30
    )

    assert len(client.requests) == 3
    assert all(request[0] == "POST" for request in client.requests)
    assert all("pageSize" not in request[2] for request in client.requests)
    assert all(request[2]["windowSizeDays"] == 1 for request in client.requests)
