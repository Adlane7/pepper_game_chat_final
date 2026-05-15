#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Optional local config. Keep this file private; it is ignored by git.
if [[ -f "${SCRIPT_DIR}/session_config.env" ]]; then
  # shellcheck disable=SC1091
  source "${SCRIPT_DIR}/session_config.env"
fi

: "${AGX_USER:?Set AGX_USER in session_config.env or the environment}"
: "${AGX_HOST:?Set AGX_HOST in session_config.env or the environment}"
: "${AGX_DIR:?Set AGX_DIR in session_config.env or the environment}"

# Stop remote (AGX)
ssh -o StrictHostKeyChecking=no ${AGX_USER}@${AGX_HOST} "bash -lc 'cd ${AGX_DIR} && ./agx_stop.sh'"

# Stop optional local helpers if you used them
pkill -f startDialogueServer.py || true
pkill -f external_mic_main.py   || true

echo "Session stopped."
