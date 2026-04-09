"""Unit tests for POST /reset — AppState.reset() completeness."""
import pytest
from datetime import datetime, timedelta, timezone

from app.engine import evaluate_alert
from app.models import Alert, AppState, RouteConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

T0 = datetime(2026, 3, 25, 14, 30, 0, tzinfo=timezone.utc)


def make_alert(alert_id: str = "a1") -> Alert:
    return Alert(
        id=alert_id,
        severity="critical",
        service="payment-api",
        group="backend",
        timestamp=T0.isoformat(),
    )


def make_route(route_id: str = "r1", window: int = 0) -> RouteConfig:
    return RouteConfig(
        id=route_id,
        conditions={},
        target={"type": "slack", "channel": "#oncall"},
        priority=10,
        suppression_window_seconds=window,
    )


def populated_state() -> AppState:
    route = make_route(window=300)
    state = AppState()
    state.routes[route.id] = route
    evaluate_alert(make_alert("a1"), state)
    evaluate_alert(make_alert("a2"), state)  # suppressed
    return state


# ---------------------------------------------------------------------------
# Reset clears all data
# ---------------------------------------------------------------------------

class TestResetClearsData:
    def test_reset_clears_routes(self):
        state = populated_state()
        state.reset()
        assert state.routes == {}

    def test_reset_clears_alerts(self):
        state = populated_state()
        state.reset()
        assert state.alerts == {}

    def test_reset_clears_alert_inputs(self):
        state = populated_state()
        state.reset()
        assert state.alert_inputs == {}

    def test_reset_clears_suppression_windows(self):
        state = populated_state()
        state.reset()
        assert state.suppression_windows == {}

    def test_reset_zeroes_total_alerts_processed(self):
        state = populated_state()
        state.reset()
        assert state.stats.total_alerts_processed == 0

    def test_reset_zeroes_total_routed(self):
        state = populated_state()
        state.reset()
        assert state.stats.total_routed == 0

    def test_reset_zeroes_total_suppressed(self):
        state = populated_state()
        state.reset()
        assert state.stats.total_suppressed == 0

    def test_reset_zeroes_total_unrouted(self):
        state = populated_state()
        state.reset()
        assert state.stats.total_unrouted == 0

    def test_reset_restores_by_severity_defaults(self):
        state = populated_state()
        state.reset()
        assert state.stats.by_severity == {"critical": 0, "warning": 0, "info": 0}

    def test_reset_clears_by_route(self):
        state = populated_state()
        state.reset()
        assert state.stats.by_route == {}

    def test_reset_clears_by_service(self):
        state = populated_state()
        state.reset()
        assert state.stats.by_service == {}


# ---------------------------------------------------------------------------
# State is fully functional after reset
# ---------------------------------------------------------------------------

class TestStateAfterReset:
    def test_can_add_route_after_reset(self):
        state = populated_state()
        state.reset()
        new_route = make_route("new-route")
        state.routes[new_route.id] = new_route
        assert "new-route" in state.routes

    def test_alert_routes_correctly_after_reset(self):
        state = populated_state()
        state.reset()
        route = make_route()
        state.routes[route.id] = route
        result = evaluate_alert(make_alert("a-new"), state)
        assert result.routed_to is not None
        assert result.routed_to.route_id == "r1"

    def test_suppression_fresh_after_reset(self):
        state = populated_state()
        state.reset()
        route = make_route(window=300)
        state.routes[route.id] = route
        result = evaluate_alert(make_alert("a-new"), state)
        # No prior suppression window — should route, not suppress
        assert result.suppressed is False

    def test_stats_accumulate_from_zero_after_reset(self):
        state = populated_state()
        state.reset()
        route = make_route()
        state.routes[route.id] = route
        evaluate_alert(make_alert("x1"), state)
        assert state.stats.total_alerts_processed == 1
        assert state.stats.total_routed == 1

    def test_reset_is_idempotent(self):
        state = populated_state()
        state.reset()
        state.reset()
        assert state.routes == {}
        assert state.alerts == {}
        assert state.stats.total_alerts_processed == 0
