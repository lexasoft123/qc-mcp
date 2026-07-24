#!/bin/bash
# One-shot setup: venv + qc-mcp package + Claude Code registration.
#
#   ./install.sh            # user scope (default): 'quad-cortex' available in
#                           # EVERY Claude Code session, any folder
#   ./install.sh --local    # this-folder-only registration instead
#
# Idempotent — safe to re-run (also after moving the repo: paths re-register).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

SCOPE="user"
[ "${1:-}" = "--local" ] && SCOPE="local"

PYTHON=${PYTHON:-python3}
if [ ! -x .venv/bin/python ]; then
  echo "==> Creating venv"
  "$PYTHON" -m venv .venv
fi
echo "==> Installing qc-mcp (editable, with GUI extras)"
./.venv/bin/pip install -q -e '.[gui]'
BIN="$HERE/.venv/bin/qc-mcp"
[ -x "$BIN" ] || { echo "install failed: $BIN missing"; exit 1; }

if command -v claude >/dev/null 2>&1; then
  echo "==> Registering with Claude Code (scope: $SCOPE)"
  # drop stale user/local registrations, then add fresh. Do NOT touch project
  # scope — that's the repo's committed .mcp.json (removing rewrites the file).
  for s in user local; do
    claude mcp remove quad-cortex -s "$s" >/dev/null 2>&1 || true
  done
  claude mcp add --scope "$SCOPE" quad-cortex -- "$BIN"
  claude mcp list 2>/dev/null | grep -i quad-cortex || true
else
  echo "claude CLI not found — register manually once it's installed:"
  echo "  claude mcp add --scope user quad-cortex -- \"$BIN\""
fi

cat <<EOF

Done. 'quad-cortex' is registered at $SCOPE scope$([ "$SCOPE" = user ] && echo " — available in every folder").
Note: the repo's skills + CLAUDE.md knowledge still load only for sessions
opened INSIDE this repo; from other folders you get device control alone.
Next: ./interceptor/build.sh (once, needs Cortex Control installed), then just
ask Claude to connect — it offers bridge/direct and launches the bridge itself.
EOF
