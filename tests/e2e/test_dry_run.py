"""E2E tests for POST /test (dry-run)."""
import pytest
from fastapi.testclient import TestClient


TS = "2026-03-25T14:00:00Z"
TS2 = "2026-03-25T14:01:00Z"


def post_route(client: TestClient, route_id: str = "r1", window: int = 0):
    client.post("/routes", json={
        "id": route_id,
        "conditions": {},
        "target": {"type": "slack", "channel": f"#{route_id}"},
        "priority": 10,
        "suppression_window_seconds": window,
    })


def dry_run(client: TestClient, alert_id: str = "a1", service: str = "payment-api",
            severity: str = "critical", ts: str = TS) -> dict:
    return client.post("/test", json={
        "id": alert_id,
        "severity": severity,
        "service": service,
        "group": "backend",
        "timestamp": ts,
    }).json()


def submit(client: TestClient, alert_id: str, ts: str = TS) -> dict:
    return client.post("/alerts", json={
        "id": alert_id,
        "severity": "critical",
        "service": "payment-api",
        "group": "backend",
        "timestamp": ts,
    }).json()


# ---------------------------------------------------------------------------
# Dry-run returns correct result
# ---------------------------------------------------------------------------

class TestDryRunResult:
    def test_dry_run_returns_200(self, client: TestClient):
        post_route(client)
        assert client.post("/test", json={
            "id": "a1", "severity": "critical", "service": "payment-api",
            "group": "backend", "timestamp": TS,
        }).status_code == 200

    def test_dry_run_returns_alert_result_shape(self, client: TestClient):
        post_route(client)
        body = dry_run(client)
        assert "alert_id" in body
        assert "routed_to" in body
        assert "suppressed" in body
        assert "matched_routes" in body
        assert "evaluation_details" in body

    def test_dry_run_routes_to_correct_route(self, client: TestClient):
        post_route(client)
        body = dry_run(client)
        assert body["routed_to"]["route_id"] == "r1"

    def test_dry_run_unrouted_when_no_routes(self, client: TestClient):
        body = dry_run(client)
        assert body["routed_to"] is None

    def test_dry_run_validation_error_returns_400(self, client: TestClient):
        resp = client.post("/test", json={
            "id": "a1", "severity": "bad", "service": "s",
            "group": "g", "timestamp": TS,
        })
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Dry-run does not mutate state
# ---------------------------------------------------------------------------

class TestDryRunNoStateMutation:
    def test_dry_run_alert_not_stored(self, client: TestClient):
        post_route(client)
        dry_run(client, alert_id="dry-1")
        resp = client.get("/alerts/dry-1")
        assert resp.status_code == 404

    def test_dry_run_not_in_get_alerts_list(self, client: TestClient):
        post_route(client)
        dry_run(client, alert_id="dry-1")
        body = client.get("/alerts").json()
        assert body["total"] == 0

    def test_dry_run_does_not_update_stats(self, client: TestClient):
        post_route(client)
        dry_run(client)
        stats = client.get("/stats").json()
        assert stats["total_alerts_processed"] == 0

    def test_dry_run_does_not_set_suppression_window(self, client: TestClient):
        post_route(client, window=300)
        dry_run(client, alert_id="a1", ts=TS)
        # A real alert at the same timestamp should NOT be suppressed
        body = submit(client, "a2", ts=TS)
        assert body["suppressed"] is False

    def test_multiple_dry_runs_do_not_interfere(self, client: TestClient):
        post_route(client)
        dry_run(client, alert_id="d1")
        dry_run(client, alert_id="d2")
        dry_run(client, alert_id="d3")
        body = client.get("/alerts").json()
        assert body["total"] == 0
        stats = client.get("/stats").json()
        assert stats["total_alerts_processed"] == 0


# ---------------------------------------------------------------------------
# Dry-run reads existing suppression state
# ---------------------------------------------------------------------------

class TestDryRunReadsSuppressionState:
    def test_dry_run_within_window_reports_suppressed(self, client: TestClient):
        post_route(client, window=300)
        submit(client, "a1", ts=TS)
        body = dry_run(client, alert_id="dry", ts=TS2)
        assert body["suppressed"] is True

    def test_dry_run_outside_window_not_suppressed(self, client: TestClient):
        post_route(client, window=60)
        submit(client, "a1", ts=TS)
        # 10 minutes later — window expired
        body = dry_run(client, alert_id="dry", ts="2026-03-25T14:40:00Z")
        assert body["suppressed"] is False
