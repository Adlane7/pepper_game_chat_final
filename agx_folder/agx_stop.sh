#!/usr/bin/env bash

set -euo pipefail

SRC_DIR="${SRC_DIR:-$(pwd)}"
LOGDIR="${LOGDIR:-${SRC_DIR}/logs}"

kill_pidfile_gently() {
  local f="$1"
  local name="$2"   # just for logging

  if [[ -f "$f" ]]; then
    PID=$(cat "$f" || true)

    if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
      echo "[INFO] Stopping $name (PID $PID) with SIGINT..."
      # SIGINT = like Ctrl+C, ros2 bag handles it nicely
      kill -2 "$PID" || true

      # Wait up to ~20s for clean shutdown
      for i in $(seq 1 40); do
        if kill -0 "$PID" 2>/dev/null; then
          sleep 0.5
        else
          echo "[INFO] $name stopped cleanly."
          break
        fi
      done

      # If it's STILL alive, fall back to SIGTERM
      if kill -0 "$PID" 2>/dev/null; then
        echo "[WARN] $name still running, sending SIGTERM..."
        kill "$PID" || true
      fi
    else
      echo "[INFO] $name PID file exists but process is not running."
    fi

    rm -f "$f"
  fi
}

# 1) Stop the recorder FIRST, gently, and wait for it
kill_pidfile_gently "$LOGDIR/recorder.pid" "rosbag recorder"

# 2) Then stop realsense node (less important if it dies fast)
kill_pidfile_gently "$LOGDIR/realsense.pid" "realsense node"

# 3) As a last resort, clean up any leftovers
pkill -f 'realsense2_camera.*rs_launch.py' || true

# OPTIONAL: if you keep this, make it also SIGINT instead of default
pkill -2 -f 'ros2 bag record' || true

pkill -f 'external_mic_main.py' || true

sleep 1
echo "AGX processes stopped."
