# Alert Routing Engine

A configurable alert routing service that ingests monitoring alerts, evaluates them against user-defined routing rules, and produces deterministic routing decisions. Runs in a single Docker container, exposes a REST API on port 8080.

## Language & Framework

**Python 3.12 + FastAPI + Uvicorn**

- **FastAPI** is Pydantic-native, so request validation and the `{"error": "..."}` 400 responses fall out naturally from model definitions — no separate validation layer to wire up.
- **Pydantic v2** handles all input validation (enums, ISO 8601 timestamps, IANA timezones, HH:MM format, integer priority) via field/model validators.
- **`zoneinfo`** (stdlib) covers IANA timezone-aware datetime conversion for `active_hours` without any third-party dependency.
- **`fnmatch`** (stdlib) handles glob matching for service name patterns (`payment-*`, `*-api`).
- **Uvicorn** starts in under a second — well within the 10-second container readiness requirement.

See [`Design.md`](./Design.md) for the full rationale and architecture.

## Build & Run

```bash
docker build -t alert-router .
docker run -p 8080:8080 alert-router
```

The service will be ready on `http://localhost:8080` within a few seconds.

## API Overview

| Method | Path | Description |
|---|---|---|
| `POST` | `/routes` | Create or update a routing configuration |
| `GET` | `/routes` | List all routing configurations |
| `DELETE` | `/routes/{id}` | Delete a routing configuration |
| `POST` | `/alerts` | Submit an alert for routing evaluation |
| `GET` | `/alerts/{id}` | Get routing result for a specific alert |
| `GET` | `/alerts` | List alerts with optional filters (`service`, `severity`, `routed`, `suppressed`) |
| `GET` | `/stats` | Aggregate statistics |
| `POST` | `/test` | Dry-run an alert without recording it or affecting suppression state |
| `POST` | `/reset` | Clear all state (routes, alerts, suppression windows, stats) |

All endpoints accept and return JSON. Full request/response shapes are in the spec and covered in [`Design.md § API Endpoints`](./Design.md#api-endpoints).

## Design Decisions

### In-memory state
All state (routes, alerts, suppression windows, stats) is held in a single module-level `AppState` instance. No database, no external dependencies. See [`Design.md § In-Memory Store`](./Design.md#in-memory-store).

### Routing engine
Alert evaluation follows a strict pipeline: condition matching → active hours check → priority sort → winner selection → suppression check. Only the single highest-priority matching route produces a notification. See [`Design.md § Routing Engine`](./Design.md#routing-engine) for the full step-by-step breakdown.

### Suppression uses alert timestamp, not wall-clock time
Suppression window expiry is calculated from `alert.timestamp`, not the server's current time. This makes behavior deterministic and testable with historical or future-dated alerts.

### Glob matching via `fnmatch`
Service name patterns in route conditions support `*` as a wildcard (e.g., `payment-*`, `*-api`). Implemented with Python's stdlib `fnmatch.fnmatch` — no regex needed.

### Validation errors return 400
FastAPI's default validation error status is 422. A global exception handler overrides this to return `400 Bad Request` with `{"error": "..."}` as required by the spec.

For edge cases and gotchas (midnight-crossing active hours, re-submitted alert IDs, dry-run suppression semantics, etc.), see [`Design.md § Special Attention & Key Edge Cases`](./Design.md#special-attention--key-edge-cases).
