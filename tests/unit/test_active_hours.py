"""Unit tests for active_hours logic in evaluate_alert."""
import pytest
from datetime import datetime, timezone, timedelta

from app.engine import _is_within_active_hours, evaluate_alert
from app.models import ActiveHours, Alert, AppState, RouteConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utc(hour: int, minute: int = 0, day: int = 25) -> datetime:
    """Return a UTC datetime on 2026-03-{day}."""
    return datetime(2026, 3, day, hour, minute, 0, tzinfo=timezone.utc)


def active_hours(tz: str, start: str, end: str) -> ActiveHours:
    return ActiveHours(timezone=tz, start=start, end=end)


def make_alert(timestamp: datetime, alert_id: str = "a1") -> Alert:
    return Alert(
        id=alert_id,
        severity="critical",
        service="payment-api",
        group="backend",
        timestamp=timestamp.isoformat(),
    )


def make_route_with_hours(tz: str, start: str, end: str, route_id: str = "r1") -> RouteConfig:
    return RouteConfig(
        id=route_id,
        conditions={},
        target={"type": "slack", "channel": "#oncall"},
        priority=10,
        active_hours={"timezone": tz, "start": start, "end": end},
    )


def fresh_state(*routes: RouteConfig) -> AppState:
    state = AppState()
    for r in routes:
        state.routes[r.id] = r
    return state


# ---------------------------------------------------------------------------
# _is_within_active_hours — UTC window (no conversion needed)
# ---------------------------------------------------------------------------

class TestIsWithinActiveHoursUTC:
    def test_time_within_window(self):
        ah = active_hours("UTC", "09:00", "17:00")
        assert _is_within_active_hours(utc(12, 0), ah) is True

    def test_time_at_start_inclusive(self):
        ah = active_hours("UTC", "09:00", "17:00")
        assert _is_within_active_hours(utc(9, 0), ah) is True

    def test_time_just_after_start(self):
        ah = active_hours("UTC", "09:00", "17:00")
        assert _is_within_active_hours(utc(9, 1), ah) is True

    def test_time_at_end_exclusive(self):
        ah = active_hours("UTC", "09:00", "17:00")
        assert _is_within_active_hours(utc(17, 0), ah) is False

    def test_time_just_before_end(self):
        ah = active_hours("UTC", "09:00", "17:00")
        assert _is_within_active_hours(utc(16, 59), ah) is True

    def test_time_before_start(self):
        ah = active_hours("UTC", "09:00", "17:00")
        assert _is_within_active_hours(utc(8, 59), ah) is False

    def test_time_after_end(self):
        ah = active_hours("UTC", "09:00", "17:00")
        assert _is_within_active_hours(utc(17, 1), ah) is False

    def test_midnight_is_outside_daytime_window(self):
        ah = active_hours("UTC", "09:00", "17:00")
        assert _is_within_active_hours(utc(0, 0), ah) is False


# ---------------------------------------------------------------------------
# _is_within_active_hours — timezone conversion (America/New_York)
# ---------------------------------------------------------------------------
#
# DST in 2026: springs forward March 8 → America/New_York = UTC-4 on March 25
# So UTC hour H → local hour H-4.

class TestIsWithinActiveHoursTimezoneConversion:
    TZ = "America/New_York"
    # Window 09:00–17:00 ET = 13:00–21:00 UTC

    def test_utc_within_local_window(self):
        ah = active_hours(self.TZ, "09:00", "17:00")
        # 14:00 UTC = 10:00 ET — within [09:00, 17:00)
        assert _is_within_active_hours(utc(14), ah) is True

    def test_utc_before_local_window(self):
        ah = active_hours(self.TZ, "09:00", "17:00")
        # 12:59 UTC = 08:59 ET — before window
        assert _is_within_active_hours(utc(12, 59), ah) is False

    def test_utc_at_local_start_inclusive(self):
        ah = active_hours(self.TZ, "09:00", "17:00")
        # 13:00 UTC = 09:00 ET — exactly at start
        assert _is_within_active_hours(utc(13), ah) is True

    def test_utc_at_local_end_exclusive(self):
        ah = active_hours(self.TZ, "09:00", "17:00")
        # 21:00 UTC = 17:00 ET — exactly at end (exclusive)
        assert _is_within_active_hours(utc(21), ah) is False

    def test_utc_just_before_local_end(self):
        ah = active_hours(self.TZ, "09:00", "17:00")
        # 20:59 UTC = 16:59 ET — just inside window
        assert _is_within_active_hours(utc(20, 59), ah) is True

    def test_utc_after_local_window(self):
        ah = active_hours(self.TZ, "09:00", "17:00")
        # 22:00 UTC = 18:00 ET — after window
        assert _is_within_active_hours(utc(22), ah) is False

    def test_non_utc_input_timestamp(self):
        ah = active_hours(self.TZ, "09:00", "17:00")
        # 10:00 ET = 14:00 UTC expressed as -04:00 offset
        from datetime import timezone as tz_mod
        et_offset = tz_mod(timedelta(hours=-4))
        ts = datetime(2026, 3, 25, 10, 0, 0, tzinfo=et_offset)
        assert _is_within_active_hours(ts, ah) is True


# ---------------------------------------------------------------------------
# _is_within_active_hours — midnight-crossing window
# ---------------------------------------------------------------------------

class TestMidnightCrossingWindow:
    # Window 22:00–06:00 UTC (crosses midnight)

    def test_time_just_after_start(self):
        ah = active_hours("UTC", "22:00", "06:00")
        assert _is_within_active_hours(utc(22, 1), ah) is True

    def test_time_at_start_inclusive(self):
        ah = active_hours("UTC", "22:00", "06:00")
        assert _is_within_active_hours(utc(22, 0), ah) is True

    def test_time_before_midnight(self):
        ah = active_hours("UTC", "22:00", "06:00")
        assert _is_within_active_hours(utc(23, 30), ah) is True

    def test_time_at_midnight(self):
        ah = active_hours("UTC", "22:00", "06:00")
        assert _is_within_active_hours(utc(0, 0), ah) is True

    def test_time_early_morning_inside(self):
        ah = active_hours("UTC", "22:00", "06:00")
        assert _is_within_active_hours(utc(5, 59), ah) is True

    def test_time_at_end_exclusive(self):
        ah = active_hours("UTC", "22:00", "06:00")
        assert _is_within_active_hours(utc(6, 0), ah) is False

    def test_time_between_end_and_start(self):
        ah = active_hours("UTC", "22:00", "06:00")
        # 08:00 is between end (06:00) and start (22:00) — outside
        assert _is_within_active_hours(utc(8, 0), ah) is False

    def test_time_just_before_start(self):
        ah = active_hours("UTC", "22:00", "06:00")
        assert _is_within_active_hours(utc(21, 59), ah) is False


# ---------------------------------------------------------------------------
# evaluate_alert — active_hours integration
# ---------------------------------------------------------------------------

class TestEvaluateAlertActiveHours:
    def test_no_active_hours_always_matches(self):
        route = RouteConfig(
            id="r1", conditions={},
            target={"type": "slack", "channel": "#x"},
            priority=10,
        )
        for h in (0, 6, 12, 18, 23):
            result = evaluate_alert(make_alert(utc(h)), fresh_state(route))
            assert result.routed_to is not None

    def test_alert_within_active_hours_matches(self):
        route = make_route_with_hours("UTC", "09:00", "17:00")
        result = evaluate_alert(make_alert(utc(12)), fresh_state(route))
        assert result.routed_to is not None
        assert result.routed_to.route_id == "r1"

    def test_alert_outside_active_hours_not_matched(self):
        route = make_route_with_hours("UTC", "09:00", "17:00")
        result = evaluate_alert(make_alert(utc(18)), fresh_state(route))
        assert result.routed_to is None

    def test_outside_active_hours_counted_as_not_matched(self):
        route = make_route_with_hours("UTC", "09:00", "17:00")
        result = evaluate_alert(make_alert(utc(18)), fresh_state(route))
        assert result.evaluation_details.routes_matched == 0
        assert result.evaluation_details.routes_not_matched == 1

    def test_active_hours_filter_independent_of_conditions(self):
        # Route conditions match, but active_hours excludes it
        route = RouteConfig(
            id="r1",
            conditions={"severity": ["critical"]},
            target={"type": "slack", "channel": "#x"},
            priority=10,
            active_hours={"timezone": "UTC", "start": "09:00", "end": "17:00"},
        )
        result = evaluate_alert(make_alert(utc(20)), fresh_state(route))
        assert result.routed_to is None

    def test_lower_priority_active_route_wins_over_inactive_higher(self):
        high_inactive = make_route_with_hours("UTC", "09:00", "17:00", route_id="high")
        high_inactive_obj = RouteConfig(
            id="high", conditions={},
            target={"type": "slack", "channel": "#high"},
            priority=100,
            active_hours={"timezone": "UTC", "start": "09:00", "end": "17:00"},
        )
        low_always = RouteConfig(
            id="low", conditions={},
            target={"type": "slack", "channel": "#low"},
            priority=1,
        )
        state = fresh_state(high_inactive_obj, low_always)
        # 20:00 UTC — outside high route's window
        result = evaluate_alert(make_alert(utc(20)), state)
        assert result.routed_to.route_id == "low"

    def test_matched_routes_excludes_outside_active_hours(self):
        active_route = make_route_with_hours("UTC", "09:00", "17:00", route_id="active")
        inactive_route = make_route_with_hours("UTC", "18:00", "23:00", route_id="inactive")
        result = evaluate_alert(make_alert(utc(12)), fresh_state(active_route, inactive_route))
        assert result.matched_routes == ["active"]

    def test_timezone_conversion_america_new_york(self):
        # 13:00 UTC = 09:00 ET (UTC-4 on 2026-03-25, after DST)
        route = make_route_with_hours("America/New_York", "09:00", "17:00")
        assert evaluate_alert(make_alert(utc(13)), fresh_state(route)).routed_to is not None
        # 12:59 UTC = 08:59 ET — before window
        assert evaluate_alert(make_alert(utc(12, 59), "a2"), fresh_state(route)).routed_to is None

    def test_midnight_crossing_active_hours(self):
        route = make_route_with_hours("UTC", "22:00", "06:00")
        assert evaluate_alert(make_alert(utc(23)), fresh_state(route)).routed_to is not None
        assert evaluate_alert(make_alert(utc(3),  "a2"), fresh_state(route)).routed_to is not None
        assert evaluate_alert(make_alert(utc(8),  "a3"), fresh_state(route)).routed_to is None
