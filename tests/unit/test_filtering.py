"""Unit tests for GET /alerts filtering via evaluate_alert + AppState."""
import pytest
from datetime import datetime, timezone

from app.engine import evaluate_alert
from app.models import Alert, AppState, RouteConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 3, 25, hour, minute, 0, tzinfo=timezone.utc)


def make_alert(alert_id: str, service: str = "payment-api", severity: str = "critical") -> Alert:
    return Alert(
        id=alert_id,
        severity=severity,
        service=service,
        group="backend",
        timestamp=utc(12).isoformat(),
    )


def make_route(route_id: str = "r1") -> RouteConfig:
    return RouteConfig(
        id=route_id,
        conditions={},
        target={"type": "slack", "channel": "#oncall"},
        priority=10,
    )


def fresh_state(*routes: RouteConfig) -> AppState:
    state = AppState()
    for r in routes:
        state.routes[r.id] = r
    return state


# ---------------------------------------------------------------------------
# alert_inputs stored on evaluate_alert
# ---------------------------------------------------------------------------

class TestAlertInputsStorage:
    def test_alert_stored_in_alert_inputs(self):
        state = fresh_state(make_route())
        alert = make_alert("a1")
        evaluate_alert(alert, state)
        assert "a1" in state.alert_inputs
        assert state.alert_inputs["a1"].service == "payment-api"

    def test_dry_run_does_not_store_alert_input(self):
        state = fresh_state(make_route())
        evaluate_alert(make_alert("a1"), state, dry_run=True)
        assert "a1" not in state.alert_inputs

    def test_resubmitted_alert_updates_alert_input(self):
        state = fresh_state(make_route())
        evaluate_alert(make_alert("a1", service="payment-api"), state)
        evaluate_alert(make_alert("a1", service="auth-service"), state)
        assert state.alert_inputs["a1"].service == "auth-service"

    def test_alert_inputs_cleared_on_reset(self):
        state = fresh_state(make_route())
        evaluate_alert(make_alert("a1"), state)
        state.reset()
        assert state.alert_inputs == {}


# ---------------------------------------------------------------------------
# Filtering by service
# ---------------------------------------------------------------------------

class TestFilterByService:
    def test_no_filter_returns_all(self):
        state = fresh_state(make_route())
        evaluate_alert(make_alert("a1", service="payment-api"), state)
        evaluate_alert(make_alert("a2", service="auth-service"), state)
        results = list(state.alerts.values())
        assert len(results) == 2

    def test_filter_by_service_returns_matching(self):
        state = fresh_state(make_route())
        evaluate_alert(make_alert("a1", service="payment-api"), state)
        evaluate_alert(make_alert("a2", service="auth-service"), state)
        filtered = [
            r for r in state.alerts.values()
            if state.alert_inputs.get(r.alert_id) and
               state.alert_inputs[r.alert_id].service == "payment-api"
        ]
        assert len(filtered) == 1
        assert filtered[0].alert_id == "a1"

    def test_filter_by_service_no_match(self):
        state = fresh_state(make_route())
        evaluate_alert(make_alert("a1", service="payment-api"), state)
        filtered = [
            r for r in state.alerts.values()
            if state.alert_inputs.get(r.alert_id) and
               state.alert_inputs[r.alert_id].service == "unknown"
        ]
        assert len(filtered) == 0


# ---------------------------------------------------------------------------
# Filtering by severity
# ---------------------------------------------------------------------------

class TestFilterBySeverity:
    def test_filter_by_severity_critical(self):
        state = fresh_state(make_route())
        evaluate_alert(make_alert("a1", severity="critical"), state)
        evaluate_alert(make_alert("a2", severity="warning"), state)
        filtered = [
            r for r in state.alerts.values()
            if state.alert_inputs.get(r.alert_id) and
               state.alert_inputs[r.alert_id].severity == "critical"
        ]
        assert len(filtered) == 1
        assert filtered[0].alert_id == "a1"

    def test_filter_by_severity_warning(self):
        state = fresh_state(make_route())
        evaluate_alert(make_alert("a1", severity="critical"), state)
        evaluate_alert(make_alert("a2", severity="warning"), state)
        filtered = [
            r for r in state.alerts.values()
            if state.alert_inputs.get(r.alert_id) and
               state.alert_inputs[r.alert_id].severity == "warning"
        ]
        assert len(filtered) == 1
        assert filtered[0].alert_id == "a2"


# ---------------------------------------------------------------------------
# Filtering by routed / suppressed
# ---------------------------------------------------------------------------

class TestFilterByRoutedSuppressed:
    def test_filter_routed_true(self):
        # Route with no suppression
        state = fresh_state(make_route())
        evaluate_alert(make_alert("a1"), state)
        filtered = [r for r in state.alerts.values() if r.routed_to is not None]
        assert len(filtered) == 1

    def test_filter_routed_false_when_no_matching_route(self):
        state = AppState()
        # No routes — alert won't be routed
        state.routes["r1"] = RouteConfig(
            id="r1",
            conditions={"severity": ["warning"]},
            target={"type": "slack", "channel": "#x"},
            priority=10,
        )
        evaluate_alert(make_alert("a1", severity="critical"), state)
        filtered = [r for r in state.alerts.values() if r.routed_to is None]
        assert len(filtered) == 1
        assert filtered[0].alert_id == "a1"

    def test_filter_suppressed_true(self):
        route = RouteConfig(
            id="r1", conditions={},
            target={"type": "slack", "channel": "#x"},
            priority=10,
            suppression_window_seconds=300,
        )
        state = fresh_state(route)
        evaluate_alert(make_alert("a1"), state)
        evaluate_alert(make_alert("a2"), state)
        suppressed = [r for r in state.alerts.values() if r.suppressed]
        assert len(suppressed) == 1
        assert suppressed[0].alert_id == "a2"

    def test_filter_suppressed_false(self):
        route = RouteConfig(
            id="r1", conditions={},
            target={"type": "slack", "channel": "#x"},
            priority=10,
            suppression_window_seconds=300,
        )
        state = fresh_state(route)
        evaluate_alert(make_alert("a1"), state)
        evaluate_alert(make_alert("a2"), state)
        not_suppressed = [r for r in state.alerts.values() if not r.suppressed]
        assert len(not_suppressed) == 1
        assert not_suppressed[0].alert_id == "a1"
