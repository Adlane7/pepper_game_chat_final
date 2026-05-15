# Pepper Game Chat

Interactive Pepper robot session code with:

- Pepper speech/tablet output through NAOqi
- External microphone speech recognition on an AGX machine
- GPT-5-compatible OpenAI free chat and word games
- ROS2/RealSense recording support on AGX
- Session logging and recording helpers

The exercise flow is:

1. Free chat
2. Word association
3. Word chain
4. List recall: one continuous minute of fruit recall
5. Closing free chat

## Privacy First

Do not commit real machine IPs, hostnames, SSH usernames, local paths, or API
keys.

This repo uses `session_config.env.example` as a safe template. Your real local
configuration should live in `session_config.env`, which is ignored by git.

The `.env` file used for OpenAI credentials is also ignored by git.

## Acknowledgements And Attribution

The OpenAI/Pepper dialogue layer in `oaichat/` and `startDialogueServer.py`
is adapted from the MIT-licensed
[`ilabsweden/pepperchat`](https://github.com/ilabsweden/pepperchat) project.
This project updates that ChatGPT-style dialogue layer for GPT-5-compatible
OpenAI Responses API usage.

The AGX external microphone pipeline, recording scripts, list-recall behavior,
session orchestration, logging, and audio/experiment workflow code are project
specific additions in this repository.

## Local Configuration

Copy the template:

```bash
cp session_config.env.example session_config.env
```

Edit `session_config.env` with your real local values:

```bash
AGX_USER="your-agx-ssh-user"
AGX_HOST="your-agx-host-or-ip"
AGX_DIR="/path/to/agx/project"

PEPPER_IP="your-pepper-host-or-ip"
PYTHON3="/path/to/project/venv/bin/python3"

START_GPT=true
OPENAI_MODEL="gpt-5-chat-latest"
PEPPER_TABLET_BASE_URL="http://pepper-tablet/apps/myapp"
```

Keep `session_config.env` private.

## Machines And Roles

### Main PC

The main PC runs:

- `start_session.sh`
- `main.py`
- `startDialogueServer.py`
- the local OpenAI/NAOqi control flow

### AGX

The AGX runs:

- `agx_start.sh`
- `agx_stop.sh`
- the external microphone controller
- ROS2/RealSense recording

Important naming note:

In this repo, the editable microphone copy is:

```bash
agx_folder/agx-external_mic.py
```

On the AGX, the live script is expected to be named:

```bash
external_mic_main.py
```

When deploying microphone changes, copy:

```bash
scp agx_folder/agx-external_mic.py "$AGX_USER@$AGX_HOST:$AGX_DIR/external_mic_main.py"
```

`agx_start.sh` intentionally launches `external_mic_main.py`.

## Network Ports

| Port | Direction | Purpose |
| --- | --- | --- |
| `5556` | AGX to main PC | ASR text publisher |
| `5557` | Main PC to AGX | Mic control commands |
| `5560` | AGX to main PC | Emotion label subscriber, if enabled |

Supported mic control commands:

- `PAUSE`
- `RESUME`
- `MODE:CONTINUOUS`
- `MODE:FIXED`

## Requirements

### Main PC Python 2

`main.py` uses Python 2 because of the NAOqi Python SDK.

Expected:

- Python 2
- NAOqi Python SDK
- `numpy`
- `python-dotenv`
- `zmq`

### Main PC Python 3

`startDialogueServer.py` uses Python 3.

The dialogue layer reads `OPENAI_MODEL` from the environment and defaults to:

```bash
gpt-5-chat-latest
```

Expected:

- `python-dotenv`
- `zmq`
- `openai`

### AGX Python 3

The AGX mic controller runs with Python 3.

Expected:

- `pyaudio`
- `webrtcvad`
- `pyzmq`
- `openai-whisper`
- `torch`
- `numpy`
- optional: `scipy`, `num2words`

The AGX also needs ROS2 Humble and the RealSense ROS2 stack.

## Deploying To AGX

After setting `session_config.env`, deploy:

```bash
source session_config.env
scp agx_folder/agx_start.sh "$AGX_USER@$AGX_HOST:$AGX_DIR/agx_start.sh"
scp agx_folder/agx_stop.sh "$AGX_USER@$AGX_HOST:$AGX_DIR/agx_stop.sh"
scp agx_folder/agx-external_mic.py "$AGX_USER@$AGX_HOST:$AGX_DIR/external_mic_main.py"
ssh "$AGX_USER@$AGX_HOST" "chmod +x '$AGX_DIR/agx_start.sh' '$AGX_DIR/agx_stop.sh'"
```

## Running A Session

From the main PC:

```bash
./start_session.sh
```

What happens:

1. `start_session.sh` loads `session_config.env`.
2. The main PC computes its reachable IP for the AGX.
3. The main PC SSHes to the AGX and runs `agx_start.sh`.
4. The AGX starts RealSense, rosbag recording, and `external_mic_main.py`.
5. The main PC waits for Whisper readiness.
6. The main PC starts `startDialogueServer.py`.
7. The main PC runs `main.py` and passes the configured AGX host.

Normal completion:

1. At the end of the exercises, `core/session_controller.py` enters the closing
   free-chat phase.
2. Pepper asks whether the participant wants to say anything else, or to say
   `goodbye` to finish.
3. If the participant says `goodbye`, `bye`, or `exit`, Pepper says the final
   goodbye and `main.py` returns.
4. If the participant does not explicitly say goodbye, the closing phase times
   out after about 60 seconds and Pepper still says the final goodbye.
5. When `main.py` exits, `start_session.sh` runs its cleanup trap automatically.
   This stops the AGX camera, recorder, mic process, and local dialogue server.

In the normal flow, you do not need to run `stop_session.sh` after the goodbye.

Use manual stop only if the session hangs, crashes, or you need to interrupt it:

```bash
./stop_session.sh
```

Or press `Ctrl+C` in the running `start_session.sh` terminal. Both paths trigger
cleanup, but they are backup controls rather than the normal ending.

## List Recall Recording Behavior

List recall should record one continuous minute, not many short VAD chunks.

The intended sequence:

1. Pepper asks the participant to say they are ready.
2. Controller waits for a ready phrase.
3. Pepper says `The timer has started!` while mic is paused.
4. Controller clears stale ASR queue items.
5. Controller sends `MODE:CONTINUOUS`.
6. Controller sends `RESUME`.
7. AGX records one fixed 60 second audio segment.
8. Controller pauses the mic at time-up and waits for Whisper.
9. Controller parses fruit names and logs each item.
10. Controller clears stale queue items before moving on.

The mic payload includes:

```json
{
  "recording_mode": "fixed",
  "segment_duration": 60.0,
  "asr_text_raw": "...",
  "text": "apple, orange, banana"
}
```

The controller only accepts the fixed one-minute payload for list recall. This
prevents late free-chat or short VAD chunks from leaking into the fruit list.

## Data Outputs

Main PC:

```bash
session_log.csv
session_events/events_<agx_session_id>.jsonl
gpt_server.out
```

AGX:

```bash
recordings/YYYY-MM-DD/participant_XX/
recordings/YYYY-MM-DD/participant_XX/mic/sess-<session_id>/*.wav
recordings/YYYY-MM-DD/participant_XX/mic/sess-<session_id>/manifest.jsonl
logs/mic.out
logs/realsense.out
logs/recorder.out
```

Generated logs, recordings, `.env`, `session_config.env`, and Python cache files
are ignored by git.

## Troubleshooting

### Mic port does not open

Check:

```bash
source session_config.env
ssh "$AGX_USER@$AGX_HOST" "tail -n 200 '$AGX_DIR/logs/mic.out'"
```

Common causes:

- `external_mic_main.py` is missing on the AGX.
- `mic_env` is missing dependencies.
- The external microphone name no longer matches `wireless micro`.
- Another process is already using port `5556`.

### Whisper never becomes ready

Check:

```bash
source session_config.env
ssh "$AGX_USER@$AGX_HOST" "ls -l /tmp/whisper_ready; tail -n 200 '$AGX_DIR/logs/mic.out'"
```

### List recall still splits

Check the AGX mic manifest for:

```json
"recording_mode": "fixed"
```

If the list-recall chunks are still `recording_mode: "vad"`, the AGX is running
old mic code or the controller did not send `MODE:CONTINUOUS` before `RESUME`.

## Validation

Useful local checks:

```bash
python3 -m py_compile core/session_controller.py exercises/list_recall.py agx_folder/agx-external_mic.py
bash -n start_session.sh
bash -n stop_session.sh
bash -n agx_folder/agx_start.sh
bash -n agx_folder/agx_stop.sh
```

## File Map

```text
.
|-- start_session.sh
|-- stop_session.sh
|-- main.py
|-- startDialogueServer.py
|-- session_config.env.example
|-- core/
|   |-- session_controller.py
|   `-- logger.py
|-- exercises/
|   |-- free_chat.py
|   |-- word_association.py
|   |-- word_chain.py
|   `-- list_recall.py
|-- agx_folder/
|   |-- agx_start.sh
|   |-- agx_stop.sh
|   |-- agx-external_mic.py
|   `-- ros2_video_recorder.py
```
