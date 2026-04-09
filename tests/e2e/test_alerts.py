"""E2E tests for POST /alerts and GET /alerts/{id} — basic routing."""
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def post_route(client, route_id, priority, conditions=None, target=None):
    return client.post("/routes", json={
        "id": route_id,
        "conditions": conditions or {},
        "target": target or {"type": "slack", "channel": f"#{route_id}"},
        "priority": priority,
    })


ALERT = {
    "id": "alert-1",
    "severity": "critical",
    "service": "payment-api",
    "group": "backend",
    "timestamp": "2026-03-25T14:30:00Z",
    "labels": {"env": "production"},
}


# ---------------------------------------------------------------------------
# POST /alerts — basic routing
# ---------------------------------------------------------------------------

class TestPostAlerts:
    def test_returns_200(self, client: TestClient):
        post_route(client, "r1", 10)
        resp = client.post("/alerts", json=ALERT)
        assert resp.status_code == 200

    def test_alert_routed_to_matching_route(self, client: TestClient):
        post_route(client, "r1", 10, conditions={"severity": ["critical"]})
        resp = client.post("/alerts", json=ALERT)
        body = resp.json()
        assert body["routed_to"]["route_id"] == "r1"
        assert body["suppressed"] is False

    def test_unrouted_when_no_routes(self, client: TestClient):
        resp = client.post("/alerts", json=ALERT)
        body = resp.json()
        assert body["routed_to"] is None
        assert body["suppressed"] is False
        assert body["matched_routes"] == []

    def test_unrouted_when_no_conditions_match(self, client: TestClient):
        post_route(client, "r1", 10, conditions={"severity": ["info"]})
        resp = client.post("/alerts", json=ALERT)
        assert resp.json()["routed_to"] is None

    def test_highest_priority_route_wins(self, client: TestClient):
        post_route(client, "low",  priority=1)
        post_route(client, "high", priority=99)
        post_route(client, "mid",  priority=50)
        body = client.post("/alerts", json=ALERT).json()
        assert body["routed_to"]["route_id"] == "high"

    def test_all_matching_routes_in_matched_routes(self, client: TestClient):
        post_route(client, "r1", 10)
        post_route(client, "r2", 5)
        post_route(client, "r3", 20)
        body = client.post("/alerts", json=ALERT).json()
        assert set(body["matched_routes"]) == {"r1", "r2", "r3"}

    def test_non_matching_routes_not_in_matched_routes(self, client: TestClient):
        post_route(client, "match",    10, conditions={"severity": ["critical"]})
        post_route(client, "no-match",  5, conditions={"severity": ["info"]})
        body = client.post("/alerts", json=ALERT).json()
        assert body["matched_routes"] == ["match"]

    def test_alert_id_in_response(self, client: TestClient):
        body = client.post("/alerts", json=ALERT).json()
        assert body["alert_id"] == "alert-1"

    def test_target_included_in_routed_to(self, client: TestClient):
        post_route(client, "r1", 10, target={"type": "slack", "channel": "#alerts"})
        body = client.post("/alerts", json=ALERT).json()
        assert body["routed_to"]["target"]["type"] == "slack"
        assert body["routed_to"]["target"]["channel"] == "#alerts"


# ---------------------------------------------------------------------------
# POST /alerts — evaluation_details
# ---------------------------------------------------------------------------

class TestEvaluationDetails:
    def test_total_routes_evaluated(self, client: TestClient):
        for i in range(3):
            post_route(client, f"r{i}", i)
        body = client.post("/alerts", json=ALERT).json()
        assert body["evaluation_details"]["total_routes_evaluated"] == 3

    def test_routes_matched_count(self, client: TestClient):
        post_route(client, "m1", 10, conditions={"severity": ["critical"]})
        post_route(client, "m2",  5, conditions={"severity": ["critical", "warning"]})
        post_route(client, "n1",  1, conditions={"severity": ["info"]})
        body = client.post("/alerts", json=ALERT).json()
        assert body["evaluation_details"]["routes_matched"] == 2
        assert body["evaluation_details"]["routes_not_matched"] == 1

    def test_suppression_applied_false(self, client: TestClient):
        post_route(client, "r1", 10)
        body = client.post("/alerts", json=ALERT).json()
        assert body["evaluation_details"]["suppression_applied"] is False

    def test_unrouted_evaluation_details(self, client: TestClient):
        post_route(client, "r1", 10, conditions={"severity": ["info"]})
        body = client.post("/alerts", json=ALERT).json()
        ed = body["evaluation_details"]
        assert ed["routes_matched"] == 0
        assert ed["routes_not_matched"] == 1
        assert ed["total_routes_evaluated"] == 1


# ---------------------------------------------------------------------------
# POST /alerts — condition matching
# ---------------------------------------------------------------------------

class TestAlertConditionMatching:
    def test_severity_filter(self, client: TestClient):
        post_route(client, "crit-only", 10, conditions={"severity": ["critical"]})
        assert client.post("/alerts", json=ALERT).json()["routed_to"]["route_id"] == "crit-only"
        info_alert = {**ALERT, "id": "a2", "severity": "info"}
        assert client.post("/alerts", json=info_alert).json()["routed_to"] is None

    def test_group_filter(self, client: TestClient):
        post_route(client, "backend-only", 10, conditions={"group": ["backend"]})
        assert client.post("/alerts", json=ALERT).json()["routed_to"]["route_id"] == "backend-only"
        frontend = {**ALERT, "id": "a2", "group": "frontend"}
        assert client.post("/alerts", json=frontend).json()["routed_to"] is None

    def test_service_exact_match(self, client: TestClient):
        post_route(client, "r1", 10, conditions={"service": ["payment-api"]})
        assert client.post("/alerts", json=ALERT).json()["routed_to"]["route_id"] == "r1"
        other = {**ALERT, "id": "a2", "service": "auth-service"}
        assert client.post("/alerts", json=other).json()["routed_to"] is None

    def test_service_glob_match(self, client: TestClient):
        post_route(client, "pay-routes", 10, conditions={"service": ["payment-*"]})
        assert client.post("/alerts", json=ALERT).json()["routed_to"]["route_id"] == "pay-routes"
        worker = {**ALERT, "id": "a2", "service": "payment-worker"}
        assert client.post("/alerts", json=worker).json()["routed_to"]["route_id"] == "pay-routes"
        other = {**ALERT, "id": "a3", "service": "auth-service"}
        assert client.post("/alerts", json=other).json()["routed_to"] is None

    def test_label_filter(self, client: TestClient):
        post_route(client, "prod-only", 10, conditions={"labels": {"env": "production"}})
        assert client.post("/alerts", json=ALERT).json()["routed_to"]["route_id"] == "prod-only"
        staging = {**ALERT, "id": "a2", "labels": {"env": "staging"}}
        assert client.post("/alerts", json=staging).json()["routed_to"] is None

    def test_extra_alert_labels_still_match(self, client: TestClient):
        post_route(client, "r1", 10, conditions={"labels": {"env": "production"}})
        rich_labels = {**ALERT, "labels": {"env": "production", "team": "payments", "region": "us-east-1"}}
        assert client.post("/alerts", json=rich_labels).json()["routed_to"]["route_id"] == "r1"

    def test_empty_conditions_is_catch_all(self, client: TestClient):
        post_route(client, "catch-all", 10, conditions={})
        for sev in ("critical", "warning", "info"):
            a = {**ALERT, "id": f"a-{sev}", "severity": sev}
            assert client.post("/alerts", json=a).json()["routed_to"]["route_id"] == "catch-all"


# ---------------------------------------------------------------------------
# POST /alerts — re-submission (upsert)
# ---------------------------------------------------------------------------

class TestAlertResubmission:
    def test_resubmit_same_id_updates_result(self, client: TestClient):
        post_route(client, "r1", 10, conditions={"severity": ["critical"]})
        client.post("/alerts", json=ALERT)
        # Remove route and re-submit — should now be unrouted
        client.delete("/routes/r1")
        client.post("/alerts", json=ALERT)
        result = client.get("/alerts/alert-1").json()
        assert result["routed_to"] is None

    def test_only_one_alert_stored_after_resubmit(self, client: TestClient):
        client.post("/alerts", json=ALERT)
        client.post("/alerts", json=ALERT)
        # GET should return the latest result without error
        assert client.get("/alerts/alert-1").status_code == 200


# ---------------------------------------------------------------------------
# POST /alerts — validation
# ---------------------------------------------------------------------------

class TestAlertValidation:
    def test_missing_severity_returns_400(self, client: TestClient):
        body = {k: v for k, v in ALERT.items() if k != "severity"}
        assert client.post("/alerts", json=body).status_code == 400

    def test_invalid_severity_returns_400(self, client: TestClient):
        assert client.post("/alerts", json={**ALERT, "severity": "urgent"}).status_code == 400

    def test_missing_service_returns_400(self, client: TestClient):
        body = {k: v for k, v in ALERT.items() if k != "service"}
        assert client.post("/alerts", json=body).status_code == 400

    def test_missing_timestamp_returns_400(self, client: TestClient):
        body = {k: v for k, v in ALERT.items() if k != "timestamp"}
        assert client.post("/alerts", json=body).status_code == 400

    def test_naive_timestamp_returns_400(self, client: TestClient):
        assert client.post("/alerts", json={**ALERT, "timestamp": "2026-03-25T14:30:00"}).status_code == 400

    def test_invalid_timestamp_returns_400(self, client: TestClient):
        assert client.post("/alerts", json={**ALERT, "timestamp": "not-a-date"}).status_code == 400


# ---------------------------------------------------------------------------
# GET /alerts/{id}
# ---------------------------------------------------------------------------

class TestGetAlert:
    def test_get_stored_alert(self, client: TestClient):
        post_route(client, "r1", 10)
        client.post("/alerts", json=ALERT)
        resp = client.get("/alerts/alert-1")
        assert resp.status_code == 200
        assert resp.json()["alert_id"] == "alert-1"

    def test_get_alert_response_matches_post_response(self, client: TestClient):
        post_route(client, "r1", 10)
        post_resp = client.post("/alerts", json=ALERT).json()
        get_resp  = client.get("/alerts/alert-1").json()
        assert post_resp == get_resp

    def test_get_missing_alert_returns_404(self, client: TestClient):
        resp = client.get("/alerts/does-not-exist")
        assert resp.status_code == 404
        assert resp.json() == {"error": "alert not found"}
