#!/usr/bin/env bash
# Double-click this file in Finder to start the portal and open it in your browser.
# (Closing the Terminal window that appears stops the portal.)
cd "$(dirname "$0")"

PORT="$(grep -E '^PORTAL_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')"
PORT="${PORT:-8787}"

# Give the server a moment to bind before the browser goes looking for it.
( sleep 3; open "http://localhost:${PORT}" ) &

./run.sh
