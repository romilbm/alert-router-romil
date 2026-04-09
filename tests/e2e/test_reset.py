"""E2E tests for POST /reset."""
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


def submit(client: TestClient, alert_id: str = "a1") -> dict:
    return client.post("/alerts", json={
        "id": alert_id,
        "severity": "critical",
        "service": "payment-api",
        "group": "backend",
        "timestamp": TS,
    }).json()


# ---------------------------------------------------------------------------
# Reset endpoint basics
# ---------------------------------------------------------------------------

class TestResetEndpoint:
    def test_reset_returns_200(self, client: TestClient):
        assert client.post("/reset").status_code == 200

    def test_reset_returns_status_ok(self, client: TestClient):
        body = client.post("/reset").json()
        assert body == {"status": "ok"}

    def test_reset_is_idempotent(self, client: TestClient):
        client.post("/reset")
        body = client.post("/reset").json()
        assert body == {"status": "ok"}


# ---------------------------------------------------------------------------
# Reset clears routes
# ---------------------------------------------------------------------------

class TestResetClearsRoutes:
    def test_routes_empty_after_reset(self, client: TestClient):
        post_route(client)
        client.post("/reset")
        body = client.get("/routes").json()
        assert body["routes"] == []

    def test_route_not_found_after_reset(self, client: TestClient):
        post_route(client)
        client.post("/reset")
        resp = client.get("/routes")
        assert resp.json()["routes"] == []


# ---------------------------------------------------------------------------
# Reset clears alerts
# ---------------------------------------------------------------------------

class TestResetClearsAlerts:
    def test_alerts_empty_after_reset(self, client: TestClient):
        post_route(client)
        submit(client, "a1")
        client.post("/reset")
        body = client.get("/alerts").json()
        assert body["total"] == 0
        assert body["alerts"] == []

    def test_alert_not_found_after_reset(self, client: TestClient):
        post_route(client)
        submit(client, "a1")
        client.post("/reset")
        resp = client.get("/alerts/a1")
        assert resp.status_code == 404

    def test_filtering_returns_empty_after_reset(self, client: TestClient):
        post_route(client)
        submit(client, "a1")
        client.post("/reset")
        body = client.get("/alerts", params={"service": "payment-api"}).json()
        assert body["total"] == 0


# ---------------------------------------------------------------------------
# Reset clears stats
# ---------------------------------------------------------------------------

class TestResetClearsStats:
    def test_stats_zeroed_after_reset(self, client: TestClient):
        post_route(client)
        submit(client, "a1")
        submit(client, "a2")
        client.post("/reset")
        stats = client.get("/stats").json()
        assert stats["total_alerts_processed"] == 0
        assert stats["total_routed"] == 0

    def test_by_severity_reset_to_defaults(self, client: TestClient):
        post_route(client)
        submit(client, "a1")
        client.post("/reset")
        stats = client.get("/stats").json()
        assert stats["by_severity"]["critical"] == 0

    def test_by_route_cleared_after_reset(self, client: TestClient):
        post_route(client)
        submit(client, "a1")
        client.post("/reset")
        stats = client.get("/stats").json()
        assert stats["by_route"] == {}

    def test_by_service_cleared_after_reset(self, client: TestClient):
        post_route(client)
        submit(client, "a1")
        client.post("/reset")
        stats = client.get("/stats").json()
        assert stats["by_service"] == {}


# ---------------------------------------------------------------------------
# Reset clears suppression windows
# ---------------------------------------------------------------------------

class TestResetClearsSuppressionWindows:
    def test_suppression_window_cleared_after_reset(self, client: TestClient):
        client.post("/routes", json={
            "id": "r1", "conditions": {},
            "target": {"type": "slack", "channel": "#x"},
            "priority": 10, "suppression_window_seconds": 300,
        })
        submit(client, "a1")
        client.post("/reset")
        # Re-add route and submit — should NOT be suppressed
        client.post("/routes", json={
            "id": "r1", "conditions": {},
            "target": {"type": "slack", "channel": "#x"},
            "priority": 10, "suppression_window_seconds": 300,
        })
        body = submit(client, "a2")
        assert body["suppressed"] is False


# ---------------------------------------------------------------------------
# State functional after reset
# ---------------------------------------------------------------------------

class TestStateAfterReset:
    def test_can_add_routes_after_reset(self, client: TestClient):
        post_route(client)
        client.post("/reset")
        post_route(client, "new-route")
        body = client.get("/routes").json()
        assert len(body["routes"]) == 1
        assert body["routes"][0]["id"] == "new-route"

    def test_alerts_route_correctly_after_reset(self, client: TestClient):
        post_route(client)
        submit(client, "a1")
        client.post("/reset")
        post_route(client)
        body = submit(client, "a2")
        assert body["routed_to"]["route_id"] == "r1"

    def test_stats_accumulate_from_zero_after_reset(self, client: TestClient):
        post_route(client)
        submit(client, "a1")
        client.post("/reset")
        post_route(client)
        submit(client, "b1")
        submit(client, "b2")
        stats = client.get("/stats").json()
        assert stats["total_alerts_processed"] == 2
