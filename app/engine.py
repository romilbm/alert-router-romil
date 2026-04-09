import fnmatch
from datetime import datetime, timezone

from app.models import Alert, AlertResult, AppState, EvaluationDetails, RouteConfig, RoutedTo


def matches_conditions(alert: Alert, route: RouteConfig) -> bool:
    """Return True if the alert satisfies all specified conditions on the route."""
    c = route.conditions

    if c.severity is not None:
        if alert.severity not in c.severity:
            return False

    if c.service is not None:
        if not any(fnmatch.fnmatch(alert.service, pattern) for pattern in c.service):
            return False

    if c.group is not None:
        if alert.group not in c.group:
            return False

    if c.labels is not None:
        for k, v in c.labels.items():
            if alert.labels.get(k) != v:
                return False

    return True


def evaluate_alert(alert: Alert, state: AppState, dry_run: bool = False) -> AlertResult:
    """
    Evaluate an alert against all routes and return an AlertResult.

    Steps:
      1. Gather all routes.
      2. Filter to those whose conditions match the alert.
      3. (active_hours check — deferred)
      4. Sort matching routes by priority descending.
      5. Winner = highest-priority match.
      6. (Suppression check — deferred)
      7. Build AlertResult.
      8. Persist state and update stats (skipped when dry_run=True).
    """
    all_routes = list(state.routes.values())
    total_evaluated = len(all_routes)

    matching_routes = [r for r in all_routes if matches_conditions(alert, r)]
    matching_routes.sort(key=lambda r: r.priority, reverse=True)

    matched_route_ids = [r.id for r in matching_routes]
    winner = matching_routes[0] if matching_routes else None

    routed_to = RoutedTo(route_id=winner.id, target=winner.target) if winner else None

    result = AlertResult(
        alert_id=alert.id,
        routed_to=routed_to,
        suppressed=False,
        suppression_reason=None,
        matched_routes=matched_route_ids,
        evaluation_details=EvaluationDetails(
            total_routes_evaluated=total_evaluated,
            routes_matched=len(matching_routes),
            routes_not_matched=total_evaluated - len(matching_routes),
            suppression_applied=False,
        ),
    )

    if not dry_run:
        state.alerts[alert.id] = result
        _update_stats(alert, result, winner, state)

    return result


def _update_stats(alert: Alert, result: AlertResult, winner, state: AppState) -> None:
    stats = state.stats

    stats.total_alerts_processed += 1
    stats.by_severity[alert.severity] = stats.by_severity.get(alert.severity, 0) + 1
    stats.by_service[alert.service] = stats.by_service.get(alert.service, 0) + 1

    if result.suppressed:
        stats.total_suppressed += 1
    elif winner is not None:
        stats.total_routed += 1
    else:
        stats.total_unrouted += 1

    if winner is not None:
        if winner.id not in stats.by_route:
            from app.models import RouteStats
            stats.by_route[winner.id] = RouteStats()
        route_stats = stats.by_route[winner.id]
        route_stats.total_matched += 1
        if result.suppressed:
            route_stats.total_suppressed += 1
        else:
            route_stats.total_routed += 1
