#!/usr/bin/env bash
# Double-click this file in Finder to start the portal and open it in your browser.
# (Closing the Terminal window that appears stops the portal.)
#
# The browser is opened only after /readyz returns 200, so it can never reach
# the portal while it is still starting (which showed as an empty response).
# A small watchdog also restarts the portal if it exits unexpectedly.
cd "$(dirname "$0")"

PORT="$(grep -E '^PORTAL_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')"
PORT="${PORT:-8787}"
READY_URL="http://127.0.0.1:${PORT}/readyz"
READY_TIMEOUT="${READY_TIMEOUT:-120}"

wait_ready() {
  local waited=0
  until [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$READY_URL" 2>/dev/null)" = "200" ]; do
    sleep 1
    waited=$((waited + 1))
    [ "$waited" -ge "$READY_TIMEOUT" ] && return 1
  done
  return 0
}

SERVER_PID=""
trap '[ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null; exit 0' INT TERM

OPENED=0
while true; do
  ./run.sh &
  SERVER_PID=$!
  if wait_ready; then
    if [ "$OPENED" = "0" ]; then
      open "http://localhost:${PORT}"
      OPENED=1
    fi
  else
    echo "Portal did not report ready within ${READY_TIMEOUT}s — check the output above." >&2
  fi
  # Watchdog: block until the server exits, then restart it after a short pause.
  wait "$SERVER_PID"
  echo "Portal stopped (exit $?). Restarting in 5s — close this window to quit." >&2
  sleep 5
done
