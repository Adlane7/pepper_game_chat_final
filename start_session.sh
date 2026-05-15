#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Optional local config. Keep this file private; it is ignored by git.
if [[ -f "${SCRIPT_DIR}/session_config.env" ]]; then
  # shellcheck disable=SC1091
  source "${SCRIPT_DIR}/session_config.env"
fi

# ==== CONFIG ====
: "${AGX_USER:?Set AGX_USER in session_config.env or the environment}"
: "${AGX_HOST:?Set AGX_HOST in session_config.env or the environment}"
: "${AGX_DIR:?Set AGX_DIR in session_config.env or the environment}"
: "${PEPPER_IP:?Set PEPPER_IP in session_config.env or the environment}"

START_GPT="${START_GPT:-true}"
PYTHON3="${PYTHON3:-python3}"

# Local ports
ASR_PUB_PORT=5556   # external_mic publishes here (on AGX)
CTRL_PUB_PORT=5557  # main.py binds here
# =================

MAIN_PC_IP=$(ip route get "${AGX_HOST}" | grep -oP 'src \K\S+')
echo "== This Main PC's IP is: ${MAIN_PC_IP} =="

DIALOGUE_PID=""

# ---------- Helpers ----------

wait_for_remote_port() {
  local HOST="$1"; local PORT="$2"; local TIMEOUT="${3:-90}"
  echo -n "Waiting for ${HOST}:${PORT} "
  for _ in $(seq 1 "$TIMEOUT"); do
    if nc -z -w 1 "${HOST}" "${PORT}" 2>/dev/null; then
      echo "→ open."
      return 0
    fi
    echo -n "."
    sleep 1
  done
  echo
  echo "ERROR: Port ${HOST}:${PORT} did not open in ${TIMEOUT}s." >&2
  exit 1
}

cleanup() {
  local status=$?   # capture exit status FIRST
  set +e

  echo
  echo "==============================="
  echo "== [CLEANUP] Stopping session =="
  echo "==============================="

  echo "== [CLEANUP] Stopping AGX (camera + recorder + mic) =="

  # 1) Try your normal stop script
  ssh -o StrictHostKeyChecking=no "${AGX_USER}@${AGX_HOST}" \
    "bash -lc 'cd ${AGX_DIR} && ./agx_stop.sh'" || \
    echo "WARN: agx_stop.sh failed or AGX not reachable."

  # 2) HARD STOP the mic on AGX (this is what you're missing)
  echo "== [CLEANUP] Force-stopping mic publisher on AGX =="
  ssh -o StrictHostKeyChecking=no "${AGX_USER}@${AGX_HOST}" \
    "bash -lc 'pkill -2 -f external_mic_main.py 2>/dev/null || true; \
              sleep 0.3; \
              pkill -f external_mic_main.py 2>/dev/null || true; \
              fuser -k -n tcp ${ASR_PUB_PORT} 2>/dev/null || true; \
              lsof -iTCP:${ASR_PUB_PORT} -sTCP:LISTEN 2>/dev/null || true'" \
    || echo "WARN: Could not force-stop mic on AGX."

  if [[ -n "${DIALOGUE_PID}" ]] && kill -0 "${DIALOGUE_PID}" 2>/dev/null; then
    echo "== [CLEANUP] Stopping local dialogue server (PID ${DIALOGUE_PID}) =="
    kill "${DIALOGUE_PID}" || true
  fi

  # Extra local safety (fine to keep)
  pkill -f startDialogueServer.py 2>/dev/null || true

  echo "== [CLEANUP] Done. Exit status: ${status} =="
  exit "${status}"
}
trap cleanup EXIT INT TERM

# ---------- START SECTION ----------

echo "== Start camera + recorder + mic on AGX =="
ssh -o StrictHostKeyChecking=no "${AGX_USER}@${AGX_HOST}" \
  "export MAIN_PC_IP='${MAIN_PC_IP}'; bash -lc 'cd ${AGX_DIR} && ./agx_start.sh'"

echo "== Waiting for Whisper to finish loading on AGX (/tmp/whisper_ready) =="
ssh -o StrictHostKeyChecking=no "${AGX_USER}@${AGX_HOST}" \
  "bash -lc 'for i in {1..240}; do \
      test -f /tmp/whisper_ready && echo \"Whisper READY\" && exit 0; \
      sleep 1; \
    done; \
    echo \"ERROR: Whisper not ready after 240s\" >&2; exit 1'"
# 1) Dialogue server on main PC
if $START_GPT; then
  echo "== Starting dialogue server with: ${PYTHON3} =="

  # If an old server is still running, kill it (port conflicts are super common)
  pkill -f "startDialogueServer.py" 2>/dev/null || true
  sleep 0.5

  nohup "$PYTHON3" "${SCRIPT_DIR}/startDialogueServer.py" > "${SCRIPT_DIR}/gpt_server.out" 2>&1 &
  DIALOGUE_PID=$!
  echo "   Dialogue server PID: ${DIALOGUE_PID}"

  sleep 2

  # ---- HARD health check: did it die? ----
  if ! kill -0 "$DIALOGUE_PID" 2>/dev/null; then
    echo "ERROR: Dialogue server crashed right after start."
    echo "---- gpt_server.out (last 200 lines) ----"
    tail -n 200 "${SCRIPT_DIR}/gpt_server.out"
    exit 1
  fi

  # ---- SOFT health check: did it print startup line? ----
  if ! grep -q "Starting OpenAI chat server" "${SCRIPT_DIR}/gpt_server.out"; then
    echo "WARN: No startup banner found yet. Showing last 80 lines:"
    tail -n 80 "${SCRIPT_DIR}/gpt_server.out"
  else
    echo "== Dialogue server looks up =="
  fi
fi

# 2) Wait for mic publisher running on AGX
echo "== Waiting for mic publisher (on AGX) =="
wait_for_remote_port "${AGX_HOST}" "${ASR_PUB_PORT}" 170

# 3) Pepper main (blocks)
if lsof -iTCP -sTCP:LISTEN -P 2>/dev/null | awk '{print $9}' | grep -q ":${CTRL_PUB_PORT}$"; then
  echo "WARN: Port ${CTRL_PUB_PORT} already in use. Is another main.py running?"
fi

echo "== Starting Pepper main (this will block until the session ends) =="
echo "   Normal finish: say goodbye during closing free chat; cleanup runs automatically."
echo "   Manual stop only: press Ctrl+C here, or run ./stop_session.sh from another terminal."

python2 main.py --pip "${PEPPER_IP}" --asr-host "${AGX_HOST}" --emotion-host "${AGX_HOST}"

echo "== Pepper main finished =="
# After this, trap will run cleanup() automatically
