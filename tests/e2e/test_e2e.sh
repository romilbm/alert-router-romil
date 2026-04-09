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
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"

[ "$FAIL" -eq 0 ]
