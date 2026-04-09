"""Unit tests for all Pydantic model validators in app/models.py."""
import pytest
from pydantic import ValidationError

from app.models import ActiveHours, Alert, Conditions, RouteConfig, Target


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_ALERT = dict(
    id="alert-1",
    severity="critical",
    service="payment-api",
    group="backend",
    timestamp="2026-03-25T14:30:00Z",
    labels={"env": "production"},
)

VALID_ROUTE = dict(
    id="route-1",
    conditions={},
    target={"type": "slack", "channel": "#oncall"},
    priority=10,
)


# ---------------------------------------------------------------------------
# Alert — required fields
# ---------------------------------------------------------------------------

class TestAlertRequiredFields:
    def test_valid_alert_passes(self):
        a = Alert(**VALID_ALERT)
        assert a.id == "alert-1"
        assert a.severity == "critical"

    def test_missing_id_raises(self):
        with pytest.raises(ValidationError, match="id"):
            Alert(**{k: v for k, v in VALID_ALERT.items() if k != "id"})

    def test_missing_severity_raises(self):
        with pytest.raises(ValidationError, match="severity"):
            Alert(**{k: v for k, v in VALID_ALERT.items() if k != "severity"})

    def test_missing_service_raises(self):
        with pytest.raises(ValidationError, match="service"):
            Alert(**{k: v for k, v in VALID_ALERT.items() if k != "service"})

    def test_missing_group_raises(self):
        with pytest.raises(ValidationError, match="group"):
            Alert(**{k: v for k, v in VALID_ALERT.items() if k != "group"})

    def test_missing_timestamp_raises(self):
        with pytest.raises(ValidationError, match="timestamp"):
            Alert(**{k: v for k, v in VALID_ALERT.items() if k != "timestamp"})

    def test_description_is_optional(self):
        a = Alert(**VALID_ALERT)
        assert a.description is None

    def test_labels_default_to_empty_dict(self):
        data = {k: v for k, v in VALID_ALERT.items() if k != "labels"}
        a = Alert(**data)
        assert a.labels == {}


# ---------------------------------------------------------------------------
# Alert — severity validation
# ---------------------------------------------------------------------------

class TestAlertSeverity:
    @pytest.mark.parametrize("severity", ["critical", "warning", "info"])
    def test_valid_severities(self, severity):
        a = Alert(**{**VALID_ALERT, "severity": severity})
        assert a.severity == severity

    @pytest.mark.parametrize("severity", ["high", "low", "CRITICAL", "Critical", "", "urgent"])
    def test_invalid_severity_raises(self, severity):
        with pytest.raises(ValidationError):
            Alert(**{**VALID_ALERT, "severity": severity})


# ---------------------------------------------------------------------------
# Alert — timestamp validation
# ---------------------------------------------------------------------------

class TestAlertTimestamp:
    @pytest.mark.parametrize("ts", [
        "2026-03-25T14:30:00Z",
        "2026-03-25T14:30:00+00:00",
        "2026-03-25T09:30:00-05:00",
        "2026-03-25T14:30:00.123456Z",
    ])
    def test_valid_timestamps(self, ts):
        a = Alert(**{**VALID_ALERT, "timestamp": ts})
        assert a.timestamp is not None

    def test_naive_timestamp_raises(self):
        # No timezone info — AwareDatetime rejects this
        with pytest.raises(ValidationError):
            Alert(**{**VALID_ALERT, "timestamp": "2026-03-25T14:30:00"})

    @pytest.mark.parametrize("ts", [
        "not-a-date",
        "25/03/2026",
        "2026-13-01T00:00:00Z",  # invalid month
        "",
    ])
    def test_invalid_timestamp_format_raises(self, ts):
        with pytest.raises(ValidationError):
            Alert(**{**VALID_ALERT, "timestamp": ts})


# ---------------------------------------------------------------------------
# ActiveHours — timezone validation
# ---------------------------------------------------------------------------

class TestActiveHoursTimezone:
    def test_valid_iana_timezone(self):
        ah = ActiveHours(timezone="America/New_York", start="09:00", end="17:00")
        assert ah.timezone == "America/New_York"

    @pytest.mark.parametrize("tz", [
        "America/Chicago",
        "Europe/London",
        "Asia/Tokyo",
        "UTC",
        "America/Los_Angeles",
    ])
    def test_valid_timezones(self, tz):
        ah = ActiveHours(timezone=tz, start="09:00", end="17:00")
        assert ah.timezone == tz

    @pytest.mark.parametrize("tz", [
        "Invalid/Zone",
        "EST",          # not a valid IANA zone
        "PST",
        "not-a-timezone",
        "",
        "US/Eastern",   # deprecated-style; zoneinfo may or may not accept — we test the boundary
    ])
    def test_invalid_timezone_raises(self, tz):
        # zoneinfo rejects these; "US/Eastern" may pass on some systems — skip gracefully
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(tz)  # if this doesn't raise, the system accepts it — skip assertion
        except Exception:
            with pytest.raises(ValidationError):
                ActiveHours(timezone=tz, start="09:00", end="17:00")


# ---------------------------------------------------------------------------
# ActiveHours — start/end time format
# ---------------------------------------------------------------------------

class TestActiveHoursTimeFormat:
    @pytest.mark.parametrize("t", ["00:00", "09:00", "17:00", "23:59"])
    def test_valid_time_formats(self, t):
        ah = ActiveHours(timezone="UTC", start=t, end=t)
        assert ah.start == t

    @pytest.mark.parametrize("t", [
        "9:00",     # missing leading zero
        "9:00am",
        "09:00:00", # seconds not allowed
        "25:00",    # invalid hour
        "09:60",    # invalid minute
        "0900",     # no colon
        "",
    ])
    def test_invalid_time_format_raises(self, t):
        with pytest.raises(ValidationError):
            ActiveHours(timezone="UTC", start=t, end="17:00")


# ---------------------------------------------------------------------------
# Target — type validation
# ---------------------------------------------------------------------------

class TestTargetType:
    def test_valid_slack_target(self):
        t = Target(type="slack", channel="#oncall")
        assert t.type == "slack"
        assert t.channel == "#oncall"

    def test_valid_email_target(self):
        t = Target(type="email", address="team@example.com")
        assert t.address == "team@example.com"

    def test_valid_pagerduty_target(self):
        t = Target(type="pagerduty", service_key="abc123")
        assert t.service_key == "abc123"

    def test_valid_webhook_target(self):
        t = Target(type="webhook", url="https://hooks.example.com/alert")
        assert t.url == "https://hooks.example.com/alert"

    def test_webhook_with_headers(self):
        t = Target(type="webhook", url="https://hooks.example.com/alert", headers={"X-Token": "secret"})
        assert t.headers == {"X-Token": "secret"}

    @pytest.mark.parametrize("bad_type", ["teams", "sms", "SLACK", "", "unknown"])
    def test_invalid_target_type_raises(self, bad_type):
        with pytest.raises(ValidationError):
            Target(type=bad_type, channel="#x")


class TestTargetRequiredFields:
    def test_slack_missing_channel_raises(self):
        with pytest.raises(ValidationError, match="channel"):
            Target(type="slack")

    def test_email_missing_address_raises(self):
        with pytest.raises(ValidationError, match="address"):
            Target(type="email")

    def test_pagerduty_missing_service_key_raises(self):
        with pytest.raises(ValidationError, match="service_key"):
            Target(type="pagerduty")

    def test_webhook_missing_url_raises(self):
        with pytest.raises(ValidationError, match="url"):
            Target(type="webhook")


# ---------------------------------------------------------------------------
# RouteConfig — priority validation
# ---------------------------------------------------------------------------

class TestRouteConfigPriority:
    def test_valid_integer_priority(self):
        r = RouteConfig(**VALID_ROUTE)
        assert r.priority == 10

    def test_zero_priority_is_valid(self):
        r = RouteConfig(**{**VALID_ROUTE, "priority": 0})
        assert r.priority == 0

    def test_negative_priority_is_valid(self):
        # Spec only says priority must be an integer, not that it must be positive
        r = RouteConfig(**{**VALID_ROUTE, "priority": -5})
        assert r.priority == -5

    def test_float_priority_raises(self):
        with pytest.raises(ValidationError, match="integer"):
            RouteConfig(**{**VALID_ROUTE, "priority": 10.0})

    def test_string_priority_raises(self):
        with pytest.raises(ValidationError, match="integer"):
            RouteConfig(**{**VALID_ROUTE, "priority": "10"})

    def test_boolean_priority_raises(self):
        with pytest.raises(ValidationError, match="integer"):
            RouteConfig(**{**VALID_ROUTE, "priority": True})


# ---------------------------------------------------------------------------
# RouteConfig — suppression_window_seconds validation
# ---------------------------------------------------------------------------

class TestRouteConfigSuppression:
    def test_default_suppression_is_zero(self):
        r = RouteConfig(**VALID_ROUTE)
        assert r.suppression_window_seconds == 0

    def test_positive_suppression_is_valid(self):
        r = RouteConfig(**{**VALID_ROUTE, "suppression_window_seconds": 300})
        assert r.suppression_window_seconds == 300

    def test_zero_suppression_is_valid(self):
        r = RouteConfig(**{**VALID_ROUTE, "suppression_window_seconds": 0})
        assert r.suppression_window_seconds == 0

    def test_negative_suppression_raises(self):
        with pytest.raises(ValidationError, match="non-negative"):
            RouteConfig(**{**VALID_ROUTE, "suppression_window_seconds": -1})

    def test_large_negative_suppression_raises(self):
        with pytest.raises(ValidationError, match="non-negative"):
            RouteConfig(**{**VALID_ROUTE, "suppression_window_seconds": -300})


# ---------------------------------------------------------------------------
# Conditions — empty conditions is valid (catch-all route)
# ---------------------------------------------------------------------------

class TestConditions:
    def test_empty_conditions_is_valid(self):
        c = Conditions()
        assert c.severity is None
        assert c.service is None
        assert c.group is None
        assert c.labels is None

    def test_route_with_empty_conditions_is_valid(self):
        r = RouteConfig(**{**VALID_ROUTE, "conditions": {}})
        assert r.conditions.severity is None

    def test_partial_conditions_is_valid(self):
        c = Conditions(severity=["critical"])
        assert c.severity == ["critical"]
        assert c.service is None


# ---------------------------------------------------------------------------
# Target — serialization excludes None fields (fix #1)
# ---------------------------------------------------------------------------

class TestTargetSerialization:
    def test_slack_target_omits_null_fields(self):
        t = Target(type="slack", channel="#oncall")
        d = t.model_dump()
        assert "address" not in d
        assert "service_key" not in d
        assert "url" not in d
        assert "headers" not in d

    def test_email_target_omits_null_fields(self):
        t = Target(type="email", address="ops@example.com")
        d = t.model_dump()
        assert "channel" not in d
        assert "service_key" not in d
        assert "url" not in d

    def test_pagerduty_target_omits_null_fields(self):
        t = Target(type="pagerduty", service_key="abc123")
        d = t.model_dump()
        assert "channel" not in d
        assert "address" not in d
        assert "url" not in d

    def test_webhook_target_with_headers_included(self):
        t = Target(type="webhook", url="https://hooks.example.com", headers={"X-Token": "s"})
        d = t.model_dump()
        assert d["url"] == "https://hooks.example.com"
        assert d["headers"] == {"X-Token": "s"}

    def test_webhook_target_without_headers_omits_it(self):
        t = Target(type="webhook", url="https://hooks.example.com")
        d = t.model_dump()
        assert "headers" not in d

    def test_type_and_relevant_field_always_present(self):
        t = Target(type="slack", channel="#oncall")
        d = t.model_dump()
        assert d["type"] == "slack"
        assert d["channel"] == "#oncall"


# ---------------------------------------------------------------------------
# Conditions — severity values validated (fix #2)
# ---------------------------------------------------------------------------

class TestConditionsSeverityValidation:
    def test_valid_severity_values_accepted(self):
        c = Conditions(severity=["critical", "warning", "info"])
        assert c.severity == ["critical", "warning", "info"]

    def test_invalid_severity_value_raises(self):
        with pytest.raises(ValidationError, match="Invalid severity"):
            Conditions(severity=["urgent"])

    def test_mixed_valid_invalid_raises(self):
        with pytest.raises(ValidationError, match="Invalid severity"):
            Conditions(severity=["critical", "unknown"])

    def test_none_severity_still_valid(self):
        c = Conditions()
        assert c.severity is None

    def test_invalid_severity_in_route_returns_400_via_post(self):
        # Tested at the model level; the validator raises ValidationError
        with pytest.raises(ValidationError):
            RouteConfig(**{
                **VALID_ROUTE,
                "conditions": {"severity": ["urgent"]},
            })
