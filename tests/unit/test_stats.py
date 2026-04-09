"""Unit tests for Stats defaults and AppState.reset()."""
import pytest

from app.models import AlertResult, AppState, EvaluationDetails, RouteConfig, Stats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_route(route_id: str = "route-1") -> RouteConfig:
    return RouteConfig(
        id=route_id,
        conditions={},
        target={"type": "slack", "channel": "#test"},
        priority=10,
    )


def make_alert_result(alert_id: str = "alert-1") -> AlertResult:
    return AlertResult(
        alert_id=alert_id,
        routed_to=None,
        suppressed=False,
        matched_routes=[],
        evaluation_details=EvaluationDetails(
            total_routes_evaluated=0,
            routes_matched=0,
            routes_not_matched=0,
            suppression_applied=False,
        ),
    )


# ---------------------------------------------------------------------------
# Stats — default values
# ---------------------------------------------------------------------------

class TestStatsDefaults:
    def test_total_counters_start_at_zero(self):
        s = Stats()
        assert s.total_alerts_processed == 0
        assert s.total_routed == 0
        assert s.total_suppressed == 0
        assert s.total_unrouted == 0

    def test_by_severity_has_all_three_keys(self):
        s = Stats()
        assert set(s.by_severity.keys()) == {"critical", "warning", "info"}

    def test_by_severity_starts_at_zero(self):
        s = Stats()
        assert s.by_severity["critical"] == 0
        assert s.by_severity["warning"] == 0
        assert s.by_severity["info"] == 0

    def test_by_route_starts_empty(self):
        s = Stats()
        assert s.by_route == {}

    def test_by_service_starts_empty(self):
        s = Stats()
        assert s.by_service == {}

    def test_two_stats_instances_do_not_share_by_severity(self):
        s1 = Stats()
        s2 = Stats()
        s1.by_severity["critical"] += 5
        assert s2.by_severity["critical"] == 0

    def test_two_stats_instances_do_not_share_by_route(self):
        s1 = Stats()
        s2 = Stats()
        from app.models import RouteStats
        s1.by_route["route-1"] = RouteStats(total_matched=3)
        assert "route-1" not in s2.by_route


# ---------------------------------------------------------------------------
# AppState — initial state
# ---------------------------------------------------------------------------

class TestAppStateInit:
    def test_routes_starts_empty(self):
        state = AppState()
        assert state.routes == {}

    def test_alerts_starts_empty(self):
        state = AppState()
        assert state.alerts == {}

    def test_suppression_windows_starts_empty(self):
        state = AppState()
        assert state.suppression_windows == {}

    def test_stats_is_fresh_on_init(self):
        state = AppState()
        assert state.stats.total_alerts_processed == 0


# ---------------------------------------------------------------------------
# AppState.reset()
# ---------------------------------------------------------------------------

class TestAppStateReset:
    def test_reset_clears_routes(self):
        state = AppState()
        state.routes["route-1"] = make_route()
        state.reset()
        assert state.routes == {}

    def test_reset_clears_alerts(self):
        state = AppState()
        state.alerts["alert-1"] = make_alert_result()
        state.reset()
        assert state.alerts == {}

    def test_reset_clears_suppression_windows(self):
        from datetime import datetime, timezone
        state = AppState()
        state.suppression_windows[("route-1", "payment-api")] = datetime(2026, 3, 25, 15, 0, tzinfo=timezone.utc)
        state.reset()
        assert state.suppression_windows == {}

    def test_reset_zeroes_stats_counters(self):
        state = AppState()
        state.stats.total_alerts_processed = 42
        state.stats.total_routed = 30
        state.stats.total_suppressed = 5
        state.stats.total_unrouted = 7
        state.reset()
        assert state.stats.total_alerts_processed == 0
        assert state.stats.total_routed == 0
        assert state.stats.total_suppressed == 0
        assert state.stats.total_unrouted == 0

    def test_reset_restores_by_severity_defaults(self):
        state = AppState()
        state.stats.by_severity["critical"] = 99
        state.reset()
        assert state.stats.by_severity == {"critical": 0, "warning": 0, "info": 0}

    def test_reset_clears_by_route(self):
        from app.models import RouteStats
        state = AppState()
        state.stats.by_route["route-1"] = RouteStats(total_matched=10)
        state.reset()
        assert state.stats.by_route == {}

    def test_reset_clears_by_service(self):
        state = AppState()
        state.stats.by_service["payment-api"] = 5
        state.reset()
        assert state.stats.by_service == {}

    def test_reset_is_idempotent(self):
        state = AppState()
        state.reset()
        state.reset()
        assert state.routes == {}
        assert state.stats.total_alerts_processed == 0

    def test_reset_creates_new_stats_instance(self):
        state = AppState()
        old_stats = state.stats
        state.reset()
        assert state.stats is not old_stats
