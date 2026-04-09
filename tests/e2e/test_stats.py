"""E2E tests for GET /stats."""
import pytest
from fastapi.testclient import TestClient


TS = "2026-03-25T14:00:00Z"


def post_route(client: TestClient, route_id: str = "r1", conditions: dict = None,
               priority: int = 10, window: int = 0):
    client.post("/routes", json={
        "id": route_id,
        "conditions": conditions or {},
        "target": {"type": "slack", "channel": f"#{route_id}"},
        "priority": priority,
        "suppression_window_seconds": window,
    })


def submit(client: TestClient, alert_id: str, service: str = "payment-api",
           severity: str = "critical") -> dict:
    return client.post("/alerts", json={
        "id": alert_id,
        "severity": severity,
        "service": service,
        "group": "backend",
        "timestamp": TS,
    }).json()


# ---------------------------------------------------------------------------
# Initial stats
# ---------------------------------------------------------------------------

class TestStatsInitial:
    def test_stats_endpoint_returns_200(self, client: TestClient):
        assert client.get("/stats").status_code == 200

    def test_initial_totals_are_zero(self, client: TestClient):
        body = client.get("/stats").json()
        assert body["total_alerts_processed"] == 0
        assert body["total_routed"] == 0
        assert body["total_suppressed"] == 0
        assert body["total_unrouted"] == 0

    def test_initial_by_severity_keys_present(self, client: TestClient):
        body = client.get("/stats").json()
        assert set(body["by_severity"].keys()) == {"critical", "warning", "info"}
        assert all(v == 0 for v in body["by_severity"].values())

    def test_initial_by_route_is_empty(self, client: TestClient):
        body = client.get("/stats").json()
        assert body["by_route"] == {}

    def test_initial_by_service_is_empty(self, client: TestClient):
        body = client.get("/stats").json()
        assert body["by_service"] == {}


# ---------------------------------------------------------------------------
# Stats after routing
# ---------------------------------------------------------------------------

class TestStatsAfterRouting:
    def test_total_alerts_processed_increments(self, client: TestClient):
        post_route(client)
        submit(client, "a1")
        submit(client, "a2")
        body = client.get("/stats").json()
        assert body["total_alerts_processed"] == 2

    def test_total_routed_increments(self, client: TestClient):
        post_route(client)
        submit(client, "a1")
        body = client.get("/stats").json()
        assert body["total_routed"] == 1

    def test_total_unrouted_increments_when_no_route_matches(self, client: TestClient):
        post_route(client, conditions={"severity": ["warning"]})
        submit(client, "a1", severity="critical")
        body = client.get("/stats").json()
        assert body["total_unrouted"] == 1

    def test_by_severity_critical_increments(self, client: TestClient):
        post_route(client)
        submit(client, "a1", severity="critical")
        body = client.get("/stats").json()
        assert body["by_severity"]["critical"] == 1

    def test_by_severity_warning_increments(self, client: TestClient):
        post_route(client)
        submit(client, "a1", severity="warning")
        body = client.get("/stats").json()
        assert body["by_severity"]["warning"] == 1

    def test_by_service_tracks_service(self, client: TestClient):
        post_route(client)
        submit(client, "a1", service="payment-api")
        submit(client, "a2", service="payment-api")
        body = client.get("/stats").json()
        assert body["by_service"]["payment-api"] == 2

    def test_by_route_tracks_winner(self, client: TestClient):
        post_route(client)
        submit(client, "a1")
        body = client.get("/stats").json()
        assert "r1" in body["by_route"]
        assert body["by_route"]["r1"]["total_matched"] == 1
        assert body["by_route"]["r1"]["total_routed"] == 1


# ---------------------------------------------------------------------------
# Stats with suppression
# ---------------------------------------------------------------------------

class TestStatsWithSuppression:
    def test_total_suppressed_increments(self, client: TestClient):
        post_route(client, window=300)
        submit(client, "a1")
        submit(client, "a2")
        body = client.get("/stats").json()
        assert body["total_suppressed"] == 1

    def test_total_routed_not_double_counted_when_suppressed(self, client: TestClient):
        post_route(client, window=300)
        submit(client, "a1")
        submit(client, "a2")
        body = client.get("/stats").json()
        assert body["total_routed"] == 1

    def test_by_route_total_suppressed(self, client: TestClient):
        post_route(client, window=300)
        submit(client, "a1")
        submit(client, "a2")
        body = client.get("/stats").json()
        rs = body["by_route"]["r1"]
        assert rs["total_matched"] == 2
        assert rs["total_routed"] == 1
        assert rs["total_suppressed"] == 1
