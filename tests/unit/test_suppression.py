"""Unit tests for suppression window logic in evaluate_alert."""
import pytest
from datetime import datetime, timedelta, timezone

from app.engine import evaluate_alert
from app.models import Alert, AppState, RouteConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

T0 = datetime(2026, 3, 25, 14, 30, 0, tzinfo=timezone.utc)   # base timestamp


def ts(offset_seconds: int) -> str:
    """Return an ISO 8601 UTC timestamp string offset from T0."""
    return (T0 + timedelta(seconds=offset_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_alert(alert_id="a1", service="payment-api", offset_seconds=0) -> Alert:
    return Alert(
        id=alert_id,
        severity="critical",
        service=service,
        group="backend",
        timestamp=ts(offset_seconds),
    )


def make_route(window: int = 300, route_id: str = "route-1") -> RouteConfig:
    return RouteConfig(
        id=route_id,
        conditions={},
        target={"type": "slack", "channel": "#oncall"},
        priority=10,
        suppression_window_seconds=window,
    )


def fresh_state(route: RouteConfig) -> AppState:
    state = AppState()
    state.routes[route.id] = route
    return state


# ---------------------------------------------------------------------------
# No suppression when window is zero
# ---------------------------------------------------------------------------

class TestNoSuppressionWhenWindowZero:
    def test_zero_window_never_suppresses(self):
        route = make_route(window=0)
        state = fresh_state(route)
        evaluate_alert(make_alert("a1"), state)
        result = evaluate_alert(make_alert("a2"), state)
        assert result.suppressed is False

    def test_zero_window_does_not_set_suppression_key(self):
        route = make_route(window=0)
        state = fresh_state(route)
        evaluate_alert(make_alert(), state)
        assert state.suppression_windows == {}


# ---------------------------------------------------------------------------
# First alert — routes normally and sets the window
# ---------------------------------------------------------------------------

class TestFirstAlertSetsWindow:
    def test_first_alert_is_not_suppressed(self):
        state = fresh_state(make_route(window=300))
        result = evaluate_alert(make_alert(), state)
        assert result.suppressed is False
        assert result.routed_to is not None
        assert result.routed_to.route_id == "route-1"

    def test_first_alert_sets_suppression_window(self):
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert(offset_seconds=0), state)
        key = ("route-1", "payment-api")
        assert key in state.suppression_windows
        expected_expiry = T0 + timedelta(seconds=300)
        assert state.suppression_windows[key] == expected_expiry

    def test_window_expiry_uses_alert_timestamp_not_wall_clock(self):
        # Submit a past-dated alert; expiry must be based on alert.timestamp
        route = make_route(window=600)
        state = fresh_state(route)
        past_alert = make_alert(offset_seconds=-3600)  # 1 hour in the past
        evaluate_alert(past_alert, state)
        key = ("route-1", "payment-api")
        expected_expiry = T0 + timedelta(seconds=-3600 + 600)
        assert state.suppression_windows[key] == expected_expiry


# ---------------------------------------------------------------------------
# Second alert within window — suppressed
# ---------------------------------------------------------------------------

class TestSuppressionWithinWindow:
    def test_second_alert_within_window_is_suppressed(self):
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1", offset_seconds=0), state)
        result = evaluate_alert(make_alert("a2", offset_seconds=100), state)
        assert result.suppressed is True

    def test_suppressed_alert_has_routed_to_set(self):
        # routed_to is still populated with the winning route even when suppressed
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1", offset_seconds=0), state)
        result = evaluate_alert(make_alert("a2", offset_seconds=100), state)
        assert result.routed_to is not None
        assert result.routed_to.route_id == "route-1"

    def test_suppressed_alert_has_suppression_reason(self):
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1", offset_seconds=0), state)
        result = evaluate_alert(make_alert("a2", offset_seconds=100), state)
        assert result.suppression_reason is not None
        assert "payment-api" in result.suppression_reason
        assert "route-1" in result.suppression_reason

    def test_suppression_reason_contains_expiry_time(self):
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1", offset_seconds=0), state)
        result = evaluate_alert(make_alert("a2", offset_seconds=100), state)
        # Expiry = T0 + 300s = 14:35:00Z
        assert "2026-03-25T14:35:00Z" in result.suppression_reason

    def test_suppression_applied_true_in_evaluation_details(self):
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1", offset_seconds=0), state)
        result = evaluate_alert(make_alert("a2", offset_seconds=100), state)
        assert result.evaluation_details.suppression_applied is True

    def test_suppression_window_not_updated_when_suppressed(self):
        # The existing window must remain unchanged when a suppression fires
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1", offset_seconds=0), state)
        original_expiry = state.suppression_windows[("route-1", "payment-api")]
        evaluate_alert(make_alert("a2", offset_seconds=100), state)
        assert state.suppression_windows[("route-1", "payment-api")] == original_expiry

    def test_alert_just_before_expiry_is_suppressed(self):
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1", offset_seconds=0), state)
        # 299 seconds in — still within window
        result = evaluate_alert(make_alert("a2", offset_seconds=299), state)
        assert result.suppressed is True

    def test_alert_at_exact_expiry_is_not_suppressed(self):
        # alert.timestamp == expiry → NOT suppressed (boundary: open interval)
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1", offset_seconds=0), state)
        result = evaluate_alert(make_alert("a2", offset_seconds=300), state)
        assert result.suppressed is False

    def test_alert_after_expiry_is_not_suppressed(self):
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1", offset_seconds=0), state)
        result = evaluate_alert(make_alert("a2", offset_seconds=301), state)
        assert result.suppressed is False


# ---------------------------------------------------------------------------
# After window expiry — routes again and resets window
# ---------------------------------------------------------------------------

class TestAfterWindowExpiry:
    def test_alert_after_expiry_routes_normally(self):
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1", offset_seconds=0), state)
        result = evaluate_alert(make_alert("a2", offset_seconds=301), state)
        assert result.suppressed is False
        assert result.routed_to.route_id == "route-1"

    def test_expired_window_sets_new_window(self):
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1", offset_seconds=0), state)
        evaluate_alert(make_alert("a2", offset_seconds=301), state)
        # New window based on a2's timestamp (T0 + 301s + 300s)
        expected = T0 + timedelta(seconds=301 + 300)
        assert state.suppression_windows[("route-1", "payment-api")] == expected

    def test_third_alert_after_new_window_suppressed(self):
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1", offset_seconds=0), state)
        evaluate_alert(make_alert("a2", offset_seconds=301), state)  # routes, resets window
        result = evaluate_alert(make_alert("a3", offset_seconds=400), state)  # within new window
        assert result.suppressed is True


# ---------------------------------------------------------------------------
# Suppression key isolation
# ---------------------------------------------------------------------------

class TestSuppressionKeyIsolation:
    def test_different_service_not_suppressed(self):
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1", service="payment-api",  offset_seconds=0), state)
        result = evaluate_alert(make_alert("a2", service="auth-service", offset_seconds=100), state)
        assert result.suppressed is False

    def test_different_route_not_suppressed(self):
        # route-1 matches only critical; route-2 matches only warning.
        # Suppression key is (route_id, service), so a window on route-1 must
        # not affect route-2.
        route1 = RouteConfig(
            id="route-1",
            conditions={"severity": ["critical"]},
            target={"type": "slack", "channel": "#oncall"},
            priority=10,
            suppression_window_seconds=300,
        )
        route2 = RouteConfig(
            id="route-2",
            conditions={"severity": ["warning"]},
            target={"type": "slack", "channel": "#other"},
            priority=5,
            suppression_window_seconds=300,
        )
        state = AppState()
        state.routes["route-1"] = route1
        state.routes["route-2"] = route2
        # Trigger window on (route-1, payment-api) via a critical alert
        evaluate_alert(make_alert("a1", offset_seconds=0), state)
        # A warning alert wins on route-2 — key (route-2, payment-api) has no window
        warning_alert = Alert(
            id="a2", severity="warning", service="payment-api",
            group="backend", timestamp=ts(100),
        )
        result = evaluate_alert(warning_alert, state)
        assert result.routed_to.route_id == "route-2"
        assert result.suppressed is False


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

class TestDryRunSuppression:
    def test_dry_run_does_not_set_suppression_window(self):
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert(), state, dry_run=True)
        assert state.suppression_windows == {}

    def test_dry_run_reads_suppression_state_correctly(self):
        # Set up a real window first via a non-dry-run call
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1", offset_seconds=0), state)
        # Dry-run within the window should report suppressed
        result = evaluate_alert(make_alert("a2", offset_seconds=100), state, dry_run=True)
        assert result.suppressed is True

    def test_dry_run_does_not_update_window_when_suppressed(self):
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1", offset_seconds=0), state)
        original_expiry = state.suppression_windows[("route-1", "payment-api")]
        evaluate_alert(make_alert("a2", offset_seconds=100), state, dry_run=True)
        assert state.suppression_windows[("route-1", "payment-api")] == original_expiry

    def test_dry_run_does_not_reset_expired_window(self):
        # After expiry, dry-run should NOT set a new window
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1", offset_seconds=0), state)
        evaluate_alert(make_alert("a2", offset_seconds=301), state, dry_run=True)
        # Window should still be the original one (from a1), not updated by dry-run
        expected_original = T0 + timedelta(seconds=300)
        assert state.suppression_windows[("route-1", "payment-api")] == expected_original


# ---------------------------------------------------------------------------
# Stats with suppression
# ---------------------------------------------------------------------------

class TestSuppressionStats:
    def test_suppressed_increments_total_suppressed(self):
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1", offset_seconds=0), state)
        evaluate_alert(make_alert("a2", offset_seconds=100), state)
        assert state.stats.total_suppressed == 1
        assert state.stats.total_routed == 1

    def test_suppressed_does_not_increment_total_routed(self):
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1", offset_seconds=0), state)
        evaluate_alert(make_alert("a2", offset_seconds=100), state)
        assert state.stats.total_routed == 1  # only the first

    def test_by_route_total_suppressed(self):
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1", offset_seconds=0), state)
        evaluate_alert(make_alert("a2", offset_seconds=100), state)
        rs = state.stats.by_route["route-1"]
        assert rs.total_matched == 2
        assert rs.total_routed == 1
        assert rs.total_suppressed == 1
