"""E2E tests for active_hours timezone-aware matching via POST /alerts."""
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
#
# America/New_York on 2026-03-25 = UTC-4 (DST sprang forward March 8 2026)
# So window 09:00–17:00 ET  =  13:00–21:00 UTC
#
# UTC timestamps used:
#   INSIDE_ET_WINDOW   = 14:00 UTC = 10:00 ET ✓
#   BEFORE_ET_WINDOW   = 12:59 UTC = 08:59 ET ✗
#   AT_ET_START        = 13:00 UTC = 09:00 ET ✓ (inclusive)
#   AT_ET_END          = 21:00 UTC = 17:00 ET ✗ (exclusive)
#   AFTER_ET_WINDOW    = 22:00 UTC = 18:00 ET ✗

INSIDE_ET     = "2026-03-25T14:00:00Z"
BEFORE_ET     = "2026-03-25T12:59:00Z"
AT_ET_START   = "2026-03-25T13:00:00Z"
AT_ET_END     = "2026-03-25T21:00:00Z"
AFTER_ET      = "2026-03-25T22:00:00Z"

# Midnight-crossing UTC window 22:00–06:00
MIDNIGHT_IN_BEFORE = "2026-03-25T23:30:00Z"   # 23:30 UTC — within [22:00, 06:00)
MIDNIGHT_IN_AFTER  = "2026-03-26T03:00:00Z"   # 03:00 UTC next day — within
MIDNIGHT_OUT       = "2026-03-25T08:00:00Z"   # 08:00 UTC — outside


def post_route(client, route_id, active_hours, priority=10):
    client.post("/routes", json={
        "id": route_id,
        "conditions": {},
        "target": {"type": "slack", "channel": f"#{route_id}"},
        "priority": priority,
        "active_hours": active_hours,
    })


def alert(alert_id: str, timestamp: str) -> dict:
    return {
        "id": alert_id,
        "severity": "critical",
        "service": "payment-api",
        "group": "backend",
        "timestamp": timestamp,
    }


# ---------------------------------------------------------------------------
# Route without active_hours — always active
# ---------------------------------------------------------------------------

class TestNoActiveHours:
    def test_route_without_active_hours_always_matches(self, client: TestClient):
        client.post("/routes", json={
            "id": "always", "conditions": {},
            "target": {"type": "slack", "channel": "#x"}, "priority": 10,
        })
        for ts in (INSIDE_ET, BEFORE_ET, AFTER_ET, MIDNIGHT_OUT):
            body = client.post("/alerts", json=alert("a", ts)).json()
            assert body["routed_to"]["route_id"] == "always"


# ---------------------------------------------------------------------------
# America/New_York window (09:00–17:00 ET)
# ---------------------------------------------------------------------------

class TestAmericaNewYorkWindow:
    AH = {"timezone": "America/New_York", "start": "09:00", "end": "17:00"}

    def test_alert_inside_window_routes(self, client: TestClient):
        post_route(client, "r1", self.AH)
        body = client.post("/alerts", json=alert("a1", INSIDE_ET)).json()
        assert body["routed_to"]["route_id"] == "r1"

    def test_alert_before_window_unrouted(self, client: TestClient):
        post_route(client, "r1", self.AH)
        body = client.post("/alerts", json=alert("a1", BEFORE_ET)).json()
        assert body["routed_to"] is None

    def test_alert_at_start_boundary_routes(self, client: TestClient):
        post_route(client, "r1", self.AH)
        body = client.post("/alerts", json=alert("a1", AT_ET_START)).json()
        assert body["routed_to"]["route_id"] == "r1"

    def test_alert_at_end_boundary_not_routed(self, client: TestClient):
        post_route(client, "r1", self.AH)
        body = client.post("/alerts", json=alert("a1", AT_ET_END)).json()
        assert body["routed_to"] is None

    def test_alert_after_window_unrouted(self, client: TestClient):
        post_route(client, "r1", self.AH)
        body = client.post("/alerts", json=alert("a1", AFTER_ET)).json()
        assert body["routed_to"] is None

    def test_outside_active_hours_counted_as_not_matched(self, client: TestClient):
        post_route(client, "r1", self.AH)
        body = client.post("/alerts", json=alert("a1", BEFORE_ET)).json()
        assert body["evaluation_details"]["routes_matched"] == 0
        assert body["evaluation_details"]["routes_not_matched"] == 1
        assert body["matched_routes"] == []


# ---------------------------------------------------------------------------
# Inactive route falls back to lower-priority always-active route
# ---------------------------------------------------------------------------

class TestFallbackToAlwaysActiveRoute:
    AH = {"timezone": "America/New_York", "start": "09:00", "end": "17:00"}

    def test_outside_window_falls_back_to_lower_priority_route(self, client: TestClient):
        post_route(client, "hours-only", self.AH, priority=100)
        client.post("/routes", json={
            "id": "always-on", "conditions": {},
            "target": {"type": "email", "address": "ops@example.com"},
            "priority": 1,
        })
        # BEFORE_ET = 08:59 ET — outside hours-only window
        body = client.post("/alerts", json=alert("a1", BEFORE_ET)).json()
        assert body["routed_to"]["route_id"] == "always-on"

    def test_inside_window_uses_higher_priority_route(self, client: TestClient):
        post_route(client, "hours-only", self.AH, priority=100)
        client.post("/routes", json={
            "id": "always-on", "conditions": {},
            "target": {"type": "email", "address": "ops@example.com"},
            "priority": 1,
        })
        body = client.post("/alerts", json=alert("a1", INSIDE_ET)).json()
        assert body["routed_to"]["route_id"] == "hours-only"

    def test_matched_routes_excludes_inactive_route(self, client: TestClient):
        post_route(client, "hours-only", self.AH, priority=100)
        client.post("/routes", json={
            "id": "always-on", "conditions": {},
            "target": {"type": "email", "address": "ops@example.com"},
            "priority": 1,
        })
        body = client.post("/alerts", json=alert("a1", BEFORE_ET)).json()
        assert "hours-only" not in body["matched_routes"]
        assert "always-on" in body["matched_routes"]


# ---------------------------------------------------------------------------
# Midnight-crossing window (22:00–06:00 UTC)
# ---------------------------------------------------------------------------

class TestMidnightCrossingWindow:
    AH = {"timezone": "UTC", "start": "22:00", "end": "06:00"}

    def test_alert_before_midnight_in_window(self, client: TestClient):
        post_route(client, "r1", self.AH)
        body = client.post("/alerts", json=alert("a1", MIDNIGHT_IN_BEFORE)).json()
        assert body["routed_to"]["route_id"] == "r1"

    def test_alert_after_midnight_in_window(self, client: TestClient):
        post_route(client, "r1", self.AH)
        body = client.post("/alerts", json=alert("a1", MIDNIGHT_IN_AFTER)).json()
        assert body["routed_to"]["route_id"] == "r1"

    def test_alert_outside_midnight_window(self, client: TestClient):
        post_route(client, "r1", self.AH)
        body = client.post("/alerts", json=alert("a1", MIDNIGHT_OUT)).json()
        assert body["routed_to"] is None

    def test_alert_at_midnight_window_end_excluded(self, client: TestClient):
        post_route(client, "r1", self.AH)
        body = client.post("/alerts", json=alert("a1", "2026-03-26T06:00:00Z")).json()
        assert body["routed_to"] is None
