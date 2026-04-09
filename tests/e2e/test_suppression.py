"""E2E tests for suppression window logic via POST /alerts."""
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Base timestamp + offsets in seconds
T0 = "2026-03-25T14:30:00Z"   # +0s
T_100 = "2026-03-25T14:31:40Z"  # +100s  (within 300s window)
T_299 = "2026-03-25T14:34:59Z"  # +299s  (just inside window)
T_300 = "2026-03-25T14:35:00Z"  # +300s  (exactly at expiry — NOT suppressed)
T_301 = "2026-03-25T14:35:01Z"  # +301s  (just past expiry)


def post_route_with_window(client, window: int = 300):
    client.post("/routes", json={
        "id": "route-1",
        "conditions": {},
        "target": {"type": "slack", "channel": "#oncall"},
        "priority": 10,
        "suppression_window_seconds": window,
    })


def alert(alert_id: str, timestamp: str, service: str = "payment-api") -> dict:
    return {
        "id": alert_id,
        "severity": "critical",
        "service": service,
        "group": "backend",
        "timestamp": timestamp,
    }


# ---------------------------------------------------------------------------
# First alert routes normally
# ---------------------------------------------------------------------------

class TestFirstAlertRoutes:
    def test_first_alert_not_suppressed(self, client: TestClient):
        post_route_with_window(client, 300)
        resp = client.post("/alerts", json=alert("a1", T0))
        body = resp.json()
        assert body["suppressed"] is False
        assert body["routed_to"]["route_id"] == "route-1"

    def test_first_alert_has_no_suppression_reason(self, client: TestClient):
        post_route_with_window(client, 300)
        body = client.post("/alerts", json=alert("a1", T0)).json()
        assert body["suppression_reason"] is None

    def test_suppression_applied_false_for_first_alert(self, client: TestClient):
        post_route_with_window(client, 300)
        body = client.post("/alerts", json=alert("a1", T0)).json()
        assert body["evaluation_details"]["suppression_applied"] is False


# ---------------------------------------------------------------------------
# Second alert within window — suppressed
# ---------------------------------------------------------------------------

class TestAlertWithinWindowSuppressed:
    def test_second_alert_within_window_suppressed(self, client: TestClient):
        post_route_with_window(client, 300)
        client.post("/alerts", json=alert("a1", T0))
        body = client.post("/alerts", json=alert("a2", T_100)).json()
        assert body["suppressed"] is True

    def test_suppressed_alert_still_has_routed_to(self, client: TestClient):
        post_route_with_window(client, 300)
        client.post("/alerts", json=alert("a1", T0))
        body = client.post("/alerts", json=alert("a2", T_100)).json()
        assert body["routed_to"] is not None
        assert body["routed_to"]["route_id"] == "route-1"

    def test_suppressed_alert_has_suppression_reason(self, client: TestClient):
        post_route_with_window(client, 300)
        client.post("/alerts", json=alert("a1", T0))
        body = client.post("/alerts", json=alert("a2", T_100)).json()
        assert body["suppression_reason"] is not None
        assert "payment-api" in body["suppression_reason"]
        assert "route-1" in body["suppression_reason"]

    def test_suppression_reason_contains_expiry_time(self, client: TestClient):
        post_route_with_window(client, 300)
        client.post("/alerts", json=alert("a1", T0))
        body = client.post("/alerts", json=alert("a2", T_100)).json()
        # Expiry = T0 + 300s = 14:35:00Z
        assert "2026-03-25T14:35:00Z" in body["suppression_reason"]

    def test_suppression_applied_true_in_evaluation_details(self, client: TestClient):
        post_route_with_window(client, 300)
        client.post("/alerts", json=alert("a1", T0))
        body = client.post("/alerts", json=alert("a2", T_100)).json()
        assert body["evaluation_details"]["suppression_applied"] is True

    def test_alert_just_before_expiry_suppressed(self, client: TestClient):
        post_route_with_window(client, 300)
        client.post("/alerts", json=alert("a1", T0))
        body = client.post("/alerts", json=alert("a2", T_299)).json()
        assert body["suppressed"] is True

    def test_alert_at_exact_expiry_not_suppressed(self, client: TestClient):
        post_route_with_window(client, 300)
        client.post("/alerts", json=alert("a1", T0))
        body = client.post("/alerts", json=alert("a2", T_300)).json()
        assert body["suppressed"] is False

    def test_stored_suppressed_alert_retrievable(self, client: TestClient):
        post_route_with_window(client, 300)
        client.post("/alerts", json=alert("a1", T0))
        client.post("/alerts", json=alert("a2", T_100))
        body = client.get("/alerts/a2").json()
        assert body["suppressed"] is True


# ---------------------------------------------------------------------------
# Alert after window expiry — routes again
# ---------------------------------------------------------------------------

class TestAlertAfterWindowExpiry:
    def test_alert_after_expiry_routes_normally(self, client: TestClient):
        post_route_with_window(client, 300)
        client.post("/alerts", json=alert("a1", T0))
        body = client.post("/alerts", json=alert("a2", T_301)).json()
        assert body["suppressed"] is False
        assert body["routed_to"]["route_id"] == "route-1"

    def test_alert_after_expiry_resets_window(self, client: TestClient):
        post_route_with_window(client, 300)
        client.post("/alerts", json=alert("a1", T0))
        client.post("/alerts", json=alert("a2", T_301))   # resets window to T_301 + 300s
        # T_301 + 300s = 14:40:01Z; alert at 14:36:00Z (+360s from T0) is within new window
        t_within_new = "2026-03-25T14:36:00Z"
        body = client.post("/alerts", json=alert("a3", t_within_new)).json()
        assert body["suppressed"] is True

    def test_no_suppression_reason_after_expiry(self, client: TestClient):
        post_route_with_window(client, 300)
        client.post("/alerts", json=alert("a1", T0))
        body = client.post("/alerts", json=alert("a2", T_301)).json()
        assert body["suppression_reason"] is None


# ---------------------------------------------------------------------------
# Different service — not suppressed
# ---------------------------------------------------------------------------

class TestDifferentServiceNotSuppressed:
    def test_different_service_not_suppressed(self, client: TestClient):
        post_route_with_window(client, 300)
        client.post("/alerts", json=alert("a1", T0, service="payment-api"))
        body = client.post("/alerts", json=alert("a2", T_100, service="auth-service")).json()
        assert body["suppressed"] is False

    def test_different_service_sets_its_own_window(self, client: TestClient):
        post_route_with_window(client, 300)
        client.post("/alerts", json=alert("a1", T0, service="payment-api"))
        client.post("/alerts", json=alert("a2", T_100, service="auth-service"))
        # auth-service window = T_100 + 300s; alert within that window is suppressed
        t_within = "2026-03-25T14:33:00Z"  # +180s from T0 = +80s from T_100
        body = client.post("/alerts", json=alert("a3", t_within, service="auth-service")).json()
        assert body["suppressed"] is True

    def test_payment_api_still_suppressed_independently(self, client: TestClient):
        post_route_with_window(client, 300)
        client.post("/alerts", json=alert("a1", T0, service="payment-api"))
        client.post("/alerts", json=alert("a2", T_100, service="auth-service"))
        # payment-api is still suppressed in its own window
        body = client.post("/alerts", json=alert("a3", T_100, service="payment-api")).json()
        assert body["suppressed"] is True


# ---------------------------------------------------------------------------
# Zero suppression window — never suppresses
# ---------------------------------------------------------------------------

class TestZeroWindowNeverSuppresses:
    def test_zero_window_route_never_suppresses(self, client: TestClient):
        client.post("/routes", json={
            "id": "no-window",
            "conditions": {},
            "target": {"type": "slack", "channel": "#x"},
            "priority": 10,
            "suppression_window_seconds": 0,
        })
        client.post("/alerts", json=alert("a1", T0))
        body = client.post("/alerts", json=alert("a2", T_100)).json()
        assert body["suppressed"] is False
