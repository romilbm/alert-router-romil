"""E2E tests for GET /alerts with query-param filtering."""
import pytest
from fastapi.testclient import TestClient


TS = "2026-03-25T14:00:00Z"


def post_route(client: TestClient, route_id: str = "r1"):
    client.post("/routes", json={
        "id": route_id,
        "conditions": {},
        "target": {"type": "slack", "channel": f"#{route_id}"},
        "priority": 10,
    })


def submit_alert(client: TestClient, alert_id: str, service: str = "payment-api",
                 severity: str = "critical") -> dict:
    return client.post("/alerts", json={
        "id": alert_id,
        "severity": severity,
        "service": service,
        "group": "backend",
        "timestamp": TS,
    }).json()


# ---------------------------------------------------------------------------
# No filters — returns all
# ---------------------------------------------------------------------------

class TestNoFilter:
    def test_empty_store_returns_empty_list(self, client: TestClient):
        resp = client.get("/alerts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["alerts"] == []
        assert body["total"] == 0

    def test_returns_all_alerts_without_filter(self, client: TestClient):
        post_route(client)
        submit_alert(client, "a1")
        submit_alert(client, "a2")
        body = client.get("/alerts").json()
        assert body["total"] == 2
        ids = {a["alert_id"] for a in body["alerts"]}
        assert ids == {"a1", "a2"}


# ---------------------------------------------------------------------------
# Filter by service
# ---------------------------------------------------------------------------

class TestFilterByService:
    def test_filter_returns_only_matching_service(self, client: TestClient):
        post_route(client)
        submit_alert(client, "a1", service="payment-api")
        submit_alert(client, "a2", service="auth-service")
        body = client.get("/alerts", params={"service": "payment-api"}).json()
        assert body["total"] == 1
        assert body["alerts"][0]["alert_id"] == "a1"

    def test_filter_by_service_no_match(self, client: TestClient):
        post_route(client)
        submit_alert(client, "a1", service="payment-api")
        body = client.get("/alerts", params={"service": "unknown"}).json()
        assert body["total"] == 0
        assert body["alerts"] == []

    def test_filter_by_service_multiple_matches(self, client: TestClient):
        post_route(client)
        submit_alert(client, "a1", service="payment-api")
        submit_alert(client, "a2", service="payment-api")
        submit_alert(client, "a3", service="auth-service")
        body = client.get("/alerts", params={"service": "payment-api"}).json()
        assert body["total"] == 2


# ---------------------------------------------------------------------------
# Filter by severity
# ---------------------------------------------------------------------------

class TestFilterBySeverity:
    def test_filter_by_severity_critical(self, client: TestClient):
        post_route(client)
        submit_alert(client, "a1", severity="critical")
        submit_alert(client, "a2", severity="warning")
        body = client.get("/alerts", params={"severity": "critical"}).json()
        assert body["total"] == 1
        assert body["alerts"][0]["alert_id"] == "a1"

    def test_filter_by_severity_warning(self, client: TestClient):
        post_route(client)
        submit_alert(client, "a1", severity="critical")
        submit_alert(client, "a2", severity="warning")
        body = client.get("/alerts", params={"severity": "warning"}).json()
        assert body["total"] == 1
        assert body["alerts"][0]["alert_id"] == "a2"

    def test_filter_by_severity_no_match(self, client: TestClient):
        post_route(client)
        submit_alert(client, "a1", severity="critical")
        body = client.get("/alerts", params={"severity": "info"}).json()
        assert body["total"] == 0


# ---------------------------------------------------------------------------
# Filter by routed
# ---------------------------------------------------------------------------

class TestFilterByRouted:
    def test_filter_routed_true_returns_routed_alerts(self, client: TestClient):
        post_route(client)
        submit_alert(client, "a1")
        body = client.get("/alerts", params={"routed": "true"}).json()
        assert body["total"] == 1
        assert body["alerts"][0]["routed_to"] is not None

    def test_filter_routed_false_returns_unrouted_alerts(self, client: TestClient):
        client.post("/routes", json={
            "id": "r1",
            "conditions": {"severity": ["warning"]},
            "target": {"type": "slack", "channel": "#x"},
            "priority": 10,
        })
        submit_alert(client, "a1", severity="critical")
        body = client.get("/alerts", params={"routed": "false"}).json()
        assert body["total"] == 1
        assert body["alerts"][0]["routed_to"] is None

    def test_filter_routed_true_excludes_unrouted(self, client: TestClient):
        client.post("/routes", json={
            "id": "r1",
            "conditions": {"severity": ["warning"]},
            "target": {"type": "slack", "channel": "#x"},
            "priority": 10,
        })
        submit_alert(client, "a1", severity="critical")   # unrouted
        submit_alert(client, "a2", severity="warning")    # routed
        body = client.get("/alerts", params={"routed": "true"}).json()
        assert body["total"] == 1
        assert body["alerts"][0]["alert_id"] == "a2"


# ---------------------------------------------------------------------------
# Filter by suppressed
# ---------------------------------------------------------------------------

class TestFilterBySuppressed:
    def test_filter_suppressed_true(self, client: TestClient):
        client.post("/routes", json={
            "id": "r1", "conditions": {},
            "target": {"type": "slack", "channel": "#x"},
            "priority": 10, "suppression_window_seconds": 300,
        })
        submit_alert(client, "a1")
        submit_alert(client, "a2")
        body = client.get("/alerts", params={"suppressed": "true"}).json()
        assert body["total"] == 1
        assert body["alerts"][0]["alert_id"] == "a2"

    def test_filter_suppressed_false(self, client: TestClient):
        client.post("/routes", json={
            "id": "r1", "conditions": {},
            "target": {"type": "slack", "channel": "#x"},
            "priority": 10, "suppression_window_seconds": 300,
        })
        submit_alert(client, "a1")
        submit_alert(client, "a2")
        body = client.get("/alerts", params={"suppressed": "false"}).json()
        assert body["total"] == 1
        assert body["alerts"][0]["alert_id"] == "a1"


# ---------------------------------------------------------------------------
# Combined filters
# ---------------------------------------------------------------------------

class TestCombinedFilters:
    def test_service_and_severity_combined(self, client: TestClient):
        post_route(client)
        submit_alert(client, "a1", service="payment-api", severity="critical")
        submit_alert(client, "a2", service="payment-api", severity="warning")
        submit_alert(client, "a3", service="auth-service", severity="critical")
        body = client.get("/alerts", params={"service": "payment-api", "severity": "critical"}).json()
        assert body["total"] == 1
        assert body["alerts"][0]["alert_id"] == "a1"

    def test_service_and_routed_combined(self, client: TestClient):
        post_route(client)
        submit_alert(client, "a1", service="payment-api")
        submit_alert(client, "a2", service="auth-service")
        body = client.get("/alerts", params={"service": "payment-api", "routed": "true"}).json()
        assert body["total"] == 1
        assert body["alerts"][0]["alert_id"] == "a1"
