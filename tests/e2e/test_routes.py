"""E2E tests for POST /routes, GET /routes, DELETE /routes/{id}."""
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SLACK_ROUTE = {
    "id": "route-1",
    "conditions": {"severity": ["critical"], "group": ["backend"]},
    "target": {"type": "slack", "channel": "#oncall"},
    "priority": 10,
}


# ---------------------------------------------------------------------------
# POST /routes — create
# ---------------------------------------------------------------------------

class TestCreateRoute:
    def test_create_returns_201(self, client: TestClient):
        resp = client.post("/routes", json=SLACK_ROUTE)
        assert resp.status_code == 201

    def test_create_returns_id_and_created_true(self, client: TestClient):
        resp = client.post("/routes", json=SLACK_ROUTE)
        assert resp.json() == {"id": "route-1", "created": True}

    def test_create_multiple_routes(self, client: TestClient):
        client.post("/routes", json=SLACK_ROUTE)
        r2 = {**SLACK_ROUTE, "id": "route-2", "priority": 5}
        resp = client.post("/routes", json=r2)
        assert resp.status_code == 201
        assert resp.json() == {"id": "route-2", "created": True}

    def test_create_route_with_all_fields(self, client: TestClient):
        route = {
            "id": "route-full",
            "conditions": {
                "severity": ["critical", "warning"],
                "service": ["payment-*"],
                "group": ["backend"],
                "labels": {"env": "production"},
            },
            "target": {"type": "slack", "channel": "#alerts"},
            "priority": 20,
            "suppression_window_seconds": 300,
            "active_hours": {
                "timezone": "America/New_York",
                "start": "09:00",
                "end": "17:00",
            },
        }
        resp = client.post("/routes", json=route)
        assert resp.status_code == 201
        assert resp.json()["created"] is True

    def test_create_route_with_empty_conditions(self, client: TestClient):
        route = {**SLACK_ROUTE, "id": "catch-all", "conditions": {}}
        resp = client.post("/routes", json=route)
        assert resp.status_code == 201

    def test_create_email_target(self, client: TestClient):
        route = {**SLACK_ROUTE, "id": "email-route", "target": {"type": "email", "address": "ops@example.com"}}
        resp = client.post("/routes", json=route)
        assert resp.status_code == 201

    def test_create_pagerduty_target(self, client: TestClient):
        route = {**SLACK_ROUTE, "id": "pd-route", "target": {"type": "pagerduty", "service_key": "abc123"}}
        resp = client.post("/routes", json=route)
        assert resp.status_code == 201

    def test_create_webhook_target(self, client: TestClient):
        route = {**SLACK_ROUTE, "id": "wh-route", "target": {"type": "webhook", "url": "https://hooks.example.com"}}
        resp = client.post("/routes", json=route)
        assert resp.status_code == 201

    def test_create_webhook_with_headers(self, client: TestClient):
        route = {**SLACK_ROUTE, "id": "wh-route", "target": {
            "type": "webhook", "url": "https://hooks.example.com", "headers": {"X-Token": "secret"}
        }}
        resp = client.post("/routes", json=route)
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# POST /routes — update (re-POST with existing ID)
# ---------------------------------------------------------------------------

class TestUpdateRoute:
    def test_repost_existing_id_returns_created_false(self, client: TestClient):
        client.post("/routes", json=SLACK_ROUTE)
        resp = client.post("/routes", json=SLACK_ROUTE)
        assert resp.status_code == 201
        assert resp.json() == {"id": "route-1", "created": False}

    def test_repost_replaces_route_data(self, client: TestClient):
        client.post("/routes", json=SLACK_ROUTE)
        updated = {**SLACK_ROUTE, "priority": 99}
        client.post("/routes", json=updated)
        routes = client.get("/routes").json()["routes"]
        assert routes[0]["priority"] == 99

    def test_repost_only_one_route_stored(self, client: TestClient):
        client.post("/routes", json=SLACK_ROUTE)
        client.post("/routes", json=SLACK_ROUTE)
        assert len(client.get("/routes").json()["routes"]) == 1


# ---------------------------------------------------------------------------
# GET /routes
# ---------------------------------------------------------------------------

class TestListRoutes:
    def test_empty_list_on_fresh_state(self, client: TestClient):
        resp = client.get("/routes")
        assert resp.status_code == 200
        assert resp.json() == {"routes": []}

    def test_lists_created_route(self, client: TestClient):
        client.post("/routes", json=SLACK_ROUTE)
        resp = client.get("/routes")
        assert resp.status_code == 200
        routes = resp.json()["routes"]
        assert len(routes) == 1
        assert routes[0]["id"] == "route-1"

    def test_lists_multiple_routes(self, client: TestClient):
        client.post("/routes", json=SLACK_ROUTE)
        client.post("/routes", json={**SLACK_ROUTE, "id": "route-2", "priority": 5})
        routes = client.get("/routes").json()["routes"]
        assert len(routes) == 2
        ids = {r["id"] for r in routes}
        assert ids == {"route-1", "route-2"}

    def test_route_fields_are_preserved(self, client: TestClient):
        client.post("/routes", json=SLACK_ROUTE)
        route = client.get("/routes").json()["routes"][0]
        assert route["priority"] == 10
        assert route["target"]["type"] == "slack"
        assert route["target"]["channel"] == "#oncall"
        assert route["conditions"]["severity"] == ["critical"]


# ---------------------------------------------------------------------------
# DELETE /routes/{id}
# ---------------------------------------------------------------------------

class TestDeleteRoute:
    def test_delete_existing_route(self, client: TestClient):
        client.post("/routes", json=SLACK_ROUTE)
        resp = client.delete("/routes/route-1")
        assert resp.status_code == 200
        assert resp.json() == {"id": "route-1", "deleted": True}

    def test_delete_removes_route_from_list(self, client: TestClient):
        client.post("/routes", json=SLACK_ROUTE)
        client.delete("/routes/route-1")
        assert client.get("/routes").json()["routes"] == []

    def test_delete_nonexistent_returns_404(self, client: TestClient):
        resp = client.delete("/routes/does-not-exist")
        assert resp.status_code == 404
        assert resp.json() == {"error": "route not found"}

    def test_delete_already_deleted_returns_404(self, client: TestClient):
        client.post("/routes", json=SLACK_ROUTE)
        client.delete("/routes/route-1")
        resp = client.delete("/routes/route-1")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /routes — validation failures (400)
# ---------------------------------------------------------------------------

class TestRouteValidation:
    def test_missing_id_returns_400(self, client: TestClient):
        body = {k: v for k, v in SLACK_ROUTE.items() if k != "id"}
        resp = client.post("/routes", json=body)
        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_missing_conditions_returns_400(self, client: TestClient):
        body = {k: v for k, v in SLACK_ROUTE.items() if k != "conditions"}
        resp = client.post("/routes", json=body)
        assert resp.status_code == 400

    def test_missing_target_returns_400(self, client: TestClient):
        body = {k: v for k, v in SLACK_ROUTE.items() if k != "target"}
        resp = client.post("/routes", json=body)
        assert resp.status_code == 400

    def test_missing_priority_returns_400(self, client: TestClient):
        body = {k: v for k, v in SLACK_ROUTE.items() if k != "priority"}
        resp = client.post("/routes", json=body)
        assert resp.status_code == 400

    def test_float_priority_returns_400(self, client: TestClient):
        resp = client.post("/routes", json={**SLACK_ROUTE, "priority": 10.0})
        assert resp.status_code == 400
        assert "integer" in resp.json()["error"]

    def test_string_priority_returns_400(self, client: TestClient):
        resp = client.post("/routes", json={**SLACK_ROUTE, "priority": "high"})
        assert resp.status_code == 400

    def test_negative_suppression_window_returns_400(self, client: TestClient):
        resp = client.post("/routes", json={**SLACK_ROUTE, "suppression_window_seconds": -1})
        assert resp.status_code == 400
        assert "non-negative" in resp.json()["error"]

    def test_invalid_target_type_returns_400(self, client: TestClient):
        body = {**SLACK_ROUTE, "target": {"type": "teams", "channel": "#x"}}
        resp = client.post("/routes", json=body)
        assert resp.status_code == 400

    def test_slack_missing_channel_returns_400(self, client: TestClient):
        body = {**SLACK_ROUTE, "target": {"type": "slack"}}
        resp = client.post("/routes", json=body)
        assert resp.status_code == 400
        assert "channel" in resp.json()["error"]

    def test_email_missing_address_returns_400(self, client: TestClient):
        body = {**SLACK_ROUTE, "target": {"type": "email"}}
        resp = client.post("/routes", json=body)
        assert resp.status_code == 400
        assert "address" in resp.json()["error"]

    def test_pagerduty_missing_service_key_returns_400(self, client: TestClient):
        body = {**SLACK_ROUTE, "target": {"type": "pagerduty"}}
        resp = client.post("/routes", json=body)
        assert resp.status_code == 400
        assert "service_key" in resp.json()["error"]

    def test_webhook_missing_url_returns_400(self, client: TestClient):
        body = {**SLACK_ROUTE, "target": {"type": "webhook"}}
        resp = client.post("/routes", json=body)
        assert resp.status_code == 400
        assert "url" in resp.json()["error"]

    def test_invalid_timezone_returns_400(self, client: TestClient):
        body = {**SLACK_ROUTE, "active_hours": {"timezone": "Bad/Zone", "start": "09:00", "end": "17:00"}}
        resp = client.post("/routes", json=body)
        assert resp.status_code == 400
        assert "timezone" in resp.json()["error"].lower()

    def test_invalid_time_format_returns_400(self, client: TestClient):
        body = {**SLACK_ROUTE, "active_hours": {"timezone": "UTC", "start": "9:00", "end": "17:00"}}
        resp = client.post("/routes", json=body)
        assert resp.status_code == 400
        assert "HH:MM" in resp.json()["error"]

    def test_invalid_time_value_returns_400(self, client: TestClient):
        body = {**SLACK_ROUTE, "active_hours": {"timezone": "UTC", "start": "25:00", "end": "17:00"}}
        resp = client.post("/routes", json=body)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Target serialization — null fields omitted in responses (fix #1)
# ---------------------------------------------------------------------------

class TestTargetResponseShape:
    def test_slack_target_omits_null_fields_in_get_routes(self, client: TestClient):
        client.post("/routes", json=SLACK_ROUTE)
        route = client.get("/routes").json()["routes"][0]
        target = route["target"]
        assert "address" not in target
        assert "service_key" not in target
        assert "url" not in target
        assert "headers" not in target

    def test_slack_target_has_type_and_channel(self, client: TestClient):
        client.post("/routes", json=SLACK_ROUTE)
        route = client.get("/routes").json()["routes"][0]
        assert route["target"]["type"] == "slack"
        assert route["target"]["channel"] == "#oncall"

    def test_alert_response_target_omits_null_fields(self, client: TestClient):
        client.post("/routes", json=SLACK_ROUTE)
        body = client.post("/alerts", json={
            "id": "a1", "severity": "critical", "service": "payment-api",
            "group": "backend", "timestamp": "2026-03-25T14:00:00Z",
        }).json()
        target = body["routed_to"]["target"]
        assert "address" not in target
        assert "service_key" not in target
        assert "url" not in target


# ---------------------------------------------------------------------------
# Conditions severity validation (fix #2)
# ---------------------------------------------------------------------------

class TestConditionsSeverityValidation:
    def test_invalid_severity_in_conditions_returns_400(self, client: TestClient):
        resp = client.post("/routes", json={
            **SLACK_ROUTE,
            "conditions": {"severity": ["urgent"]},
        })
        assert resp.status_code == 400
        assert "severity" in resp.json()["error"].lower()

    def test_mixed_valid_invalid_severity_returns_400(self, client: TestClient):
        resp = client.post("/routes", json={
            **SLACK_ROUTE,
            "conditions": {"severity": ["critical", "unknown"]},
        })
        assert resp.status_code == 400

    def test_valid_severity_values_accepted(self, client: TestClient):
        resp = client.post("/routes", json={
            **SLACK_ROUTE,
            "conditions": {"severity": ["critical", "warning", "info"]},
        })
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# DELETE clears suppression windows (fix #4)
# ---------------------------------------------------------------------------

class TestDeleteClearsSuppressionWindows:
    def test_recreated_route_not_suppressed_by_stale_window(self, client: TestClient):
        route = {
            "id": "r1", "conditions": {},
            "target": {"type": "slack", "channel": "#x"},
            "priority": 10, "suppression_window_seconds": 300,
        }
        client.post("/routes", json=route)
        # Trigger a suppression window
        client.post("/alerts", json={
            "id": "a1", "severity": "critical", "service": "payment-api",
            "group": "g", "timestamp": "2026-03-25T14:00:00Z",
        })
        # Delete the route — should clear its suppression windows
        client.delete("/routes/r1")
        # Recreate the same route
        client.post("/routes", json=route)
        # First alert after recreation must NOT be suppressed
        body = client.post("/alerts", json={
            "id": "a2", "severity": "critical", "service": "payment-api",
            "group": "g", "timestamp": "2026-03-25T14:01:00Z",
        }).json()
        assert body["suppressed"] is False
        assert body["routed_to"]["route_id"] == "r1"

    def test_delete_clears_only_matching_route_windows(self, client: TestClient):
        for rid in ("r1", "r2"):
            client.post("/routes", json={
                "id": rid, "conditions": {"service": [rid]},
                "target": {"type": "slack", "channel": f"#{rid}"},
                "priority": 10, "suppression_window_seconds": 300,
            })
        client.post("/alerts", json={
            "id": "a1", "severity": "critical", "service": "r1",
            "group": "g", "timestamp": "2026-03-25T14:00:00Z",
        })
        client.post("/alerts", json={
            "id": "a2", "severity": "critical", "service": "r2",
            "group": "g", "timestamp": "2026-03-25T14:00:00Z",
        })
        # Delete r1 — r2's window should be unaffected
        client.delete("/routes/r1")
        body = client.post("/alerts", json={
            "id": "a3", "severity": "critical", "service": "r2",
            "group": "g", "timestamp": "2026-03-25T14:01:00Z",
        }).json()
        assert body["suppressed"] is True
        assert body["routed_to"]["route_id"] == "r2"
