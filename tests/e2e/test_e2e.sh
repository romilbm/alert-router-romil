#!/usr/bin/env bash
# End-to-end tests against a running service on http://localhost:8080
# Usage: ./tests/e2e/test_e2e.sh [BASE_URL]
# Requires: curl, jq

set -euo pipefail

BASE="${1:-http://localhost:8080}"
PASS=0
FAIL=0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

green()  { printf '\033[32m%s\033[0m\n' "$*"; }
red()    { printf '\033[31m%s\033[0m\n' "$*"; }

pass() { PASS=$((PASS + 1)); green "  PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); red  "  FAIL: $1"; }

# assert_eq <label> <actual> <expected>
assert_eq() {
  if [ "$2" = "$3" ]; then
    pass "$1"
  else
    fail "$1 — got: $2, want: $3"
  fi
}

reset_state() {
  curl -s -X POST "$BASE/reset" > /dev/null
}

# ---------------------------------------------------------------------------
# Route CRUD
# ---------------------------------------------------------------------------

echo ""
echo "=== Route CRUD ==="
reset_state

SLACK_ROUTE='{
  "id": "route-1",
  "conditions": {"severity": ["critical"], "group": ["backend"]},
  "target": {"type": "slack", "channel": "#oncall"},
  "priority": 10
}'

# Create — returns 201, created: true
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/routes" \
  -H "Content-Type: application/json" -d "$SLACK_ROUTE")
assert_eq "POST /routes returns 201" "$STATUS" "201"

BODY=$(curl -s -X POST "$BASE/routes" \
  -H "Content-Type: application/json" \
  -d '{"id":"route-2","conditions":{},"target":{"type":"slack","channel":"#x"},"priority":5}')
assert_eq "POST /routes new route created=true"  "$(echo "$BODY" | jq -r '.created')" "true"
assert_eq "POST /routes new route id correct"     "$(echo "$BODY" | jq -r '.id')"      "route-2"

# Update — re-POST same ID returns created: false
BODY=$(curl -s -X POST "$BASE/routes" \
  -H "Content-Type: application/json" -d "$SLACK_ROUTE")
assert_eq "POST /routes re-POST same ID returns created=false" "$(echo "$BODY" | jq -r '.created')" "false"
assert_eq "POST /routes re-POST same ID returns 201" \
  "$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/routes" \
    -H "Content-Type: application/json" -d "$SLACK_ROUTE")" "201"

# Verify update replaces data
UPDATED=$(echo "$SLACK_ROUTE" | jq '.priority = 99')
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" -d "$UPDATED" > /dev/null
PRIORITY=$(curl -s "$BASE/routes" | jq '[.routes[] | select(.id=="route-1")] | .[0].priority')
assert_eq "POST /routes update replaces priority" "$PRIORITY" "99"

# List
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" -d "$SLACK_ROUTE" > /dev/null
ROUTES=$(curl -s "$BASE/routes")
assert_eq "GET /routes returns 200" \
  "$(curl -s -o /dev/null -w "%{http_code}" "$BASE/routes")" "200"
assert_eq "GET /routes returns array under 'routes'" \
  "$(echo "$ROUTES" | jq '.routes | type')" '"array"'
assert_eq "GET /routes count" "$(echo "$ROUTES" | jq '.routes | length')" "1"
assert_eq "GET /routes route id" "$(echo "$ROUTES" | jq -r '.routes[0].id')" "route-1"

# Empty list on fresh state
reset_state
assert_eq "GET /routes empty after reset" \
  "$(curl -s "$BASE/routes" | jq '.routes | length')" "0"

# Delete
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" -d "$SLACK_ROUTE" > /dev/null
BODY=$(curl -s -X DELETE "$BASE/routes/route-1")
assert_eq "DELETE /routes/{id} returns 200" \
  "$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/routes" \
    -H "Content-Type: application/json" -d "$SLACK_ROUTE" && \
    curl -s -o /dev/null -w "%{http_code}" -X DELETE "$BASE/routes/route-1")" "200"
assert_eq "DELETE /routes/{id} deleted=true"  "$(echo "$BODY" | jq -r '.deleted')" "true"
assert_eq "DELETE /routes/{id} id in response" "$(echo "$BODY" | jq -r '.id')"     "route-1"
assert_eq "DELETE removes route from list" \
  "$(curl -s "$BASE/routes" | jq '.routes | length')" "0"

# Delete — 404 on missing
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "$BASE/routes/does-not-exist")
assert_eq "DELETE /routes/{id} missing returns 404" "$STATUS" "404"
ERROR=$(curl -s -X DELETE "$BASE/routes/does-not-exist" | jq -r '.error')
assert_eq "DELETE /routes/{id} missing error body" "$ERROR" "route not found"

# Delete — 404 after already deleted
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" -d "$SLACK_ROUTE" > /dev/null
curl -s -X DELETE "$BASE/routes/route-1" > /dev/null
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "$BASE/routes/route-1")
assert_eq "DELETE already-deleted route returns 404" "$STATUS" "404"

# ---------------------------------------------------------------------------
# Route Validation
# ---------------------------------------------------------------------------

echo ""
echo "=== Route Validation ==="
reset_state

# Missing required fields
for field in id conditions target priority; do
  BODY=$(echo "$SLACK_ROUTE" | jq "del(.$field)")
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/routes" \
    -H "Content-Type: application/json" -d "$BODY")
  assert_eq "POST /routes missing $field returns 400" "$STATUS" "400"
  ERROR=$(curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" -d "$BODY" | jq -r '.error')
  assert_eq "POST /routes missing $field has error key" "$([ -n "$ERROR" ] && echo ok)" "ok"
done

# Float priority
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/routes" \
  -H "Content-Type: application/json" \
  -d '{"id":"r","conditions":{},"target":{"type":"slack","channel":"#x"},"priority":10.5}')
assert_eq "POST /routes float priority returns 400" "$STATUS" "400"
ERROR=$(curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"r","conditions":{},"target":{"type":"slack","channel":"#x"},"priority":10.5}' | jq -r '.error')
assert_eq "POST /routes float priority error mentions integer" \
  "$(echo "$ERROR" | grep -c integer)" "1"

# Negative suppression_window_seconds
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/routes" \
  -H "Content-Type: application/json" \
  -d '{"id":"r","conditions":{},"target":{"type":"slack","channel":"#x"},"priority":1,"suppression_window_seconds":-1}')
assert_eq "POST /routes negative suppression returns 400" "$STATUS" "400"

# Invalid target type
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/routes" \
  -H "Content-Type: application/json" \
  -d '{"id":"r","conditions":{},"target":{"type":"teams","channel":"#x"},"priority":1}')
assert_eq "POST /routes invalid target type returns 400" "$STATUS" "400"

# Slack missing channel
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/routes" \
  -H "Content-Type: application/json" \
  -d '{"id":"r","conditions":{},"target":{"type":"slack"},"priority":1}')
assert_eq "POST /routes slack missing channel returns 400" "$STATUS" "400"

# Email missing address
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/routes" \
  -H "Content-Type: application/json" \
  -d '{"id":"r","conditions":{},"target":{"type":"email"},"priority":1}')
assert_eq "POST /routes email missing address returns 400" "$STATUS" "400"

# PagerDuty missing service_key
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/routes" \
  -H "Content-Type: application/json" \
  -d '{"id":"r","conditions":{},"target":{"type":"pagerduty"},"priority":1}')
assert_eq "POST /routes pagerduty missing service_key returns 400" "$STATUS" "400"

# Webhook missing url
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/routes" \
  -H "Content-Type: application/json" \
  -d '{"id":"r","conditions":{},"target":{"type":"webhook"},"priority":1}')
assert_eq "POST /routes webhook missing url returns 400" "$STATUS" "400"

# Invalid IANA timezone
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/routes" \
  -H "Content-Type: application/json" \
  -d '{"id":"r","conditions":{},"target":{"type":"slack","channel":"#x"},"priority":1,"active_hours":{"timezone":"Bad/Zone","start":"09:00","end":"17:00"}}')
assert_eq "POST /routes invalid timezone returns 400" "$STATUS" "400"

# Invalid HH:MM format (missing leading zero)
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/routes" \
  -H "Content-Type: application/json" \
  -d '{"id":"r","conditions":{},"target":{"type":"slack","channel":"#x"},"priority":1,"active_hours":{"timezone":"UTC","start":"9:00","end":"17:00"}}')
assert_eq "POST /routes invalid time format returns 400" "$STATUS" "400"

# Invalid time value (25:00)
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/routes" \
  -H "Content-Type: application/json" \
  -d '{"id":"r","conditions":{},"target":{"type":"slack","channel":"#x"},"priority":1,"active_hours":{"timezone":"UTC","start":"25:00","end":"17:00"}}')
assert_eq "POST /routes invalid time value returns 400" "$STATUS" "400"

# ---------------------------------------------------------------------------
# Basic Routing
# ---------------------------------------------------------------------------

echo ""
echo "=== Basic Routing ==="

ALERT='{
  "id": "alert-1",
  "severity": "critical",
  "service": "payment-api",
  "group": "backend",
  "timestamp": "2026-03-25T14:30:00Z",
  "labels": {"env": "production"}
}'

# POST /alerts returns 200
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"r1","conditions":{},"target":{"type":"slack","channel":"#oncall"},"priority":10}' > /dev/null
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/alerts" \
  -H "Content-Type: application/json" -d "$ALERT")
assert_eq "POST /alerts returns 200" "$STATUS" "200"

# Routed to correct route
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" -d "$ALERT")
assert_eq "POST /alerts routed_to.route_id" \
  "$(echo "$BODY" | jq -r '.routed_to.route_id')" "r1"
assert_eq "POST /alerts suppressed=false" \
  "$(echo "$BODY" | jq -r '.suppressed')" "false"
assert_eq "POST /alerts alert_id" \
  "$(echo "$BODY" | jq -r '.alert_id')" "alert-1"

# Unrouted — no routes
reset_state
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" -d "$ALERT")
assert_eq "POST /alerts unrouted routed_to=null" \
  "$(echo "$BODY" | jq -r '.routed_to')" "null"
assert_eq "POST /alerts unrouted matched_routes empty" \
  "$(echo "$BODY" | jq '.matched_routes | length')" "0"

# Unrouted — no conditions match
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"info-only","conditions":{"severity":["info"]},"target":{"type":"slack","channel":"#x"},"priority":1}' > /dev/null
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" -d "$ALERT")
assert_eq "POST /alerts unrouted when no conditions match" \
  "$(echo "$BODY" | jq -r '.routed_to')" "null"

# Highest priority wins
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"low","conditions":{},"target":{"type":"slack","channel":"#low"},"priority":1}' > /dev/null
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"high","conditions":{},"target":{"type":"slack","channel":"#high"},"priority":99}' > /dev/null
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"mid","conditions":{},"target":{"type":"slack","channel":"#mid"},"priority":50}' > /dev/null
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" -d "$ALERT")
assert_eq "POST /alerts highest priority route wins" \
  "$(echo "$BODY" | jq -r '.routed_to.route_id')" "high"

# All matching routes appear in matched_routes
assert_eq "POST /alerts all matching routes in matched_routes" \
  "$(echo "$BODY" | jq '.matched_routes | length')" "3"

# Non-matching route not in matched_routes
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"match","conditions":{"severity":["critical"]},"target":{"type":"slack","channel":"#match"},"priority":10}' > /dev/null
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"no-match","conditions":{"severity":["info"]},"target":{"type":"slack","channel":"#nm"},"priority":5}' > /dev/null
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" -d "$ALERT")
assert_eq "POST /alerts non-matching route excluded from matched_routes" \
  "$(echo "$BODY" | jq '.matched_routes | length')" "1"
assert_eq "POST /alerts matched_routes contains correct route" \
  "$(echo "$BODY" | jq -r '.matched_routes[0]')" "match"

# evaluation_details counts
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"m1","conditions":{"severity":["critical"]},"target":{"type":"slack","channel":"#x"},"priority":10}' > /dev/null
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"m2","conditions":{"severity":["critical","warning"]},"target":{"type":"slack","channel":"#y"},"priority":5}' > /dev/null
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"n1","conditions":{"severity":["info"]},"target":{"type":"slack","channel":"#z"},"priority":1}' > /dev/null
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" -d "$ALERT")
assert_eq "POST /alerts evaluation_details.total_routes_evaluated" \
  "$(echo "$BODY" | jq '.evaluation_details.total_routes_evaluated')" "3"
assert_eq "POST /alerts evaluation_details.routes_matched" \
  "$(echo "$BODY" | jq '.evaluation_details.routes_matched')" "2"
assert_eq "POST /alerts evaluation_details.routes_not_matched" \
  "$(echo "$BODY" | jq '.evaluation_details.routes_not_matched')" "1"
assert_eq "POST /alerts evaluation_details.suppression_applied=false" \
  "$(echo "$BODY" | jq '.evaluation_details.suppression_applied')" "false"

# Glob matching: payment-*
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"pay-glob","conditions":{"service":["payment-*"]},"target":{"type":"slack","channel":"#x"},"priority":10}' > /dev/null
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" -d "$ALERT")
assert_eq "POST /alerts glob payment-* matches payment-api" \
  "$(echo "$BODY" | jq -r '.routed_to.route_id')" "pay-glob"
WORKER=$(echo "$ALERT" | jq '.id = "a2" | .service = "payment-worker"')
assert_eq "POST /alerts glob payment-* matches payment-worker" \
  "$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
    -d "$WORKER" | jq -r '.routed_to.route_id')" "pay-glob"
OTHER=$(echo "$ALERT" | jq '.id = "a3" | .service = "auth-service"')
assert_eq "POST /alerts glob payment-* does not match auth-service" \
  "$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
    -d "$OTHER" | jq -r '.routed_to')" "null"

# Empty conditions is catch-all
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"catch-all","conditions":{},"target":{"type":"slack","channel":"#all"},"priority":1}' > /dev/null
for SEV in critical warning info; do
  A=$(echo "$ALERT" | jq --arg s "$SEV" --arg id "a-$SEV" '.severity = $s | .id = $id')
  assert_eq "POST /alerts empty conditions matches severity=$SEV" \
    "$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
      -d "$A" | jq -r '.routed_to.route_id')" "catch-all"
done

# Alert validation — 400s
reset_state
for FIELD in severity service group timestamp; do
  BODY=$(echo "$ALERT" | jq "del(.$FIELD)")
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/alerts" \
    -H "Content-Type: application/json" -d "$BODY")
  assert_eq "POST /alerts missing $FIELD returns 400" "$STATUS" "400"
done

STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/alerts" \
  -H "Content-Type: application/json" \
  -d "$(echo "$ALERT" | jq '.severity = "urgent"')")
assert_eq "POST /alerts invalid severity returns 400" "$STATUS" "400"

STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/alerts" \
  -H "Content-Type: application/json" \
  -d "$(echo "$ALERT" | jq '.timestamp = "not-a-date"')")
assert_eq "POST /alerts invalid timestamp returns 400" "$STATUS" "400"

STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/alerts" \
  -H "Content-Type: application/json" \
  -d "$(echo "$ALERT" | jq '.timestamp = "2026-03-25T14:30:00"')")
assert_eq "POST /alerts naive timestamp (no tz) returns 400" "$STATUS" "400"

# GET /alerts/{id}
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"r1","conditions":{},"target":{"type":"slack","channel":"#x"},"priority":10}' > /dev/null
curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" -d "$ALERT" > /dev/null
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/alerts/alert-1")
assert_eq "GET /alerts/{id} returns 200" "$STATUS" "200"
assert_eq "GET /alerts/{id} correct alert_id" \
  "$(curl -s "$BASE/alerts/alert-1" | jq -r '.alert_id')" "alert-1"

# GET /alerts/{id} — 404
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/alerts/does-not-exist")
assert_eq "GET /alerts/{id} missing returns 404" "$STATUS" "404"
assert_eq "GET /alerts/{id} missing error body" \
  "$(curl -s "$BASE/alerts/does-not-exist" | jq -r '.error')" "alert not found"

# ---------------------------------------------------------------------------
# Suppression Windows
# ---------------------------------------------------------------------------

echo ""
echo "=== Suppression Windows ==="

# Timestamps relative to T0 = 2026-03-25T14:30:00Z, window = 300s, expiry = 14:35:00Z
T0="2026-03-25T14:30:00Z"
T_100="2026-03-25T14:31:40Z"   # +100s — within window
T_299="2026-03-25T14:34:59Z"   # +299s — just inside
T_300="2026-03-25T14:35:00Z"   # +300s — exactly at expiry (not suppressed)
T_301="2026-03-25T14:35:01Z"   # +301s — just after expiry

ROUTE_WITH_WINDOW='{
  "id": "route-1",
  "conditions": {},
  "target": {"type": "slack", "channel": "#oncall"},
  "priority": 10,
  "suppression_window_seconds": 300
}'

mk_alert() {
  local id="$1" ts="$2" svc="${3:-payment-api}"
  printf '{"id":"%s","severity":"critical","service":"%s","group":"backend","timestamp":"%s"}' \
    "$id" "$svc" "$ts"
}

# First alert routes normally
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" -d "$ROUTE_WITH_WINDOW" > /dev/null
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" -d "$(mk_alert a1 $T0)")
assert_eq "Suppression: first alert not suppressed" \
  "$(echo "$BODY" | jq -r '.suppressed')" "false"
assert_eq "Suppression: first alert routed_to set" \
  "$(echo "$BODY" | jq -r '.routed_to.route_id')" "route-1"
assert_eq "Suppression: first alert suppression_reason null" \
  "$(echo "$BODY" | jq -r '.suppression_reason')" "null"

# Second alert within window — suppressed
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" -d "$(mk_alert a2 $T_100)")
assert_eq "Suppression: second alert within window suppressed" \
  "$(echo "$BODY" | jq -r '.suppressed')" "true"
assert_eq "Suppression: suppressed alert still has routed_to" \
  "$(echo "$BODY" | jq -r '.routed_to.route_id')" "route-1"
assert_eq "Suppression: suppression_reason contains service" \
  "$(echo "$BODY" | jq -r '.suppression_reason' | grep -c payment-api)" "1"
assert_eq "Suppression: suppression_reason contains expiry 14:35:00Z" \
  "$(echo "$BODY" | jq -r '.suppression_reason' | grep -c '14:35:00Z')" "1"
assert_eq "Suppression: suppression_applied=true in evaluation_details" \
  "$(echo "$BODY" | jq -r '.evaluation_details.suppression_applied')" "true"

# Alert just before expiry — suppressed
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" -d "$(mk_alert a3 $T_299)")
assert_eq "Suppression: alert at T-299s suppressed" \
  "$(echo "$BODY" | jq -r '.suppressed')" "true"

# Alert exactly at expiry boundary — NOT suppressed
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" -d "$(mk_alert a4 $T_300)")
assert_eq "Suppression: alert at exact expiry (T+300s) not suppressed" \
  "$(echo "$BODY" | jq -r '.suppressed')" "false"

# Alert after expiry — routes again
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" -d "$ROUTE_WITH_WINDOW" > /dev/null
curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" -d "$(mk_alert a1 $T0)" > /dev/null
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" -d "$(mk_alert a2 $T_301)")
assert_eq "Suppression: alert after expiry routes normally" \
  "$(echo "$BODY" | jq -r '.suppressed')" "false"
assert_eq "Suppression: alert after expiry has no suppression_reason" \
  "$(echo "$BODY" | jq -r '.suppression_reason')" "null"

# Third alert — suppressed again under new window (T_301 + 300s = 14:40:01Z)
T_WITHIN_NEW="2026-03-25T14:36:00Z"
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d "$(mk_alert a3 $T_WITHIN_NEW)")
assert_eq "Suppression: alert within new window suppressed" \
  "$(echo "$BODY" | jq -r '.suppressed')" "true"

# Different service — not suppressed
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" -d "$ROUTE_WITH_WINDOW" > /dev/null
curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" -d "$(mk_alert a1 $T0 payment-api)" > /dev/null
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d "$(mk_alert a2 $T_100 auth-service)")
assert_eq "Suppression: different service not suppressed" \
  "$(echo "$BODY" | jq -r '.suppressed')" "false"

# payment-api still suppressed independently
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d "$(mk_alert a3 $T_100 payment-api)")
assert_eq "Suppression: original service still suppressed" \
  "$(echo "$BODY" | jq -r '.suppressed')" "true"

# Zero suppression window — never suppresses
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"no-win","conditions":{},"target":{"type":"slack","channel":"#x"},"priority":1,"suppression_window_seconds":0}' > /dev/null
curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" -d "$(mk_alert a1 $T0)" > /dev/null
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" -d "$(mk_alert a2 $T_100)")
assert_eq "Suppression: zero window never suppresses" \
  "$(echo "$BODY" | jq -r '.suppressed')" "false"

# ---------------------------------------------------------------------------
# Active Hours & Timezones
# ---------------------------------------------------------------------------
# America/New_York on 2026-03-25 = UTC-4 (after DST March 8)
# Window 09:00–17:00 ET = 13:00–21:00 UTC
#
# INSIDE_ET   = 14:00 UTC = 10:00 ET ✓
# BEFORE_ET   = 12:59 UTC = 08:59 ET ✗
# AT_ET_START = 13:00 UTC = 09:00 ET ✓ (inclusive)
# AT_ET_END   = 21:00 UTC = 17:00 ET ✗ (exclusive)
# AFTER_ET    = 22:00 UTC = 18:00 ET ✗

echo ""
echo "=== Active Hours & Timezones ==="

INSIDE_ET="2026-03-25T14:00:00Z"
BEFORE_ET="2026-03-25T12:59:00Z"
AT_ET_START="2026-03-25T13:00:00Z"
AT_ET_END="2026-03-25T21:00:00Z"
AFTER_ET="2026-03-25T22:00:00Z"

AH_ROUTE='{
  "id": "et-route",
  "conditions": {},
  "target": {"type": "slack", "channel": "#oncall"},
  "priority": 10,
  "active_hours": {"timezone": "America/New_York", "start": "09:00", "end": "17:00"}
}'

mk_ah_alert() {
  printf '{"id":"%s","severity":"critical","service":"svc","group":"backend","timestamp":"%s"}' \
    "$1" "$2"
}

# Alert inside ET window routes
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" -d "$AH_ROUTE" > /dev/null
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d "$(mk_ah_alert a1 $INSIDE_ET)")
assert_eq "ActiveHours: inside ET window routes" \
  "$(echo "$BODY" | jq -r '.routed_to.route_id')" "et-route"

# Alert before ET window — unrouted
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d "$(mk_ah_alert a2 $BEFORE_ET)")
assert_eq "ActiveHours: before ET window unrouted" \
  "$(echo "$BODY" | jq -r '.routed_to')" "null"
assert_eq "ActiveHours: before window routes_matched=0" \
  "$(echo "$BODY" | jq '.evaluation_details.routes_matched')" "0"
assert_eq "ActiveHours: before window matched_routes empty" \
  "$(echo "$BODY" | jq '.matched_routes | length')" "0"

# At start boundary — inclusive
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d "$(mk_ah_alert a3 $AT_ET_START)")
assert_eq "ActiveHours: at start boundary (09:00 ET) routes" \
  "$(echo "$BODY" | jq -r '.routed_to.route_id')" "et-route"

# At end boundary — exclusive
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d "$(mk_ah_alert a4 $AT_ET_END)")
assert_eq "ActiveHours: at end boundary (17:00 ET) not routed" \
  "$(echo "$BODY" | jq -r '.routed_to')" "null"

# After ET window
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d "$(mk_ah_alert a5 $AFTER_ET)")
assert_eq "ActiveHours: after ET window unrouted" \
  "$(echo "$BODY" | jq -r '.routed_to')" "null"

# Fallback to lower-priority always-active route when outside active window
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" -d "$AH_ROUTE" > /dev/null
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"always","conditions":{},"target":{"type":"email","address":"ops@example.com"},"priority":1}' > /dev/null
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d "$(mk_ah_alert a1 $BEFORE_ET)")
assert_eq "ActiveHours: fallback to always-on route outside window" \
  "$(echo "$BODY" | jq -r '.routed_to.route_id')" "always"
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d "$(mk_ah_alert a2 $INSIDE_ET)")
assert_eq "ActiveHours: higher-priority active route wins inside window" \
  "$(echo "$BODY" | jq -r '.routed_to.route_id')" "et-route"

# Midnight-crossing window (22:00–06:00 UTC)
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"night","conditions":{},"target":{"type":"slack","channel":"#night"},"priority":10,"active_hours":{"timezone":"UTC","start":"22:00","end":"06:00"}}' > /dev/null
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d "$(mk_ah_alert a1 '2026-03-25T23:30:00Z')")
assert_eq "ActiveHours: midnight-crossing — 23:30 UTC in window" \
  "$(echo "$BODY" | jq -r '.routed_to.route_id')" "night"
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d "$(mk_ah_alert a2 '2026-03-26T03:00:00Z')")
assert_eq "ActiveHours: midnight-crossing — 03:00 UTC in window" \
  "$(echo "$BODY" | jq -r '.routed_to.route_id')" "night"
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d "$(mk_ah_alert a3 '2026-03-25T08:00:00Z')")
assert_eq "ActiveHours: midnight-crossing — 08:00 UTC outside window" \
  "$(echo "$BODY" | jq -r '.routed_to')" "null"
BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d "$(mk_ah_alert a4 '2026-03-26T06:00:00Z')")
assert_eq "ActiveHours: midnight-crossing — 06:00 UTC at end boundary (exclusive)" \
  "$(echo "$BODY" | jq -r '.routed_to')" "null"

# Route without active_hours is always active
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"always","conditions":{},"target":{"type":"slack","channel":"#all"},"priority":5}' > /dev/null
for TS in "$INSIDE_ET" "$BEFORE_ET" "$AFTER_ET"; do
  BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
    -d "$(mk_ah_alert "a-$TS" "$TS")")
  assert_eq "ActiveHours: no active_hours route always matches ($TS)" \
    "$(echo "$BODY" | jq -r '.routed_to.route_id')" "always"
done

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"

[ "$FAIL" -eq 0 ]
