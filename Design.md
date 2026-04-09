# Design Doc: Configurable Alert Routing Engine

## Overview

This service is a configurable alert routing engine — similar in concept to PagerDuty routing rules or Prometheus Alertmanager. It ingests monitoring alerts via a REST API, evaluates them against user-defined routing configurations, and produces deterministic routing decisions. All state is held in-memory; no database is required.

### Tech Stack

- **Python 3.13**: Strong standard library support for datetime/timezone handling (`zoneinfo`), `fnmatch` for glob matching, and clean data modeling via Pydantic. Python's expressiveness keeps the routing logic readable.
- **FastAPI**: Async-ready, Pydantic-native web framework. Handles request/response validation, automatic OpenAPI docs, and clean path/query parameter parsing with minimal boilerplate. Exception handlers plug in naturally.
- **Uvicorn**: ASGI server for FastAPI. Starts fast (well under the 10-second container startup requirement), single-worker is sufficient for a single-process in-memory service.
- **Pydantic v2**: Data validation and serialization for all models. `model_validator` and `field_validator` give us granular control for the validation requirements (severity enum, IANA timezone, HH:MM format, ISO 8601 timestamps, etc.).
- **`zoneinfo`** (stdlib): IANA timezone-aware datetime conversion for `active_hours` logic — no third-party library needed.
- **`fnmatch`** (stdlib): Glob pattern matching for service name patterns (`payment-*`, `*-api`).

This stack is well-suited: FastAPI's Pydantic integration means validation errors map directly to `400` responses, timezone handling is solid via `zoneinfo`, and the single-process model fits perfectly with the in-memory state requirement.

---

## Project Layout

```
alert-router/
├── Dockerfile
├── README.md
├── Design.md
├── requirements.txt
├── app/
│   ├── __init__.py
│   ├── main.py            # FastAPI app factory, mounts routers, registers exception handlers
│   ├── models.py          # Pydantic models: Alert, RouteConfig, AlertResult, Stats, AppState
│   ├── state.py           # Module-level AppState singleton
│   ├── engine.py          # Routing engine: match, evaluate, suppress logic
│   └── routers/
│       ├── __init__.py
│       ├── routes.py      # POST/GET/DELETE /routes
│       ├── alerts.py      # POST /alerts, GET /alerts, GET /alerts/{id}
│       └── system.py      # GET /stats, POST /test, POST /reset
└── tests/
    ├── conftest.py            # pytest fixtures: TestClient, reset state between tests
    ├── unit/
    │   ├── test_models.py     # Pydantic validation: required fields, invalid enums, bad timestamps, etc.
    │   ├── test_engine.py     # Routing engine logic: condition matching, glob, active hours, suppression, priority
    │   └── test_stats.py      # Stats increment correctness across routed/suppressed/unrouted outcomes
    └── e2e/
        ├── test_routes.py     # Route CRUD: create, list, update (re-POST), delete, 404
        ├── test_alerts.py     # Alert submission: basic routing, unrouted, re-submission (upsert)
        ├── test_suppression.py  # Suppression window: first routes, second suppressed, expiry, different service
        ├── test_active_hours.py # Active hours + timezone: inside window, outside, boundary times, overnight
        ├── test_filtering.py  # GET /alerts filters: service, severity, routed, suppressed, combined
        ├── test_stats.py      # GET /stats: all counters, by_severity, by_route, by_service
        ├── test_dry_run.py    # POST /test: correct result, no state mutation, no suppression side effects
        └── test_reset.py      # POST /reset: routes, alerts, suppression windows, stats all cleared
```

---

## Data Models

All models live in `app/models.py` using Pydantic v2.

### `Alert`
Represents an incoming monitoring event.

```python
class Alert(BaseModel):
    id: str                              # required, unique
    severity: Literal["critical", "warning", "info"]  # required, validated enum
    service: str                         # required
    group: str                           # required
    description: Optional[str] = None   # optional
    timestamp: datetime                  # required, ISO 8601; parsed by Pydantic
    labels: Optional[Dict[str, str]] = {}  # optional key-value pairs
```

Validators:
- `timestamp`: Pydantic parses ISO 8601 automatically; we add a validator to reject non-timezone-aware strings (raise 400).
- `severity`: `Literal` type enforces the allowed values; Pydantic raises `ValidationError` on unknown values.

### `RouteConfig`
Represents a routing rule.

```python
class ActiveHours(BaseModel):
    timezone: str   # validated IANA timezone (e.g. "America/New_York")
    start: str      # "HH:MM" format
    end: str        # "HH:MM" format

class Target(BaseModel):
    type: Literal["slack", "email", "pagerduty", "webhook"]
    # type-specific fields validated via model_validator:
    channel: Optional[str] = None       # slack
    address: Optional[str] = None       # email
    service_key: Optional[str] = None   # pagerduty
    url: Optional[str] = None           # webhook
    headers: Optional[Dict[str, str]] = None  # webhook optional

class Conditions(BaseModel):
    severity: Optional[List[str]] = None
    service: Optional[List[str]] = None   # supports glob patterns
    group: Optional[List[str]] = None
    labels: Optional[Dict[str, str]] = None

class RouteConfig(BaseModel):
    id: str
    conditions: Conditions
    target: Target
    priority: int
    suppression_window_seconds: int = 0
    active_hours: Optional[ActiveHours] = None
```

Validators:
- `ActiveHours.timezone`: Check via `zoneinfo.ZoneInfo(tz)` — raises `ValueError` if invalid IANA zone.
- `ActiveHours.start/end`: Regex or `datetime.strptime` to enforce `HH:MM` format.
- `Target`: `model_validator(mode='after')` checks that required type-specific fields are present.
- `priority`: `int` type enforces integer; Pydantic rejects floats/strings.
- `suppression_window_seconds`: `field_validator` to reject negative values.

### `AlertResult`
Stored result of routing an alert. Also used as the response body for `POST /alerts` and `GET /alerts/{id}`.

```python
class RoutedTo(BaseModel):
    route_id: str
    target: Target

class EvaluationDetails(BaseModel):
    total_routes_evaluated: int
    routes_matched: int
    routes_not_matched: int
    suppression_applied: bool

class AlertResult(BaseModel):
    alert_id: str
    routed_to: Optional[RoutedTo]         # None if unrouted
    suppressed: bool
    suppression_reason: Optional[str] = None
    matched_routes: List[str]             # IDs of all matching routes
    evaluation_details: EvaluationDetails
```

---

## In-Memory Store

`app/state.py` holds a single module-level `AppState` instance imported by all routers and the engine.

### `Stats`

Tracks aggregate statistics, updated on every `POST /alerts` (not `POST /test`).

```python
class RouteStats(BaseModel):
    total_matched: int = 0
    total_routed: int = 0
    total_suppressed: int = 0

class Stats(BaseModel):
    total_alerts_processed: int = 0
    total_routed: int = 0
    total_suppressed: int = 0
    total_unrouted: int = 0
    by_severity: Dict[str, int] = {"critical": 0, "warning": 0, "info": 0}
    by_route: Dict[str, RouteStats] = {}
    by_service: Dict[str, int] = {}
```

### `AppState`

```python
class AppState:
    routes: Dict[str, RouteConfig] = {}
    alerts: Dict[str, AlertResult] = {}
    # Suppression tracking: (route_id, service) -> expiry datetime (UTC)
    suppression_windows: Dict[Tuple[str, str], datetime] = {}
    stats: Stats = Stats()

    def reset(self):
        self.routes.clear()
        self.alerts.clear()
        self.suppression_windows.clear()
        self.stats = Stats()
```

Module-level singleton:

```python
# app/state.py
app_state = AppState()
```

All routers import `app_state` directly. This is intentionally simple — no dependency injection needed for a single-process system.

---

## Routing Engine

`app/engine.py` contains the core `evaluate_alert(alert, state, dry_run=False) -> AlertResult` function.

### Step-by-step evaluation

**Step 1 — Gather all routes**
Collect `list(state.routes.values())`. `total_routes_evaluated = len(routes)`.

**Step 2 — Condition matching for each route**
For each route, run `matches_conditions(alert, route.conditions)`:

- If `conditions.severity` is set: `alert.severity in conditions.severity` must be `True`.
- If `conditions.service` is set: at least one pattern in the list must match `alert.service` using `fnmatch.fnmatch(alert.service, pattern)`. This handles `payment-*`, `*-api`, etc.
- If `conditions.group` is set: `alert.group in conditions.group` must be `True`.
- If `conditions.labels` is set: for every `(k, v)` in `conditions.labels`, `alert.labels.get(k) == v` must be `True`. Extra labels on the alert are ignored.
- An omitted/`None` condition field matches everything for that dimension.

All specified conditions must match (logical AND). The route is added to `matching_routes` only if all pass.

**Step 3 — Active hours check**
For each route in `matching_routes`, if `route.active_hours` is set:

1. Parse `alert.timestamp` as a timezone-aware UTC datetime.
2. Convert it to `route.active_hours.timezone` using `zoneinfo.ZoneInfo`.
3. Extract `HH:MM` from the localized time.
4. Parse `active_hours.start` and `active_hours.end` as `time` objects.
5. If `local_time < start or local_time >= end`: remove the route from `matching_routes`.

Routes without `active_hours` always remain in `matching_routes`.

**Step 4 — Sort by priority**
`matching_routes.sort(key=lambda r: r.priority, reverse=True)`

Collect `matched_route_ids = [r.id for r in matching_routes]`.

**Step 5 — Select the winner**
`winner = matching_routes[0]` if any remain, else `None`.

**Step 6 — Suppression check (winner only)**
If `winner` is not `None` and `winner.suppression_window_seconds > 0`:

1. Key: `(winner.id, alert.service)`
2. Lookup `expiry = state.suppression_windows.get(key)`
3. If `expiry` exists and `alert.timestamp < expiry`: alert is **suppressed**.
   - Set `suppressed = True`, compute `suppression_reason` string with the expiry time (ISO 8601 UTC).
   - Do NOT update the suppression window (the existing window stands).
4. If no active suppression: alert is **routed**.
   - Set new `state.suppression_windows[key] = alert.timestamp + timedelta(seconds=winner.suppression_window_seconds)`.

> **Key nuance**: Suppression uses `alert.timestamp`, not wall-clock time. This is per the spec hint: "pay attention to timestamps, not wall-clock time."

**Step 7 — Build result**
```
AlertResult(
    alert_id=alert.id,
    routed_to=RoutedTo(route_id=winner.id, target=winner.target) if winner and not suppressed else (RoutedTo(...) if suppressed else None),
    suppressed=suppressed,
    suppression_reason=...,
    matched_routes=matched_route_ids,
    evaluation_details=EvaluationDetails(
        total_routes_evaluated=total,
        routes_matched=len(matching_routes),
        routes_not_matched=total - len(matching_routes),
        suppression_applied=suppressed,
    )
)
```

> Note: When suppressed, `routed_to` still contains the winning route (the route that would have routed but was suppressed). When unrouted, `routed_to` is `null`.

**Step 8 — Side effects (skipped for dry_run)**
If `dry_run=False`:
- `state.alerts[alert.id] = result` (upsert — re-submission of same ID updates the record).
- Update `state.stats` (see Stats Tracking section).

---

## Validation

### Pydantic model validators

All validation is expressed via Pydantic v2 validators in `models.py`. FastAPI automatically catches `RequestValidationError` and returns `422` by default — we override this to return `400` with `{"error": "..."}`.

Key validators:

| Field | Validator | Error condition |
|---|---|---|
| `Alert.severity` | `Literal` type | Not one of `critical/warning/info` |
| `Alert.timestamp` | `field_validator` | Not valid ISO 8601 or no timezone info |
| `RouteConfig.priority` | `int` type | Not an integer (float, string) |
| `RouteConfig.suppression_window_seconds` | `field_validator` | Negative value |
| `Target.type` | `Literal` type | Not one of `slack/email/pagerduty/webhook` |
| `Target` fields | `model_validator(mode='after')` | Missing required type-specific field |
| `ActiveHours.timezone` | `field_validator` | Invalid IANA timezone string |
| `ActiveHours.start/end` | `field_validator` | Not `HH:MM` format |

### Exception handlers in `main.py`

```python
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    # Extract first error message from exc.errors()
    return JSONResponse(status_code=400, content={"error": first_error_message})
```

This converts Pydantic's `RequestValidationError` into the `{"error": "..."}` format required by the spec.

---

## API Endpoints

### Routes (`app/routers/routes.py`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/routes` | Create or update a route. Returns `201` with `{"id": ..., "created": true/false}`. |
| `GET` | `/routes` | List all routes. Returns `{"routes": [...]}`. |
| `DELETE` | `/routes/{id}` | Delete route by ID. Returns `{"id": ..., "deleted": true}` or `404`. |

Logic for `POST /routes`:
- `created = route.id not in state.routes`
- `state.routes[route.id] = route`
- Return `201` with `{"id": route.id, "created": created}`

### Alerts (`app/routers/alerts.py`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/alerts` | Submit alert for routing. Returns `200` with `AlertResult`. |
| `GET` | `/alerts/{id}` | Get result for a specific alert. Returns `200` or `404`. |
| `GET` | `/alerts` | List alerts with optional filters. Returns `{"alerts": [...], "total": N}`. |

Query params for `GET /alerts`: `service` (exact match), `severity`, `routed` (bool), `suppressed` (bool). All optional and combinable.

Filtering logic:
- `routed=true`: `result.routed_to is not None`
- `routed=false`: `result.routed_to is None`
- `suppressed=true`: `result.suppressed == True`
- `suppressed=false`: `result.suppressed == False`

### System (`app/routers/system.py`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/stats` | Return aggregate stats. |
| `POST` | `/test` | Dry-run alert routing. No state changes. Returns `AlertResult`. |
| `POST` | `/reset` | Clear all state. Returns `{"status": "ok"}`. |

`POST /test` calls `evaluate_alert(alert, state, dry_run=True)` — same engine, no side effects.

---

## Stats Tracking

Stats are updated at the end of `evaluate_alert` when `dry_run=False`.

On each `POST /alerts`:

1. **`total_alerts_processed`** — always incremented by 1.
2. **`total_routed`** — incremented if `routed_to is not None and not suppressed`.
3. **`total_suppressed`** — incremented if `suppressed == True`.
4. **`total_unrouted`** — incremented if `routed_to is None and not suppressed` (no matching route).
5. **`by_severity[alert.severity]`** — always incremented by 1.
6. **`by_service[alert.service]`** — always incremented by 1.
7. **`by_route[winner.id]`** — updated for the winning route only:
   - `total_matched` — incremented (winner was matched).
   - `total_routed` — incremented if not suppressed.
   - `total_suppressed` — incremented if suppressed.

> Note: `by_route` only tracks the winning route per alert, not all matched routes. This matches the spec's example which shows per-route `total_matched`, `total_routed`, `total_suppressed`.

When an alert is **re-submitted** (same `id`), the old `AlertResult` is overwritten. The stats counters for the previous submission are NOT reversed — stats are append-only event counts. This is the simplest correct interpretation since the spec doesn't mention stat rollback on re-submission.

---

## Special Attention & Key Edge Cases

### 1. Suppression uses alert timestamp, not wall-clock time
The suppression window expiry is calculated as `alert.timestamp + timedelta(seconds=window)`. When checking suppression for a subsequent alert, compare `incoming_alert.timestamp < expiry_datetime`. This means a backdated alert can "slip through" a window, and a future-dated alert can set an expiry far in the future — both correct per spec.

### 2. Glob matching is one-directional
`fnmatch.fnmatch(alert.service, pattern)` — the pattern is the route condition, the value is the alert's service. For a route condition `["payment-*"]`, each string in the list is treated as a potential glob pattern. Non-glob strings (no `*`) still work because `fnmatch` treats them as exact matches.

### 3. Active hours boundary: `start` inclusive, `end` exclusive
The spec says `"09:00"` to `"17:00"`. Use `start <= local_time < end`. Alerts at exactly `09:00` are in; alerts at exactly `17:00` are out. This matches standard half-open interval convention.

### 4. Active hours crossing midnight
The spec examples don't cover overnight windows (e.g., `"22:00"` to `"06:00"`). Implement with: if `start > end`, the window wraps midnight, so `local_time >= start OR local_time < end`. This avoids a subtle bug for overnight on-call rotations.

### 5. Re-submitted alert IDs
`POST /alerts` with an existing `id` replaces the stored `AlertResult` with the new evaluation result. The routing is fully re-evaluated against current routes. Stats are not rolled back for the old result.

### 6. Route deletion with active suppression windows
If a route is deleted, its suppression windows remain in `state.suppression_windows`. This is harmless — the deleted route will never be selected as a winner again. No cleanup needed.

### 7. Empty `conditions` object matches all alerts
A `RouteConfig` with `conditions: {}` (all fields `None`) must match every alert. This is the "catch-all" route pattern. Ensure the matching logic defaults to `True` for each unset condition field.

### 8. `POST /test` must not affect suppression state
The dry-run path must read from `state.suppression_windows` to compute the correct result (so it reports accurately whether suppression would apply), but must NOT write back to it. Pass a `dry_run=True` flag to skip the state mutation step.

### 9. `matched_routes` always reflects all condition+active_hours matches, regardless of suppression
`matched_routes` is computed before the suppression check and must contain every route that passed both condition matching and the active hours check — regardless of the final outcome (routed, suppressed, or unrouted). Suppression only controls whether a notification is produced; it does not filter `matched_routes`. The suppressed response example in the spec shows `["route-1"]` because only one route matched in that scenario, not because suppression trimmed the list. When unrouted (no routes matched), `matched_routes` is `[]`.

### 10. `priority` must be an integer — reject floats
Pydantic v2 by default coerces `10.0` to `10` in lax mode. Use `model_config = ConfigDict(strict=True)` on `RouteConfig` or a `field_validator` to reject non-integer priority values explicitly.

### 11. `evaluation_details.routes_matched` counts routes that passed condition + active_hours
The active hours check is part of "matching." A route that fails the active hours check should be counted in `routes_not_matched`, not `routes_matched`.

### 12. Timezone validation must reject unknown strings
`zoneinfo.ZoneInfo("Invalid/Zone")` raises `ZoneInfoNotFoundError`. Catch this in the `field_validator` and raise a `ValueError` with a clear message. This also correctly rejects strings like `"EST"` which are not valid IANA zone names.

---

## Implementation Order

Build in this sequence to keep a working state at each stage:

### Phase 1 — Scaffold & Core Models
1. Set up `requirements.txt`, `Dockerfile`, `app/main.py` with a bare FastAPI app on port 8080.
2. Implement `app/models.py`: `Alert`, `Conditions`, `ActiveHours`, `Target`, `RouteConfig`, `AlertResult`, `Stats`, `AppState`.
3. Implement `app/state.py`: module-level `app_state` singleton.
4. Add the `RequestValidationError` → `400` exception handler in `main.py`.

### Phase 2 — Route CRUD
5. Implement `app/routers/routes.py`: `POST /routes`, `GET /routes`, `DELETE /routes/{id}`.
6. Manually test with curl: create, list, update (re-POST), delete, 404 on missing.

### Phase 3 — Core Routing Engine
7. Implement `app/engine.py`:
   - Condition matching (severity, service with glob, group, labels).
   - Active hours check using `zoneinfo`.
   - Priority sorting and winner selection.
   - Result building (no suppression yet).
8. Implement `POST /alerts` (no suppression, no stats yet).
9. Test: basic routing, multiple matching routes (highest priority wins), unrouted alerts, omitted conditions.

### Phase 4 — Suppression
10. Add suppression window tracking to `AppState`.
11. Add suppression check to engine: read window, compare timestamps, write new window.
12. Test: first alert routes, second alert same service+route suppresses, alert after window expiry routes again, different service not suppressed.

### Phase 5 — Stats
13. Add stats update logic to `evaluate_alert` (non-dry-run path).
14. Implement `GET /stats`.
15. Test all stat counters.

### Phase 6 — Query Endpoints & Dry-run
16. Implement `GET /alerts/{id}` and `GET /alerts` with filters.
17. Implement `POST /test` (dry_run=True path).
18. Implement `POST /reset`.
19. Test filtering combinations, dry-run has no side effects, reset zeroes everything.

### Phase 7 — Validation Hardening
20. Add all field validators: negative suppression, invalid IANA timezone, bad `HH:MM` format, invalid ISO 8601, strict integer priority.
21. Run through the full validation test cases from the spec.

### Phase 8 — Docker & Polish
22. Finalize `Dockerfile`: slim Python base image, install deps, copy app, `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]`.
23. Verify container starts within 10 seconds.
24. Write `README.md`.
25. Do a full end-to-end curl test sweep covering: suppression window expiry, active hours boundary, re-submitted alert IDs, combined filters, and `evaluation_details` counts.
