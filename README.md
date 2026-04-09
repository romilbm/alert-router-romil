# Alert Routing Engine

A configurable alert routing service that ingests monitoring alerts, evaluates them against user-defined routing rules, and produces deterministic routing decisions. Runs in a single Docker container, exposes a REST API on port 8080.

## Language & Framework

**Python 3.13 + FastAPI + Uvicorn**

- **FastAPI** is Pydantic-native, so request validation and the `{"error": "..."}` 400 responses fall out naturally from model definitions — no separate validation layer.
- **Pydantic v2** handles all input validation (enums, ISO 8601 timestamps, IANA timezones, HH:MM format, integer priority) via field/model validators.
- **`zoneinfo`** (stdlib) covers IANA timezone-aware datetime conversion for `active_hours` with no third-party dependency.
- **`fnmatch`** (stdlib) handles glob matching for service name patterns (`payment-*`, `*-api`).
- **Uvicorn** starts in under a second — well within the 10-second container readiness requirement.

See [`Design.md`](./Design.md) for the full architecture and rationale.

---

## Build & Run

**Docker:**
```bash
docker build -t alert-router .
docker run -p 8080:8080 alert-router
```

**Local (development):**
```bash
# Python 3.13 required — pydantic-core does not yet have wheels for 3.14
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8080 --reload
```

The service is ready on `http://localhost:8080`. Health check: `GET /healthz` → `{"status": "ok"}`.

---

## API Reference

### Routes

#### `POST /routes` — Create or update a route

Request body:
```json
{
  "id": "oncall-critical",
  "conditions": {
    "severity": ["critical"],
    "service": ["payment-*"],
    "group": ["backend"],
    "labels": {"env": "prod"}
  },
  "target": {"type": "slack", "channel": "#oncall"},
  "priority": 100,
  "suppression_window_seconds": 300,
  "active_hours": {
    "timezone": "America/New_York",
    "start": "09:00",
    "end": "17:00"
  }
}
```

`conditions` fields are all optional — omitting a field matches everything for that dimension. `active_hours` and `suppression_window_seconds` are optional (defaults: always active, no suppression).

Response `201`:
```json
{"id": "oncall-critical", "created": true}
```

`created` is `false` when re-POSTing an existing ID (update/replace).

#### `GET /routes` — List all routes

Response `200`:
```json
{"routes": [{"id": "oncall-critical", "conditions": {...}, ...}]}
```

#### `DELETE /routes/{id}` — Delete a route

Response `200`: `{"id": "oncall-critical", "deleted": true}`
Response `404`: `{"error": "route not found"}`

---

### Alerts

#### `POST /alerts` — Submit an alert for routing

Request body:
```json
{
  "id": "alert-001",
  "severity": "critical",
  "service": "payment-api",
  "group": "backend",
  "timestamp": "2026-03-25T14:00:00Z",
  "description": "High error rate",
  "labels": {"env": "prod", "region": "us-east-1"}
}
```

`severity` must be one of `critical`, `warning`, `info`. `timestamp` must be ISO 8601 with timezone info. All other optional fields default to empty/null.

Response `200`:
```json
{
  "alert_id": "alert-001",
  "routed_to": {"route_id": "oncall-critical", "target": {"type": "slack", "channel": "#oncall"}},
  "suppressed": false,
  "suppression_reason": null,
  "matched_routes": ["oncall-critical"],
  "evaluation_details": {
    "total_routes_evaluated": 2,
    "routes_matched": 1,
    "routes_not_matched": 1,
    "suppression_applied": false
  }
}
```

When suppressed, `routed_to` still names the winning route (the one that would have routed), and `suppression_reason` contains the expiry time:
```json
{
  "routed_to": {"route_id": "oncall-critical", "target": {...}},
  "suppressed": true,
  "suppression_reason": "Alert for service 'payment-api' on route 'oncall-critical' suppressed until 2026-03-25T14:05:00Z"
}
```

When no route matches, `routed_to` is `null` and `matched_routes` is `[]`.

Resubmitting an alert with the same `id` replaces the stored result. Stats are append-only (the old result's stats are not reversed).

#### `GET /alerts/{id}` — Get stored result

Response `200`: same `AlertResult` shape as above.
Response `404`: `{"error": "alert not found"}`

#### `GET /alerts` — List alerts with filters

Query parameters (all optional, combinable):

| Param | Type | Matches |
|---|---|---|
| `service` | string | exact service name match |
| `severity` | string | exact severity match (`critical`/`warning`/`info`) |
| `routed` | bool | `true` → `routed_to is not null`; `false` → `routed_to is null` |
| `suppressed` | bool | `true`/`false` matches `suppressed` field |

Note: `routed=true` includes suppressed alerts — `routed_to` is set even when suppressed (it names the route that matched but was suppressed).

Response `200`:
```json
{"alerts": [...], "total": 3}
```

---

### System

#### `GET /stats` — Aggregate statistics

Response `200`:
```json
{
  "total_alerts_processed": 10,
  "total_routed": 7,
  "total_suppressed": 2,
  "total_unrouted": 1,
  "by_severity": {"critical": 6, "warning": 3, "info": 1},
  "by_service": {"payment-api": 5, "auth-service": 5},
  "by_route": {
    "oncall-critical": {"total_matched": 9, "total_routed": 7, "total_suppressed": 2}
  }
}
```

`by_route` tracks only the winning route per alert. `total_routed` (global) counts alerts that were routed and not suppressed. `total_suppressed` counts suppressed alerts. Stats are updated only by `POST /alerts`, never by `POST /test`.

#### `POST /test` — Dry-run alert routing

Same request body as `POST /alerts`. Returns an `AlertResult` showing what *would* happen — reads current suppression state to produce an accurate result — but does not store the alert, update stats, or modify any suppression windows.

#### `POST /reset` — Clear all state

Clears all routes, alerts, suppression windows, and stats. Returns `{"status": "ok"}`.

---

## Routing Engine

Alert evaluation follows a strict pipeline (see [`Design.md § Routing Engine`](./Design.md#routing-engine)):

1. **Gather** all routes.
2. **Condition matching** — filter to routes where all specified conditions match (severity enum, service glob, group exact, labels key-value). Unset conditions match everything.
3. **Active hours check** — remove routes whose `active_hours` window does not cover the alert's timestamp (converted to the route's configured timezone). Routes without `active_hours` always pass.
4. **Sort** surviving routes by `priority` descending.
5. **Winner** = highest-priority match.
6. **Suppression check** — if the winner has `suppression_window_seconds > 0`, check `(route_id, service)` → expiry. If `alert.timestamp < expiry`: suppress (do not update window). Otherwise: route and set a new window.
7. **Build `AlertResult`** — `matched_routes` contains all routes that passed steps 2–3, computed before the suppression check.
8. **Side effects** (skipped for `POST /test`): persist result, update stats.

### Key semantics

- **`matched_routes`** lists every route that passed condition + active hours checks — regardless of suppression. Suppression only controls whether a notification fires; it does not filter `matched_routes`.
- **`evaluation_details.routes_matched`** counts routes that passed both condition matching and the active hours check. A route outside its active hours window is counted in `routes_not_matched`.
- **`routed_to`** is set to the winning route even when suppressed. It is `null` only when no route matched.
- **Suppression timestamps** use `alert.timestamp`, not wall-clock time — backdated alerts can slip through, future-dated alerts set windows far ahead. Both are correct per spec.
- **Glob service matching**: the pattern lives on the route, the value on the alert. `fnmatch("payment-api", "payment-*")` → `True`.
- **Active hours boundaries**: `start` is inclusive, `end` is exclusive. Overnight windows (e.g., `22:00`–`06:00`) are handled with OR logic.

---

## Validation

All validation is enforced via Pydantic v2 validators and returns `400 Bad Request` with `{"error": "..."}`.

| Field | Rejection condition |
|---|---|
| `Alert.severity` | Not one of `critical`, `warning`, `info` |
| `Alert.timestamp` | Not valid ISO 8601, or no timezone info (naive datetime) |
| `RouteConfig.priority` | Not a strict integer (floats like `10.0`, booleans rejected) |
| `RouteConfig.suppression_window_seconds` | Negative value |
| `Target.type` | Not one of `slack`, `email`, `pagerduty`, `webhook` |
| `Target` (slack) | Missing `channel` |
| `Target` (email) | Missing `address` |
| `Target` (pagerduty) | Missing `service_key` |
| `Target` (webhook) | Missing `url` |
| `ActiveHours.timezone` | Invalid IANA timezone string (e.g., `"EST"`, `""`) |
| `ActiveHours.start` / `.end` | Not `HH:MM` format (leading zeros required) |

---

## Running Tests

**Unit tests** (models, engine logic — no HTTP layer):
```bash
pytest tests/unit/ -v
```

**E2E tests** (full HTTP via FastAPI `TestClient`):
```bash
pytest tests/e2e/ -v
```

**All tests:**
```bash
pytest tests/ -v
```

**Shell E2E suite** (curl/jq against a live server — requires the service running on port 8080):
```bash
# Start the server first:
uvicorn app.main:app --port 8080

# In another terminal:
bash tests/e2e/test_e2e.sh
```

The shell suite covers Route CRUD, Route Validation, Basic Routing, Suppression Windows, Active Hours & Timezones, Query and Filtering, Stats, Dry-run, and Full Reset.

Current test count: **406 pytest tests** (unit + e2e), all passing.

---

## Troubleshooting

### `[Errno 48] address already in use` on port 8080

If a previous server process wasn't cleanly shut down, starting Uvicorn will fail with:
```
ERROR: [Errno 48] error while attempting to bind on address ('127.0.0.1', 8080): address already in use
```

Find and kill the process holding the port, then restart:
```bash
lsof -ti:8080 | xargs kill -9
uvicorn app.main:app --port 8080
```

This also applies before running the shell E2E suite — if the suite fails with unexpected results, a stale server with leftover state is the most likely cause. Kill it and start fresh before re-running.
