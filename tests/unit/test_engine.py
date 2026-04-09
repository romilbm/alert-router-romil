"""Unit tests for app/engine.py — condition matching and evaluate_alert."""
import pytest

from app.engine import evaluate_alert, matches_conditions
from app.models import Alert, AppState, Conditions, RouteConfig, Target


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_alert(**overrides) -> Alert:
    defaults = dict(
        id="alert-1",
        severity="critical",
        service="payment-api",
        group="backend",
        timestamp="2026-03-25T14:30:00Z",
        labels={"env": "production", "region": "us-east-1"},
    )
    return Alert(**{**defaults, **overrides})


def make_route(route_id="route-1", priority=10, **condition_overrides) -> RouteConfig:
    conditions = {k: v for k, v in condition_overrides.items()
                  if k in ("severity", "service", "group", "labels")}
    return RouteConfig(
        id=route_id,
        conditions=conditions,
        target={"type": "slack", "channel": "#oncall"},
        priority=priority,
    )


def fresh_state(*routes: RouteConfig) -> AppState:
    state = AppState()
    for r in routes:
        state.routes[r.id] = r
    return state


# ---------------------------------------------------------------------------
# matches_conditions — severity
# ---------------------------------------------------------------------------

class TestMatchesSeverity:
    def test_matching_severity(self):
        alert = make_alert(severity="critical")
        route = make_route(severity=["critical", "warning"])
        assert matches_conditions(alert, route) is True

    def test_non_matching_severity(self):
        alert = make_alert(severity="info")
        route = make_route(severity=["critical", "warning"])
        assert matches_conditions(alert, route) is False

    def test_omitted_severity_matches_all(self):
        for sev in ("critical", "warning", "info"):
            alert = make_alert(severity=sev)
            route = make_route()  # no severity condition
            assert matches_conditions(alert, route) is True


# ---------------------------------------------------------------------------
# matches_conditions — service (exact and glob)
# ---------------------------------------------------------------------------

class TestMatchesService:
    def test_exact_service_match(self):
        alert = make_alert(service="payment-api")
        route = make_route(service=["payment-api"])
        assert matches_conditions(alert, route) is True

    def test_exact_service_no_match(self):
        alert = make_alert(service="auth-service")
        route = make_route(service=["payment-api"])
        assert matches_conditions(alert, route) is False

    def test_glob_prefix_wildcard(self):
        alert = make_alert(service="payment-api")
        route = make_route(service=["payment-*"])
        assert matches_conditions(alert, route) is True

    def test_glob_prefix_wildcard_matches_worker(self):
        alert = make_alert(service="payment-worker")
        route = make_route(service=["payment-*"])
        assert matches_conditions(alert, route) is True

    def test_glob_prefix_wildcard_no_match(self):
        alert = make_alert(service="auth-service")
        route = make_route(service=["payment-*"])
        assert matches_conditions(alert, route) is False

    def test_glob_suffix_wildcard(self):
        alert = make_alert(service="payment-api")
        route = make_route(service=["*-api"])
        assert matches_conditions(alert, route) is True

    def test_glob_suffix_wildcard_matches_auth(self):
        alert = make_alert(service="auth-api")
        route = make_route(service=["*-api"])
        assert matches_conditions(alert, route) is True

    def test_glob_suffix_wildcard_no_match(self):
        alert = make_alert(service="payment-worker")
        route = make_route(service=["*-api"])
        assert matches_conditions(alert, route) is False

    def test_glob_any_wildcard(self):
        alert = make_alert(service="anything")
        route = make_route(service=["*"])
        assert matches_conditions(alert, route) is True

    def test_multiple_service_patterns_first_matches(self):
        alert = make_alert(service="payment-api")
        route = make_route(service=["auth-*", "payment-*"])
        assert matches_conditions(alert, route) is True

    def test_multiple_service_patterns_none_match(self):
        alert = make_alert(service="user-service")
        route = make_route(service=["auth-*", "payment-*"])
        assert matches_conditions(alert, route) is False

    def test_omitted_service_matches_all(self):
        for svc in ("payment-api", "auth-service", "anything"):
            alert = make_alert(service=svc)
            route = make_route()
            assert matches_conditions(alert, route) is True


# ---------------------------------------------------------------------------
# matches_conditions — group
# ---------------------------------------------------------------------------

class TestMatchesGroup:
    def test_matching_group(self):
        alert = make_alert(group="backend")
        route = make_route(group=["backend", "infrastructure"])
        assert matches_conditions(alert, route) is True

    def test_non_matching_group(self):
        alert = make_alert(group="frontend")
        route = make_route(group=["backend"])
        assert matches_conditions(alert, route) is False

    def test_omitted_group_matches_all(self):
        for grp in ("backend", "frontend", "infrastructure"):
            assert matches_conditions(make_alert(group=grp), make_route()) is True


# ---------------------------------------------------------------------------
# matches_conditions — labels
# ---------------------------------------------------------------------------

class TestMatchesLabels:
    def test_all_condition_labels_present(self):
        alert = make_alert(labels={"env": "production", "region": "us-east-1"})
        route = make_route(labels={"env": "production"})
        assert matches_conditions(alert, route) is True

    def test_extra_alert_labels_are_fine(self):
        alert = make_alert(labels={"env": "production", "team": "payments", "region": "us-east-1"})
        route = make_route(labels={"env": "production"})
        assert matches_conditions(alert, route) is True

    def test_missing_label_key_no_match(self):
        alert = make_alert(labels={"region": "us-east-1"})
        route = make_route(labels={"env": "production"})
        assert matches_conditions(alert, route) is False

    def test_wrong_label_value_no_match(self):
        alert = make_alert(labels={"env": "staging"})
        route = make_route(labels={"env": "production"})
        assert matches_conditions(alert, route) is False

    def test_multiple_condition_labels_all_must_match(self):
        alert = make_alert(labels={"env": "production", "region": "us-east-1"})
        route = make_route(labels={"env": "production", "region": "us-east-1"})
        assert matches_conditions(alert, route) is True

    def test_multiple_condition_labels_one_missing(self):
        alert = make_alert(labels={"env": "production"})
        route = make_route(labels={"env": "production", "region": "us-east-1"})
        assert matches_conditions(alert, route) is False

    def test_empty_alert_labels_no_match_when_condition_set(self):
        alert = make_alert(labels={})
        route = make_route(labels={"env": "production"})
        assert matches_conditions(alert, route) is False

    def test_omitted_labels_condition_matches_all(self):
        alert = make_alert(labels={"env": "production"})
        route = make_route()  # no labels condition
        assert matches_conditions(alert, route) is True


# ---------------------------------------------------------------------------
# matches_conditions — combined / empty conditions
# ---------------------------------------------------------------------------

class TestMatchesCombined:
    def test_empty_conditions_matches_any_alert(self):
        alert = make_alert()
        route = make_route()
        assert matches_conditions(alert, route) is True

    def test_all_conditions_must_match(self):
        alert = make_alert(severity="critical", service="payment-api", group="backend",
                           labels={"env": "production"})
        route = make_route(severity=["critical"], service=["payment-*"],
                           group=["backend"], labels={"env": "production"})
        assert matches_conditions(alert, route) is True

    def test_one_condition_fails_overall_fails(self):
        alert = make_alert(severity="info", service="payment-api", group="backend")
        route = make_route(severity=["critical"], service=["payment-*"], group=["backend"])
        assert matches_conditions(alert, route) is False


# ---------------------------------------------------------------------------
# evaluate_alert — basic routing
# ---------------------------------------------------------------------------

class TestEvaluateAlertRouting:
    def test_no_routes_returns_unrouted(self):
        alert = make_alert()
        result = evaluate_alert(alert, fresh_state())
        assert result.routed_to is None
        assert result.suppressed is False
        assert result.matched_routes == []

    def test_single_matching_route(self):
        alert = make_alert()
        route = make_route(severity=["critical"])
        result = evaluate_alert(alert, fresh_state(route))
        assert result.routed_to is not None
        assert result.routed_to.route_id == "route-1"
        assert result.suppressed is False

    def test_no_matching_route_returns_unrouted(self):
        alert = make_alert(severity="info")
        route = make_route(severity=["critical"])
        result = evaluate_alert(alert, fresh_state(route))
        assert result.routed_to is None

    def test_highest_priority_route_wins(self):
        alert = make_alert()
        low  = make_route("low",  priority=1)
        high = make_route("high", priority=99)
        mid  = make_route("mid",  priority=50)
        result = evaluate_alert(alert, fresh_state(low, high, mid))
        assert result.routed_to.route_id == "high"

    def test_all_matching_routes_in_matched_routes(self):
        alert = make_alert()
        r1 = make_route("r1", priority=10)
        r2 = make_route("r2", priority=5)
        r3 = make_route("r3", priority=20)
        result = evaluate_alert(alert, fresh_state(r1, r2, r3))
        assert set(result.matched_routes) == {"r1", "r2", "r3"}

    def test_matched_routes_ordered_by_priority_desc(self):
        alert = make_alert()
        r1 = make_route("r1", priority=10)
        r2 = make_route("r2", priority=5)
        r3 = make_route("r3", priority=20)
        result = evaluate_alert(alert, fresh_state(r1, r2, r3))
        assert result.matched_routes == ["r3", "r1", "r2"]

    def test_non_matching_routes_not_in_matched_routes(self):
        alert = make_alert(severity="info")
        r_match   = make_route("match",    severity=["info"])
        r_nomatch = make_route("no-match", severity=["critical"])
        result = evaluate_alert(alert, fresh_state(r_match, r_nomatch))
        assert result.matched_routes == ["match"]

    def test_routed_to_contains_target(self):
        alert = make_alert()
        route = make_route()
        result = evaluate_alert(alert, fresh_state(route))
        assert result.routed_to.target.type == "slack"
        assert result.routed_to.target.channel == "#oncall"


# ---------------------------------------------------------------------------
# evaluate_alert — evaluation_details counts
# ---------------------------------------------------------------------------

class TestEvaluationDetails:
    def test_total_routes_evaluated(self):
        alert = make_alert()
        routes = [make_route(f"r{i}", priority=i) for i in range(4)]
        result = evaluate_alert(alert, fresh_state(*routes))
        assert result.evaluation_details.total_routes_evaluated == 4

    def test_routes_matched_and_not_matched(self):
        alert = make_alert(severity="critical")
        match1 = make_route("m1", priority=10, severity=["critical"])
        match2 = make_route("m2", priority=5,  severity=["critical", "warning"])
        nomatch = make_route("n1", priority=1,  severity=["info"])
        result = evaluate_alert(alert, fresh_state(match1, match2, nomatch))
        assert result.evaluation_details.routes_matched == 2
        assert result.evaluation_details.routes_not_matched == 1
        assert result.evaluation_details.total_routes_evaluated == 3

    def test_unrouted_counts(self):
        alert = make_alert(severity="info")
        route = make_route(severity=["critical"])
        result = evaluate_alert(alert, fresh_state(route))
        assert result.evaluation_details.routes_matched == 0
        assert result.evaluation_details.routes_not_matched == 1

    def test_suppression_applied_false_when_no_suppression(self):
        result = evaluate_alert(make_alert(), fresh_state(make_route()))
        assert result.evaluation_details.suppression_applied is False


# ---------------------------------------------------------------------------
# evaluate_alert — state persistence and stats
# ---------------------------------------------------------------------------

class TestEvaluateAlertSideEffects:
    def test_alert_stored_in_state(self):
        state = fresh_state(make_route())
        alert = make_alert()
        evaluate_alert(alert, state)
        assert "alert-1" in state.alerts

    def test_alert_upsert_replaces_on_resubmit(self):
        state = fresh_state(make_route())
        evaluate_alert(make_alert(id="a1", severity="critical"), state)
        evaluate_alert(make_alert(id="a1", severity="warning"), state)
        assert len(state.alerts) == 1

    def test_dry_run_does_not_store_alert(self):
        state = fresh_state(make_route())
        evaluate_alert(make_alert(), state, dry_run=True)
        assert "alert-1" not in state.alerts

    def test_dry_run_does_not_update_stats(self):
        state = fresh_state(make_route())
        evaluate_alert(make_alert(), state, dry_run=True)
        assert state.stats.total_alerts_processed == 0

    def test_stats_total_alerts_processed(self):
        state = fresh_state(make_route())
        evaluate_alert(make_alert(id="a1"), state)
        evaluate_alert(make_alert(id="a2"), state)
        assert state.stats.total_alerts_processed == 2

    def test_stats_total_routed(self):
        state = fresh_state(make_route())
        evaluate_alert(make_alert(), state)
        assert state.stats.total_routed == 1

    def test_stats_total_unrouted(self):
        state = fresh_state()  # no routes
        evaluate_alert(make_alert(), state)
        assert state.stats.total_unrouted == 1

    def test_stats_by_severity(self):
        state = fresh_state(make_route())
        evaluate_alert(make_alert(id="a1", severity="critical"), state)
        evaluate_alert(make_alert(id="a2", severity="warning"), state)
        assert state.stats.by_severity["critical"] == 1
        assert state.stats.by_severity["warning"] == 1
        assert state.stats.by_severity["info"] == 0

    def test_stats_by_service(self):
        state = fresh_state(make_route())
        evaluate_alert(make_alert(id="a1", service="payment-api"), state)
        evaluate_alert(make_alert(id="a2", service="payment-api"), state)
        evaluate_alert(make_alert(id="a3", service="auth-service"), state)
        assert state.stats.by_service["payment-api"] == 2
        assert state.stats.by_service["auth-service"] == 1

    def test_stats_by_route(self):
        state = fresh_state(make_route("r1", priority=10))
        evaluate_alert(make_alert(id="a1"), state)
        evaluate_alert(make_alert(id="a2"), state)
        rs = state.stats.by_route["r1"]
        assert rs.total_matched == 2
        assert rs.total_routed == 2
        assert rs.total_suppressed == 0

    def test_stats_by_route_only_winner_tracked(self):
        state = fresh_state(
            make_route("high", priority=20),
            make_route("low",  priority=1),
        )
        evaluate_alert(make_alert(), state)
        assert "high" in state.stats.by_route
        assert "low" not in state.stats.by_route
