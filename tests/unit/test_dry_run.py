"""Unit tests for POST /test dry-run via evaluate_alert(dry_run=True)."""
import pytest
from datetime import datetime, timezone

from app.engine import evaluate_alert
from app.models import Alert, AppState, RouteConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

T0 = datetime(2026, 3, 25, 14, 30, 0, tzinfo=timezone.utc)


def make_alert(alert_id: str = "a1", service: str = "payment-api") -> Alert:
    return Alert(
        id=alert_id,
        severity="critical",
        service=service,
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


def fresh_state(*routes: RouteConfig) -> AppState:
    state = AppState()
    for r in routes:
        state.routes[r.id] = r
    return state


# ---------------------------------------------------------------------------
# Dry-run returns correct result without mutating state
# ---------------------------------------------------------------------------

class TestDryRunNoStateMutation:
    def test_dry_run_returns_alert_result(self):
        state = fresh_state(make_route())
        result = evaluate_alert(make_alert(), state, dry_run=True)
        assert result.alert_id == "a1"
        assert result.routed_to is not None
        assert result.routed_to.route_id == "r1"

    def test_dry_run_does_not_store_alert_result(self):
        state = fresh_state(make_route())
        evaluate_alert(make_alert(), state, dry_run=True)
        assert "a1" not in state.alerts

    def test_dry_run_does_not_store_alert_input(self):
        state = fresh_state(make_route())
        evaluate_alert(make_alert(), state, dry_run=True)
        assert "a1" not in state.alert_inputs

    def test_dry_run_does_not_update_stats(self):
        state = fresh_state(make_route())
        evaluate_alert(make_alert(), state, dry_run=True)
        assert state.stats.total_alerts_processed == 0
        assert state.stats.total_routed == 0

    def test_dry_run_does_not_set_suppression_window(self):
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert(), state, dry_run=True)
        assert state.suppression_windows == {}

    def test_dry_run_does_not_increment_by_severity(self):
        state = fresh_state(make_route())
        evaluate_alert(make_alert(), state, dry_run=True)
        assert state.stats.by_severity["critical"] == 0

    def test_dry_run_does_not_increment_by_service(self):
        state = fresh_state(make_route())
        evaluate_alert(make_alert(), state, dry_run=True)
        assert state.stats.by_service == {}


# ---------------------------------------------------------------------------
# Dry-run reads existing suppression state
# ---------------------------------------------------------------------------

class TestDryRunReadsSuppressionState:
    def test_dry_run_reports_suppressed_within_window(self):
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1"), state)
        from datetime import timedelta
        suppressed_ts = (T0 + timedelta(seconds=100)).isoformat()
        suppressed_alert = Alert(
            id="a2", severity="critical", service="payment-api",
            group="backend", timestamp=suppressed_ts,
        )
        result = evaluate_alert(suppressed_alert, state, dry_run=True)
        assert result.suppressed is True

    def test_dry_run_does_not_update_window_when_suppressed(self):
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1"), state)
        original_expiry = state.suppression_windows[("r1", "payment-api")]
        from datetime import timedelta
        ts2 = (T0 + timedelta(seconds=100)).isoformat()
        evaluate_alert(Alert(id="a2", severity="critical", service="payment-api",
                             group="backend", timestamp=ts2), state, dry_run=True)
        assert state.suppression_windows[("r1", "payment-api")] == original_expiry

    def test_dry_run_after_expiry_does_not_reset_window(self):
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1"), state)
        original_expiry = state.suppression_windows[("r1", "payment-api")]
        from datetime import timedelta
        ts2 = (T0 + timedelta(seconds=400)).isoformat()
        evaluate_alert(Alert(id="a2", severity="critical", service="payment-api",
                             group="backend", timestamp=ts2), state, dry_run=True)
        assert state.suppression_windows[("r1", "payment-api")] == original_expiry


# ---------------------------------------------------------------------------
# Dry-run result correctness
# ---------------------------------------------------------------------------

class TestDryRunResultContent:
    def test_dry_run_unrouted_when_no_routes(self):
        state = AppState()
        result = evaluate_alert(make_alert(), state, dry_run=True)
        assert result.routed_to is None

    def test_dry_run_evaluation_details_correct(self):
        state = fresh_state(make_route())
        result = evaluate_alert(make_alert(), state, dry_run=True)
        assert result.evaluation_details.total_routes_evaluated == 1
        assert result.evaluation_details.routes_matched == 1
        assert result.evaluation_details.routes_not_matched == 0

    def test_dry_run_matched_routes_populated(self):
        state = fresh_state(make_route())
        result = evaluate_alert(make_alert(), state, dry_run=True)
        assert result.matched_routes == ["r1"]

    def test_dry_run_real_alert_unaffected_by_preceding_dry_run(self):
        # A dry-run should not set any window, so the real alert goes through unsuppressed
        state = fresh_state(make_route(window=300))
        evaluate_alert(make_alert("a1"), state, dry_run=True)
        result = evaluate_alert(make_alert("a1"), state, dry_run=False)
        assert result.suppressed is False
        assert result.routed_to is not None
