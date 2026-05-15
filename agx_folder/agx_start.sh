#!/usr/bin/env bash
# --- Cleanup old processes ---
# --- Auto-generate participant ID ---
SRC_DIR="${SRC_DIR:-$(pwd)}"
DATE_DIR="$(date +'%Y-%m-%d')"
BASE_ROOT="${RECORDINGS_ROOT:-${SRC_DIR}/recordings/${DATE_DIR}}"

mkdir -p "$BASE_ROOT"

# Count existing participants
count=$(ls -d ${BASE_ROOT}/participant_* 2>/dev/null | wc -l)

# Next participant number (zero-padded)
next=$((count + 1))
PARTICIPANT_ID=$(printf "participant_%02d" "$next")

echo "[Meta] Auto-assigned PARTICIPANT_ID = $PARTICIPANT_ID"

# Create participant root folder
PART_DIR="${BASE_ROOT}/${PARTICIPANT_ID}"
mkdir -p "${PART_DIR}/rosbag"


LOGDIR="${LOGDIR:-${SRC_DIR}/logs}"
mkdir -p "$LOGDIR"

for name in realsense recorder mic; do
  PIDFILE="$LOGDIR/${name}.pid"
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "[Clean] Stopping previous $name..."
    kill "$(cat "$PIDFILE")" || true
    sleep 1
  fi
done

pkill -f realsense2_camera_node || true
pkill -f 'ros2 bag record' || true
pkill -f 'external_mic_main.py' || true
fuser -k -n tcp 5556 2>/dev/null || true
sleep 0.2

# Ensure camera devices are free
for dev in /dev/video*; do
  [ -e "$dev" ] || continue
  echo "[Check] Ensuring $dev is free..."
  while fuser "$dev" >/dev/null 2>&1; do
    echo "  -> $dev busy, waiting..."
    sleep 1
  done
done

set -euo pipefail

# ---- ROS env helper ----
source_ros() {
  set +u
  . /opt/ros/humble/setup.bash
  [ -f ~/ros2_humble/install/local_setup.bash ] && . ~/ros2_humble/install/local_setup.bash
  set -u
}

# ---- Wait for camera topic ----
wait_for_camera_topic() {
  local TIMEOUT="${1:-60}"
  local SLEEP="1"
  local CANDIDATES=("/camera/camera/aligned_depth_to_color/image_raw")
  echo "[Wait] Waiting for RealSense topic..."
  source_ros
  local elapsed=0
  while (( elapsed < TIMEOUT )); do
    local topics
    topics="$(ros2 topic list 2>/dev/null || echo '')"
    for t in "${CANDIDATES[@]}"; do
      if echo "$topics" | grep -qx "$t"; then
        echo "[Wait] Found topic: $t"
        return 0
      fi
    done
    sleep "$SLEEP"; (( elapsed += SLEEP ))
  done
  echo "[Wait][ERROR] No RealSense topic found." >&2
  return 1
}

# =========================
# 1) Launch RealSense
# =========================
nohup bash -lc '
  set -euo pipefail
  set +u
  . /opt/ros/humble/setup.bash
  [ -f ~/ros2_humble/install/local_setup.bash ] && . ~/ros2_humble/install/local_setup.bash
  set -u

  echo "[RealSense] ros2 at: $(command -v ros2)"
  ros2 launch realsense2_camera rs_launch.py \
    enable_color:=true \
    enable_depth:=true \
    align_depth.enable:=true \
    rgb_camera.color_profile:=640x480x30 \
    depth_module.depth_profile:=640x480x30
' >"$LOGDIR/realsense.out" 2>&1 & echo $! > "$LOGDIR/realsense.pid"

# Wait for topics
wait_for_camera_topic 60 || true
sleep 1

# =========================
# 2) Shared session timestamp
# =========================
SESSION_START_EPOCH_S=$(python3 -c "import time; print(f'{time.time():.6f}')")
echo "[Meta] Shared session start epoch ${SESSION_START_EPOCH_S}"

REC_DIR="${PART_DIR}/rosbag"
mkdir -p "$REC_DIR"
BAG_NAME="rec_$(date +'%Y-%m-%dT%H-%M-%S')"
BAG_PATH="${REC_DIR}/${BAG_NAME}"

META_FILE="${BAG_PATH}.json"
echo "{\"session_start_epoch_s\": ${SESSION_START_EPOCH_S}}" > "${META_FILE}"
echo "[Meta] Wrote session_start_epoch_s to ${META_FILE}"

export SESSION_START_EPOCH_S

# =========================
# 3) Start ROS bag recorder
# =========================
echo "[Recorder] Starting ros2 bag record → ${BAG_PATH}"
nohup bash -lc "
  set -euo pipefail
  set +u
  . /opt/ros/humble/setup.bash
  [ -f ~/ros2_humble/install/local_setup.bash ] && . ~/ros2_humble/install/local_setup.bash
  set -u

  ros2 bag record -o '${BAG_PATH}' \
    /camera/camera/color/image_raw \
    /camera/camera/aligned_depth_to_color/image_raw \
    /camera/camera/color/camera_info \
    /camera/camera/aligned_depth_to_color/camera_info \
    /tf_static
" >"$LOGDIR/recorder.out" 2>&1 & echo $! > "$LOGDIR/recorder.pid"

# =========================
# 4) Start microphone script
# =========================
rm -f /tmp/whisper_ready 2>/dev/null || true
echo "[Mic] Starting external_mic_main.py on AGX..."
nohup bash -lc "
  set -euo pipefail
  cd ${SRC_DIR}
  SESSION_START_EPOCH_S=${SESSION_START_EPOCH_S} \
  PARTICIPANT_ID=${PARTICIPANT_ID} \
  MAIN_PC_IP=${MAIN_PC_IP:-127.0.0.1} \
  ${SRC_DIR}/mic_env/bin/python3 external_mic_main.py

" >"$LOGDIR/mic.out" 2>&1 & echo $! > "$LOGDIR/mic.pid"
echo "AGX started. PIDs:"
cat "$LOGDIR/realsense.pid" "$LOGDIR/recorder.pid" "$LOGDIR/mic.pid"
echo "[Wait] Waiting for mic publisher port 5556..."
MIC_PID="$(cat "$LOGDIR/mic.pid" 2>/dev/null || echo "")"

for i in $(seq 1 300); do
  # If mic died, print logs and fail fast
  if [[ -n "$MIC_PID" ]] && ! kill -0 "$MIC_PID" 2>/dev/null; then
    echo "[Mic][ERROR] external_mic_main.py exited early."
    echo "---- mic.out (last 200 lines) ----"
    tail -n 200 "$LOGDIR/mic.out" || true
    exit 1
  fi

  # Port up?
  if ss -lnt 2>/dev/null | awk '{print $4}' | grep -qE '^(0\.0\.0\.0|::):5556$'; then
    echo "[Wait] Mic port is listening on all interfaces."
    break
  fi

  sleep 1
done

# After loop: if still not listening, fail and show logs
if ! ss -lnt 2>/dev/null | awk '{print $4}' | grep -qE '^(0\.0\.0\.0|::):5556$'; then
  echo "[Mic][ERROR] Port 5556 not listening on all interfaces after 300s."
  tail -n 200 "$LOGDIR/mic.out" || true
  exit 1
fi

RUN_META="${PART_DIR}/run_meta.json"
cat > "${RUN_META}" <<EOF
{
  "date_dir": "${DATE_DIR}",
  "participant_id": "${PARTICIPANT_ID}",
  "session_start_epoch_s": ${SESSION_START_EPOCH_S},
  "bag_path": "${BAG_PATH}",
  "mic_dir": "${PART_DIR}/mic"
}
EOF
echo "[Meta] Wrote ${RUN_META}"
