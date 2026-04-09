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
# Query and filtering
# ---------------------------------------------------------------------------

echo ""
echo "=== Query and filtering ==="
reset_state

curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"r1","conditions":{},"target":{"type":"slack","channel":"#x"},"priority":10}' > /dev/null

# Submit alerts with different services and severities
curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d '{"id":"f1","severity":"critical","service":"payment-api","group":"backend","timestamp":"2026-03-25T14:00:00Z"}' > /dev/null
curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d '{"id":"f2","severity":"warning","service":"auth-service","group":"backend","timestamp":"2026-03-25T14:00:00Z"}' > /dev/null
curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d '{"id":"f3","severity":"critical","service":"payment-api","group":"backend","timestamp":"2026-03-25T14:00:00Z"}' > /dev/null

# No filter — returns all
TOTAL=$(curl -s "$BASE/alerts" | jq '.total')
assert_eq "Filter: no filter returns all 3 alerts" "$TOTAL" "3"

# Filter by service
TOTAL=$(curl -s "$BASE/alerts?service=payment-api" | jq '.total')
assert_eq "Filter: service=payment-api returns 2" "$TOTAL" "2"

TOTAL=$(curl -s "$BASE/alerts?service=auth-service" | jq '.total')
assert_eq "Filter: service=auth-service returns 1" "$TOTAL" "1"

TOTAL=$(curl -s "$BASE/alerts?service=unknown" | jq '.total')
assert_eq "Filter: service=unknown returns 0" "$TOTAL" "0"

# Filter by severity
TOTAL=$(curl -s "$BASE/alerts?severity=critical" | jq '.total')
assert_eq "Filter: severity=critical returns 2" "$TOTAL" "2"

TOTAL=$(curl -s "$BASE/alerts?severity=warning" | jq '.total')
assert_eq "Filter: severity=warning returns 1" "$TOTAL" "1"

# Filter by routed
TOTAL=$(curl -s "$BASE/alerts?routed=true" | jq '.total')
assert_eq "Filter: routed=true returns 3 (all routed)" "$TOTAL" "3"

# Submit an unrouted alert (no matching route for warning on a severity-filtered route)
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"r2","conditions":{"severity":["warning"]},"target":{"type":"slack","channel":"#y"},"priority":10}' > /dev/null
curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d '{"id":"u1","severity":"critical","service":"svc","group":"g","timestamp":"2026-03-25T14:00:00Z"}' > /dev/null

TOTAL=$(curl -s "$BASE/alerts?routed=false" | jq '.total')
assert_eq "Filter: routed=false returns 1 unrouted alert" "$TOTAL" "1"

# Filter by suppressed
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"r3","conditions":{},"target":{"type":"slack","channel":"#z"},"priority":10,"suppression_window_seconds":300}' > /dev/null
curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d '{"id":"s1","severity":"critical","service":"svc","group":"g","timestamp":"2026-03-25T14:00:00Z"}' > /dev/null
curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d '{"id":"s2","severity":"critical","service":"svc","group":"g","timestamp":"2026-03-25T14:00:00Z"}' > /dev/null

TOTAL=$(curl -s "$BASE/alerts?suppressed=true" | jq '.total')
assert_eq "Filter: suppressed=true returns 1" "$TOTAL" "1"

TOTAL=$(curl -s "$BASE/alerts?suppressed=false" | jq '.total')
assert_eq "Filter: suppressed=false returns 1" "$TOTAL" "1"

# Combined filters
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"r4","conditions":{},"target":{"type":"slack","channel":"#w"},"priority":10}' > /dev/null
curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d '{"id":"c1","severity":"critical","service":"payment-api","group":"g","timestamp":"2026-03-25T14:00:00Z"}' > /dev/null
curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d '{"id":"c2","severity":"warning","service":"payment-api","group":"g","timestamp":"2026-03-25T14:00:00Z"}' > /dev/null
curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d '{"id":"c3","severity":"critical","service":"auth-service","group":"g","timestamp":"2026-03-25T14:00:00Z"}' > /dev/null

TOTAL=$(curl -s "$BASE/alerts?service=payment-api&severity=critical" | jq '.total')
assert_eq "Filter: service=payment-api&severity=critical returns 1" "$TOTAL" "1"

# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

echo ""
echo "=== Stats ==="
reset_state

# Initial stats
TOTAL=$(curl -s "$BASE/stats" | jq '.total_alerts_processed')
assert_eq "Stats: initial total_alerts_processed is 0" "$TOTAL" "0"

ROUTED=$(curl -s "$BASE/stats" | jq '.total_routed')
assert_eq "Stats: initial total_routed is 0" "$ROUTED" "0"

BY_CRIT=$(curl -s "$BASE/stats" | jq '.by_severity.critical')
assert_eq "Stats: initial by_severity.critical is 0" "$BY_CRIT" "0"

BY_ROUTE=$(curl -s "$BASE/stats" | jq '.by_route | keys | length')
assert_eq "Stats: initial by_route is empty" "$BY_ROUTE" "0"

# After routing
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"stat-r","conditions":{},"target":{"type":"slack","channel":"#s"},"priority":10}' > /dev/null
curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d '{"id":"stat1","severity":"critical","service":"payment-api","group":"g","timestamp":"2026-03-25T14:00:00Z"}' > /dev/null
curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d '{"id":"stat2","severity":"warning","service":"auth-service","group":"g","timestamp":"2026-03-25T14:00:00Z"}' > /dev/null

TOTAL=$(curl -s "$BASE/stats" | jq '.total_alerts_processed')
assert_eq "Stats: total_alerts_processed = 2 after two alerts" "$TOTAL" "2"

ROUTED=$(curl -s "$BASE/stats" | jq '.total_routed')
assert_eq "Stats: total_routed = 2" "$ROUTED" "2"

BY_CRIT=$(curl -s "$BASE/stats" | jq '.by_severity.critical')
assert_eq "Stats: by_severity.critical = 1" "$BY_CRIT" "1"

BY_WARN=$(curl -s "$BASE/stats" | jq '.by_severity.warning')
assert_eq "Stats: by_severity.warning = 1" "$BY_WARN" "1"

SVC_COUNT=$(curl -s "$BASE/stats" | jq '.by_service["payment-api"]')
assert_eq "Stats: by_service.payment-api = 1" "$SVC_COUNT" "1"

ROUTE_MATCHED=$(curl -s "$BASE/stats" | jq '.by_route["stat-r"].total_matched')
assert_eq "Stats: by_route.stat-r.total_matched = 2" "$ROUTE_MATCHED" "2"

# Stats with suppression
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"sup-r","conditions":{},"target":{"type":"slack","channel":"#s"},"priority":10,"suppression_window_seconds":300}' > /dev/null
curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d '{"id":"sup1","severity":"critical","service":"svc","group":"g","timestamp":"2026-03-25T14:00:00Z"}' > /dev/null
curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d '{"id":"sup2","severity":"critical","service":"svc","group":"g","timestamp":"2026-03-25T14:00:00Z"}' > /dev/null

SUPPRESSED=$(curl -s "$BASE/stats" | jq '.total_suppressed')
assert_eq "Stats: total_suppressed = 1" "$SUPPRESSED" "1"

ROUTED=$(curl -s "$BASE/stats" | jq '.total_routed')
assert_eq "Stats: total_routed = 1 (suppressed not double-counted)" "$ROUTED" "1"

# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

echo ""
echo "=== Dry-run ==="
reset_state

curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"dr-r","conditions":{},"target":{"type":"slack","channel":"#d"},"priority":10}' > /dev/null

# Dry-run returns correct result
BODY=$(curl -s -X POST "$BASE/test" -H "Content-Type: application/json" \
  -d '{"id":"dr1","severity":"critical","service":"svc","group":"g","timestamp":"2026-03-25T14:00:00Z"}')
ROUTE=$(echo "$BODY" | jq -r '.routed_to.route_id')
assert_eq "Dry-run: routes to correct route" "$ROUTE" "dr-r"

# Dry-run alert not stored
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/alerts/dr1")
assert_eq "Dry-run: alert not stored in /alerts/{id}" "$STATUS" "404"

TOTAL=$(curl -s "$BASE/alerts" | jq '.total')
assert_eq "Dry-run: /alerts returns 0 alerts" "$TOTAL" "0"

# Dry-run does not update stats
TOTAL=$(curl -s "$BASE/stats" | jq '.total_alerts_processed')
assert_eq "Dry-run: stats not updated" "$TOTAL" "0"

# Dry-run does not set suppression window
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"dr-r2","conditions":{},"target":{"type":"slack","channel":"#d"},"priority":10,"suppression_window_seconds":300}' > /dev/null

curl -s -X POST "$BASE/test" -H "Content-Type: application/json" \
  -d '{"id":"dry-alert","severity":"critical","service":"svc","group":"g","timestamp":"2026-03-25T14:00:00Z"}' > /dev/null

REAL=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d '{"id":"real1","severity":"critical","service":"svc","group":"g","timestamp":"2026-03-25T14:00:00Z"}')
SUPPRESSED=$(echo "$REAL" | jq '.suppressed')
assert_eq "Dry-run: real alert not suppressed after dry-run" "$SUPPRESSED" "false"

# Dry-run reads existing suppression window
REAL2=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d '{"id":"real2","severity":"critical","service":"svc","group":"g","timestamp":"2026-03-25T14:01:00Z"}')
# Second real alert within window is suppressed
SUPPRESSED2=$(echo "$REAL2" | jq '.suppressed')
assert_eq "Dry-run: real alert within window is suppressed" "$SUPPRESSED2" "true"

DRY=$(curl -s -X POST "$BASE/test" -H "Content-Type: application/json" \
  -d '{"id":"dry2","severity":"critical","service":"svc","group":"g","timestamp":"2026-03-25T14:02:00Z"}')
DRY_SUPPRESSED=$(echo "$DRY" | jq '.suppressed')
assert_eq "Dry-run: reports suppressed when within existing window" "$DRY_SUPPRESSED" "true"

# Dry-run validation error
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/test" -H "Content-Type: application/json" \
  -d '{"id":"bad","severity":"invalid","service":"s","group":"g","timestamp":"2026-03-25T14:00:00Z"}')
assert_eq "Dry-run: invalid severity returns 400" "$STATUS" "400"

# ---------------------------------------------------------------------------
# Full reset
# ---------------------------------------------------------------------------

echo ""
echo "=== Full reset ==="

# Populate state
reset_state
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"rst-r","conditions":{},"target":{"type":"slack","channel":"#r"},"priority":10,"suppression_window_seconds":300}' > /dev/null
curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d '{"id":"rst1","severity":"critical","service":"svc","group":"g","timestamp":"2026-03-25T14:00:00Z"}' > /dev/null
curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d '{"id":"rst2","severity":"critical","service":"svc","group":"g","timestamp":"2026-03-25T14:00:00Z"}' > /dev/null

# Verify populated
TOTAL=$(curl -s "$BASE/alerts" | jq '.total')
assert_eq "Reset pre-check: 2 alerts before reset" "$TOTAL" "2"

STATS=$(curl -s "$BASE/stats" | jq '.total_alerts_processed')
assert_eq "Reset pre-check: stats populated before reset" "$STATS" "2"

# Reset
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/reset")
assert_eq "Reset: returns 200" "$STATUS" "200"

BODY=$(curl -s -X POST "$BASE/reset")
assert_eq "Reset: returns {status: ok}" "$(echo "$BODY" | jq -r '.status')" "ok"

# Verify routes cleared
ROUTES=$(curl -s "$BASE/routes" | jq '. | length')
assert_eq "Reset: routes cleared" "$ROUTES" "0"

# Verify alerts cleared
TOTAL=$(curl -s "$BASE/alerts" | jq '.total')
assert_eq "Reset: alerts cleared" "$TOTAL" "0"

ALERT_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/alerts/rst1")
assert_eq "Reset: alert rst1 not found after reset" "$ALERT_STATUS" "404"

# Verify stats cleared
STATS=$(curl -s "$BASE/stats" | jq '.total_alerts_processed')
assert_eq "Reset: stats.total_alerts_processed = 0" "$STATS" "0"

BY_ROUTE=$(curl -s "$BASE/stats" | jq '.by_route | keys | length')
assert_eq "Reset: by_route cleared" "$BY_ROUTE" "0"

# Verify suppression window cleared — re-add route and send alert, should NOT be suppressed
curl -s -X POST "$BASE/routes" -H "Content-Type: application/json" \
  -d '{"id":"rst-r","conditions":{},"target":{"type":"slack","channel":"#r"},"priority":10,"suppression_window_seconds":300}' > /dev/null

BODY=$(curl -s -X POST "$BASE/alerts" -H "Content-Type: application/json" \
  -d '{"id":"rst3","severity":"critical","service":"svc","group":"g","timestamp":"2026-03-25T14:00:00Z"}')
assert_eq "Reset: first alert after reset not suppressed" \
  "$(echo "$BODY" | jq '.suppressed')" "false"

# Verify state fully functional after reset
assert_eq "Reset: alert routes correctly after reset" \
  "$(echo "$BODY" | jq -r '.routed_to.route_id')" "rst-r"

STATS=$(curl -s "$BASE/stats" | jq '.total_alerts_processed')
assert_eq "Reset: stats accumulate from zero after reset" "$STATS" "1"

# Reset is idempotent
reset_state
reset_state
TOTAL=$(curl -s "$BASE/alerts" | jq '.total')
assert_eq "Reset: idempotent — alerts still empty after double reset" "$TOTAL" "0"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"

[ "$FAIL" -eq 0 ]
