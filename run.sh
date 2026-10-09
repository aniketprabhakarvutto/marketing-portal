#!/usr/bin/env bash
# Start Vortex Veri.
# No dependencies and no virtualenv — this runs on the Python that ships with macOS.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "python3 was not found. Install it with:  xcode-select --install" >&2
  exit 1
fi

"$PY" - <<'EOF' || { echo "This portal needs Python 3.9 or newer." >&2; exit 1; }
import sys
sys.exit(0 if sys.version_info >= (3, 9) else 1)
EOF

# macOS tags anything written by an app as quarantined, and Gatekeeper then
# refuses to let it be double-clicked ("Apple could not verify..."). Running
# this script from a terminal is already an explicit decision to trust it, so
# clear that tag on the launcher next to it — and say so rather than doing it
# silently. Nothing outside this folder is touched.
if [ "$(uname)" = "Darwin" ] && command -v xattr >/dev/null 2>&1; then
  if xattr "Start Portal.command" 2>/dev/null | grep -q com.apple.quarantine; then
    xattr -d com.apple.quarantine "Start Portal.command" 2>/dev/null \
      && echo "→ Unblocked 'Start Portal.command' — you can double-click it from now on."
  fi
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "→ Created .env from the template."
  echo "  It starts with demo data; add your credentials whenever you're ready."
fi

# The local model is optional. Start the bundled runtime only when present,
# and stop only the process started by this script when the portal exits.
LOCAL_AI_PID=""
if [ -x .local-ai/runtime/ollama ] && command -v curl >/dev/null 2>&1; then
  if ! curl -fsS --max-time 1 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    OLLAMA_HOST=127.0.0.1:11434 \
    OLLAMA_MODELS="$PWD/.local-ai/models" \
    OLLAMA_NO_CLOUD=1 \
      .local-ai/runtime/ollama serve > .local-ai/ollama.log 2>&1 &
    LOCAL_AI_PID=$!
    echo "→ Starting local AI model runtime."
  fi
fi

stop_local_ai() {
  if [ -n "$LOCAL_AI_PID" ]; then
    kill "$LOCAL_AI_PID" 2>/dev/null || true
  fi
}
trap stop_local_ai EXIT

"$PY" -m portal.cli serve
