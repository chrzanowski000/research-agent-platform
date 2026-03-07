#!/usr/bin/env bash
set -euo pipefail

COMPOSE_CMD="op run --env-file=.env_tpl -- docker compose"

cleanup() {
  echo ""
  echo "⚠️  Shutting down..."
  $COMPOSE_CMD down
}
trap cleanup EXIT INT TERM

$COMPOSE_CMD up --build --remove-orphans &
COMPOSE_PID=$!

# Monitor containers for failures
while kill -0 $COMPOSE_PID 2>/dev/null; do
  FAILED=$($COMPOSE_CMD ps --format json 2>/dev/null \
    | grep -E '"State":"exited"|"Health":"unhealthy"' \
    | grep -oP '"Name":"[^"]*"' \
    | grep -oP '[^"]+$' || true)

  if [ -n "$FAILED" ]; then
    echo ""
    echo "❌ WARNING: Container failure detected: $FAILED"
    echo "--- Logs ---"
    for name in $FAILED; do
      echo "=== $name ==="
      $COMPOSE_CMD logs --tail=20 "$name" 2>&1 || true
    done
    echo "------------"
  fi

  sleep 5
done

wait $COMPOSE_PID
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
  echo ""
  echo "❌ WARNING: docker compose exited with code $EXIT_CODE"
  echo "--- Failed container logs ---"
  $COMPOSE_CMD ps --format json 2>/dev/null \
    | grep -oP '"Name":"[^"]*"' \
    | grep -oP '[^"]+$' \
    | while read -r name; do
        STATUS=$($COMPOSE_CMD ps --format json 2>/dev/null | grep "\"$name\"" | grep -oP '"State":"[^"]*"' | grep -oP '[^"]+$' | tail -1 || echo "unknown")
        if [ "$STATUS" = "exited" ] || [ "$STATUS" = "unhealthy" ]; then
          echo "=== $name (status: $STATUS) ==="
          $COMPOSE_CMD logs --tail=30 "$name" 2>&1 || true
        fi
      done
fi
