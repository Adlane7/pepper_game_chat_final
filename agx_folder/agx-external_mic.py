#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ExternalMicSender with WebRTC VAD for Improved Speech Detection
  - Continuously captures audio from an external microphone using WebRTC VAD.
  - Uses Whisper to convert captured audio to text.
  - Publishes recognized text over ZeroMQ on port 5556.
  - Listens for control commands ("PAUSE", "RESUME") on port 5557 to temporarily halt recording.
  - Saves WAVs using a clear, sortable naming scheme and writes a per-session manifest.jsonl

Run with:
    python3 sender.py
"""

import time
import numpy as np
import pyaudio
import webrtcvad
import zmq
import os
import threading
import whisper
import wave, tempfile
import json
import math
import uuid
import datetime
from pathlib import Path
import torch
import numpy as np
try:
    from scipy.signal import resample_poly
    from fractions import Fraction
except Exception:
    resample_poly = None
    Fraction = None

def adaptive_padding_ms(spoken_ms):
    """
    spoken_ms = how long we've been in-speech (approx)
    return required silence (ms) before we finalize the utterance
    """
    # Protect short utterances (people pause mid-thought)
    if spoken_ms < 800:
        return 900   # ms of silence required to end
    elif spoken_ms < 2000:
        return 650
    elif spoken_ms < 4000:
        return 550
    else:
        return 400   # long utterance -> end quickly
def audio_rms_int16(audio_bytes: bytes) -> float:
    if not audio_bytes:
        return 0.0
    x = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(x * x)))

def normalize_int16(audio_bytes: bytes, target_peak: float = 0.90) -> bytes:
    """
    Normalize to a target peak (0..1). Helps Whisper when speech is quiet.
    """
    x = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak < 1.0:
        return audio_bytes  # nothing to normalize
    scale = (target_peak * 32767.0) / peak
    y = np.clip(x * scale, -32768, 32767).astype(np.int16)
    return y.tobytes()
import re
try:
    from num2words import num2words as _n2w  # optional, nicer wording if installed
except Exception:
    _n2w = None

_DIGIT_WORD = {'0':'zero','1':'one','2':'two','3':'three','4':'four','5':'five',
               '6':'six','7':'seven','8':'eight','9':'nine'}



def open_input_stream(p, device_index):
    """Safely open an input stream, ensuring channelCount <= maxInputChannels."""
    info = p.get_device_info_by_index(device_index)
    max_ch = int(info.get("maxInputChannels", 0))

    if max_ch < 1:
        raise RuntimeError(
            f"Device {device_index} has no input channels (maxInputChannels={max_ch}, name={info.get('name')})"
        )

    # We only ever want mono input
    channels = min(1, max_ch)

    return p.open(
        format=pyaudio.paInt16,
        channels=channels,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=FRAME_SIZE,
        input_device_index=device_index,
    )
def find_mic_index(prefer_hw=None, keyword="wireless micro", retries=30, sleep_s=0.5):
    """
    Select mic by stable NAME match (keyword), not by hw:X,Y (which can swap).
    Optionally boost if prefer_hw matches, but never require it.
    """
    keyword = (keyword or "").lower()
    prefer_hw = (prefer_hw or "").lower() if prefer_hw else ""

    for attempt in range(1, retries + 1):
        p = pyaudio.PyAudio()
        try:
            best = None
            print("[Mic] Scan attempt {}/{}...".format(attempt, retries))

            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if info.get("maxInputChannels", 0) <= 0:
                    continue

                name = (info.get("name") or "")
                name_l = name.lower()

                # Require keyword match (prevents picking Jetson APE / pulse / default)
                if keyword and (keyword not in name_l):
                    continue

                score = 10  # keyword matched
                if prefer_hw and (prefer_hw in name_l):
                    score += 100

                # verify it can actually open
                try:
                    s = open_input_stream(p, i)
                    s.stop_stream()
                    s.close()
                    print("[Mic] Candidate OK: idx={} name='{}' score={}".format(i, name, score))

                    if best is None or score > best[0]:
                        best = (score, i)

                except Exception as e:
                    print("[Mic] Candidate but cannot open idx={} name='{}': {}".format(i, name, e))

            if best:
                return best[1]

        finally:
            p.terminate()

        time.sleep(sleep_s)

    return None

def _int_to_words(n: int) -> str:
    """Lightweight English int→words up to 999,999 (fallback if num2words missing)."""
    if _n2w:
        return _n2w(n, lang='en').replace('-', ' ')
    to19 = ["zero","one","two","three","four","five","six","seven","eight","nine",
            "ten","eleven","twelve","thirteen","fourteen","fifteen","sixteen",
            "seventeen","eighteen","nineteen"]
    tens = ["","","twenty","thirty","forty","fifty","sixty","seventy","eighty","ninety"]
    def words(num):
        if num < 20: return to19[num]
        if num < 100: return tens[num//10] + ("" if num%10==0 else " " + to19[num%10])
        if num < 1000:
            return to19[num//100] + " hundred" + ("" if num%100==0 else " " + words(num%100))
        if num < 1000000:
            return words(num//1000) + " thousand" + ("" if num%1000==0 else " " + words(num%1000))
        return str(num)  # beyond scope
    return words(n)
def peak_int16(audio_bytes: bytes) -> int:
    if not audio_bytes:
        return 0
    x = np.frombuffer(audio_bytes, dtype=np.int16)
    return int(np.max(np.abs(x))) if x.size else 0

def _ordinal_to_words(n: int) -> str:
    if _n2w:
        return _n2w(n, lang='en', to='ordinal').replace('-', ' ')
    # simple fallback for common ordinals
    irregular = {1:"first",2:"second",3:"third",5:"fifth",8:"eighth",9:"ninth",12:"twelfth"}
    if n in irregular: return irregular[n]
    base = _int_to_words(n)
    if base.endswith("y"):
        return base[:-1] + "ieth"
    if base.endswith("e"):
        return base + "th"
    return base + "th"

def expand_numbers(text: str) -> str:
    """Expand plain numbers, ordinals (1st), and decimals (3.14) to words."""
    # decimals first: 3.14 -> three point one four
    def _dec(m):
        whole = int(m.group(1))
        frac = m.group(2)
        return _int_to_words(whole) + " point " + " ".join(_DIGIT_WORD[d] for d in frac)
    text = re.sub(r'\b(\d+)\.(\d+)\b', _dec, text)

    # ordinals: 21st, 3rd, 4th
    def _ord(m):
        return _ordinal_to_words(int(m.group(1)))
    text = re.sub(r'\b(\d+)(st|nd|rd|th)\b', _ord, text, flags=re.IGNORECASE)

    # integers: 2025 -> two thousand twenty five
    def _int(m):
        return _int_to_words(int(m.group(0)))
    text = re.sub(r'\b\d+\b', _int, text)

    # tidy spaces
    return re.sub(r'\s+', ' ', text).strip()
os.environ["ALSA_DEBUG"] = "0"

# Configuration parameters
SAMPLE_RATE = 48000              # Sampling rate in Hz
# --- tweak these ---
FRAME_DURATION_MS = 20             # was 20ms → faster detection
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000)
VAD_AGGRESSIVENESS = 1         # 2   # was 3 → less likely to miss onsets
PRE_ROLL_MS = 900                   # was 600 for all of them be careful
#was 250                  # amount of audio to keep before first speech
PRE_ROLL_FRAMES = max(1, PRE_ROLL_MS // FRAME_DURATION_MS)
PADDING_DURATION_MS = 750 # was 1000 with tye
NUM_PADDING_FRAMES = int(PADDING_DURATION_MS / FRAME_DURATION_MS)
        # Aggressiveness mode for WebRTC VAD (0-3)
MIN_UTT_S = 0.05  # ignore segments shorter than ~350 ms
PUB_PORT = 5556                  # ZeroMQ publisher port for recognized text
CTRL_PORT = 5557                 # ZeroMQ control port for PAUSE/RESUME commands

MIN_RMS = 110.0   # start here; tune 100–250 depending on mic gain/noise
STARTUP_SUPPRESSION_S = 0.05  # suppress recognition for this long after RESUME
MIN_START_VOICED_FRAMES = 2  #was 3 with tye was 1 last2# need this many consecutive voiced frames to start


DEBUG_CONTINUOUS_LOCAL = False
# Global flags
paused_ctrl = True
paused_int = True
continuous_mode = False
#medium / large
model = None
_model_ready = threading.Event()
READY_FILE = "/tmp/whisper_ready"
# ------------------------ Helpers ------------------------
def load_whisper_async(model_name="medium.en"):
    global model
    device = "cuda" if torch.cuda.is_available() else "cpu"
    t0 = time.time()
    print(f"[Whisper] Loading {model_name} on {device}...", flush=True)
    model = whisper.load_model(model_name, device=device)
    print(f"[Whisper] Loaded in {time.time()-t0:.1f}s", flush=True)

    _model_ready.set()

    # ✅ READY signal for main PC (bash will wait for this)
    try:
        with open(READY_FILE, "w") as f:
            f.write("ready\n")
    except Exception as e:
        print("[Whisper] Could not write READY_FILE:", e, flush=True)

def utc_iso_filename():
    """Return filename-safe UTC ISO-8601 with milliseconds, 'Z' suffix, colons replaced."""
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S.%f")[:-3] + "Z"
def resample_audio(x, orig_sr, target_sr):
    if orig_sr == target_sr:
        return x

    if resample_poly is not None and Fraction is not None:
        frac = Fraction(target_sr, orig_sr).limit_denominator(1000)
        return resample_poly(x, frac.numerator, frac.denominator).astype(np.float32)

    # Fallback: linear interpolation
    n = int(round(len(x) * float(target_sr) / float(orig_sr)))
    if n <= 1:
        return x
    t_old = np.linspace(0.0, 1.0, num=len(x), endpoint=False)
    t_new = np.linspace(0.0, 1.0, num=n, endpoint=False)
    return np.interp(t_new, t_old, x).astype(np.float32)

def transcribe_with_whisper(audio_bytes, sample_rate=48000):
    # int16 bytes -> float32 [-1, 1]
    _model_ready.wait()
    x = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

    # Resample to 16k (Whisper expects 16k)
    x = resample_audio(x, sample_rate, 16000)

    result = model.transcribe(
        x,
        language="en",
        task="transcribe",
        temperature=0.0,
        fp16=True,
        condition_on_previous_text=False,
        no_speech_threshold=0.2,
        logprob_threshold=-1.0,
        compression_ratio_threshold=2.4,
        # speed knobs (optional)
        beam_size=1, # was 1 with stig
        best_of=1, # was 1 with stig
        without_timestamps=True,
    )

    segments = result.get("segments") or []
    text = (result.get("text") or "").strip()

    # confidence proxy from avg_logprob
    confs = [seg.get("avg_logprob", 0.0) for seg in segments]
    avg_logp = sum(confs) / len(confs) if confs else 0.0
    avg_conf = float(math.exp(avg_logp))

    text = expand_numbers(text)
    return text, avg_conf, segments
from collections import deque

def record_with_webrtcvad(stream, vad):
    print("Recording with WebRTC VAD...")

    pre_roll = deque(maxlen=PRE_ROLL_FRAMES)
    frames = []
    speech_started = False
    silence_frames = 0
    last_pause_log = 0.0

    t0 = time.time()
    voiced_streak = 0

    # --- WAIT FOR SPEECH START ---
    while not speech_started:
        if is_paused():
            # keep draining while paused so buffer doesn't grow
            stream.read(FRAME_SIZE, exception_on_overflow=False)
            pre_roll.clear()
            now = time.time()
            if now - last_pause_log > 1.0:
                print("Recording paused. Waiting for resume to start.")
                last_pause_log = now
            continue

        frame = stream.read(FRAME_SIZE, exception_on_overflow=False)

        if time.time() - t0 < STARTUP_SUPPRESSION_S:
            pre_roll.append(frame)
            continue

        pre_roll.append(frame)
        voiced = vad.is_speech(frame, SAMPLE_RATE)
        rms = audio_rms_int16(frame)

        if voiced and rms >= MIN_RMS:
            voiced_streak += 1
            if voiced_streak >= MIN_START_VOICED_FRAMES:
                speech_started = True
                frames.extend(list(pre_roll))
                frames.append(frame)
                print(f"Speech detected (gated): rms={rms:.1f}")
        else:
            voiced_streak = 0

    # --- RECORD UNTIL END ---
    deferred_pause = False
    spoken_frames = 0

    while True:
        frame = stream.read(FRAME_SIZE, exception_on_overflow=False)
        frames.append(frame)

        voiced = vad.is_speech(frame, SAMPLE_RATE)
        if voiced:
            silence_frames = 0
            spoken_frames += 1
        else:
            silence_frames += 1

        if is_paused():
            deferred_pause = True

        spoken_ms = spoken_frames * FRAME_DURATION_MS
        need_silence_ms = adaptive_padding_ms(spoken_ms)
        need_silence_frames = int(need_silence_ms / FRAME_DURATION_MS)

        GRACE_MS = 400
        grace_frames = int(GRACE_MS / FRAME_DURATION_MS)

        if (not voiced) and (silence_frames >= need_silence_frames):
            resumed = False
            for _ in range(grace_frames):
                f2 = stream.read(FRAME_SIZE, exception_on_overflow=False)
                frames.append(f2)
                if vad.is_speech(f2, SAMPLE_RATE) and audio_rms_int16(f2) >= MIN_RMS:
                    resumed = True
                    silence_frames = 0
                    spoken_frames += 1
                    break
                else:
                    silence_frames += 1
            if not resumed:
                break

        if deferred_pause:
            early_ms = max(150, int(need_silence_ms * 0.5))
            early_frames = int(early_ms / FRAME_DURATION_MS)
            if (not voiced) and (silence_frames >= early_frames):
                print("Stopping after pause request (adaptive early).")
                break

    t_end_record = time.time()
    audio_data = b"".join(frames)
    segment_duration = len(frames) * FRAME_DURATION_MS / 1000.0
    return audio_data, segment_duration, t_end_record


def record_fixed_duration(stream, duration_s):
    frames = []
    start = time.time()
    while time.time() - start < duration_s:
        if is_paused():
            # keep draining
            stream.read(FRAME_SIZE, exception_on_overflow=False)
            continue
        frames.append(stream.read(FRAME_SIZE, exception_on_overflow=False))

    t_end_record = time.time()
    audio_bytes = b"".join(frames)
    segment_duration = len(frames) * FRAME_DURATION_MS / 1000.0
    return audio_bytes, segment_duration, t_end_record



def is_paused():
    return paused_ctrl or paused_int


def control_listener():
    global paused_ctrl,paused_int, continuous_mode
    context = zmq.Context()
    control_sub = context.socket(zmq.SUB)
    MAIN_PC_IP = os.environ.get("MAIN_PC_IP", "127.0.0.1")
    control_sub.connect(f"tcp://{MAIN_PC_IP}:{CTRL_PORT}")
    control_sub.setsockopt(zmq.SUBSCRIBE, b"")
    print(f"Control listener connected to tcp://{MAIN_PC_IP}:{CTRL_PORT}")


    while True:
        try:
            msg = control_sub.recv_string().strip().upper()

            if msg == "PAUSE":
                paused_ctrl = True
                print("Received control command: PAUSE")

            elif msg == "RESUME":
                paused_ctrl = False
                paused_int = False  # also clear internal pause
                print("Received control command: RESUME")

            elif msg == "MODE:CONTINUOUS":
                continuous_mode = True
                print("Received control command: MODE:CONTINUOUS")
            elif msg == "MODE:FIXED":
                continuous_mode = False
                print("Received control command: MODE:FIXED")
        except Exception as e:
            print("Exception in control_listener:", e)
        time.sleep(0.01)



def main():
    session_start = float(os.environ.get("SESSION_START_EPOCH_S", time.time()))
# Write per-participant mic start file (no overwriting across participants)
    session_date = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    PARTICIPANT_ID = os.environ.get("PARTICIPANT_ID", "participant_unknown")
    mic_start_path = Path("recordings") / session_date / PARTICIPANT_ID / "mic_start.json"
    mic_start_path.parent.mkdir(parents=True, exist_ok=True)

    with open(mic_start_path, "w") as f:
        json.dump({"session_start_epoch_s": session_start}, f)
    print(f"[Meta] Using shared session_start_epoch_s = {session_start:.6f}")

    global continuous_mode, paused_int


    # ——— Generate a per‐run session ID ———
    session_id = uuid.uuid4().hex
    try:
        if os.path.exists(READY_FILE):
            os.remove(READY_FILE)
    except Exception:
        pass
    # -------------------------------------------------------
    # --- Participant-aware directory structure (NEW CODE) ---
    # -------------------------------------------------------

    # recordings/YYYY-MM-DD/participant_X/mic/
    BASE_DIR = Path("recordings") / session_date / PARTICIPANT_ID / "mic"
    BASE_DIR.mkdir(parents=True, exist_ok=True)

    # One folder per execution
    session_dir = BASE_DIR / f"sess-{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = session_dir / "manifest.jsonl"
    utt_index = 0

    with open(Path("recordings") / "latest_session.txt", "w") as f:
        f.write(str(session_dir))

    # -------------------------------------------------------

    # Set up ZeroMQ publisher for recognized text.
    context = zmq.Context()
    publisher = context.socket(zmq.PUB)
    publisher.bind(f"tcp://*:{PUB_PORT}")
    print(f"ZeroMQ Publisher bound to tcp://*:{PUB_PORT}", flush=True)  # <-- add flush

    # Start the control listener thread.
    ctrl_thread = threading.Thread(target=control_listener, daemon=True)
    ctrl_thread.start()

    #threading.Thread(target=load_whisper_async, args=("medium.en",), daemon=True).start()
    threading.Thread(target=load_whisper_async, args=("small.en",), daemon=True).start()




    # List available input devices.
    p = pyaudio.PyAudio()
    print("Available input devices:")
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info.get("maxInputChannels", 0) > 0:
            print(f"  Device {i}: {info.get('name')}")
    p.terminate()

    # Set the external microphone device index (update as necessary)
# --- Find Microphone robustly (retries + hw preference) ---
    external_device_index = find_mic_index(
    prefer_hw=None,
    keyword="wireless micro",
    retries=30,
    sleep_s=0.5
    )
    if external_device_index is None:
        print("[FATAL] Could not find a usable mic (Wireless MICRO / hw:2,0).")
        import sys
        sys.exit(1)

    print(f"[Mic] Using device index: {external_device_index}")
    # ---------------------------------------------------------
    # --- OPEN STREAM ONCE (persistent) ---
    p_audio = pyaudio.PyAudio()
    stream = open_input_stream(p_audio, external_device_index)
    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
    print("[Mic] Stream opened and kept alive.")
    
    while True:
        if is_paused():
            stream.read(FRAME_SIZE, exception_on_overflow=False)  # keep buffer fresh
            time.sleep(FRAME_DURATION_MS / 1000.0)
            continue
        if not _model_ready.is_set():
            stream.read(FRAME_SIZE, exception_on_overflow=False)
            time.sleep(FRAME_DURATION_MS / 1000.0)
            continue

        try:
            # 1) Record audio + get duration
            recording_mode = "vad"
            if continuous_mode:
                recording_mode = "fixed"
                audio_bytes, segment_duration, ts_end = record_fixed_duration(stream, duration_s=60)
                continuous_mode = False
            else:
                audio_bytes, segment_duration, ts_end = record_with_webrtcvad(stream, vad)
            DEBUG_AUDIO = False   # True only when tuning
            ts_start = ts_end - segment_duration

            # after recording
            if segment_duration < MIN_UTT_S:
                print(f"[DROP] too short: {segment_duration:.2f}s (min={MIN_UTT_S:.2f}s)")
                if DEBUG_AUDIO:
                    print(f"[DEBUG] too short: {segment_duration:.2f}s")
                continue

            rms  = audio_rms_int16(audio_bytes)
            peak = peak_int16(audio_bytes)
            print(f"[AUDIO] dur={segment_duration:.2f}s rms={rms:.1f} peak={peak}")
            if DEBUG_AUDIO:
                print(f"[AUDIO] dur={segment_duration:.2f}s rms={rms:.1f} peak={peak}")

            if recording_mode != "fixed" and rms < MIN_RMS:
                print(f"[DROP] low-energy rms={rms:.1f} < {MIN_RMS:.1f}")
                if DEBUG_AUDIO:
                    print(f"[DROP] low-energy rms={rms:.1f} < {MIN_RMS:.1f}")
                continue

            audio_bytes = normalize_int16(audio_bytes, target_peak=0.90)
            # 2) Transcribe & get confidence
            try:
                text, asr_conf,segments = transcribe_with_whisper(audio_bytes)
            except Exception as e:
                print("[Mic] Recording failed. Re-detecting mic...", e)

                # close old stream safely
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
                try:
                    p_audio.terminate()
                except Exception:
                    pass

                new_idx = find_mic_index(prefer_hw=None, keyword="wireless micro", retries=10, sleep_s=0.5)
                if new_idx is not None:
                    external_device_index = new_idx
                    print(f"[Mic] Switched to device index: {external_device_index}")

                    # reopen persistent stream
                    p_audio = pyaudio.PyAudio()
                    stream = open_input_stream(p_audio, external_device_index)
                    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
                    print("[Mic] Stream reopened.")

                time.sleep(0.5)
                continue

            raw_text = (text or "").strip()
            raw = raw_text.lower().strip()

            bad_asr = (
                raw == "" or
                raw in ("dot","period","comma","question mark","exclamation mark",
                        ".","?","!",",",";","thank you.","you")
                # optional: also gate by confidence
                # or asr_conf < 0.35
            )



            # 3) Build filename and write WAV
            iso = utc_iso_filename()
            dur_ms = int(segment_duration * 1000)
            conf_pct = max(0, min(100, int(round(asr_conf * 100))))
            fname = f"{iso}_sess-{session_id}_utt-{utt_index:05d}_dur-{dur_ms}ms_conf-{conf_pct}.wav"
            wav_path = str(session_dir / fname)

            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(audio_bytes)

            print("Recognized:", text, f"(conf={asr_conf:.2f}) →", fname)

            # 4) Publish JSON so controller can parse text + confidence + duration

            payload = {
                "session_id": session_id,
                "iso_utc": iso,
                "utterance_index": utt_index,
                "wav_path": wav_path,
                "asr_confidence": asr_conf,
                "whisper_segments": segments,
                "segment_duration": segment_duration,
                "segment_start_epoch_s": ts_start,
                "segment_end_epoch_s": ts_end,
                "recording_mode": recording_mode,

                # always keep what Whisper heard for debugging / later analysis
                "asr_text_raw": raw_text,
                "debug": {"raw": raw, "rms": rms, "peak": peak},
            }
            if bad_asr:
                payload["event"] = "NO_HEAR"
                payload["text"] = ""          # controller will say: "I didn’t hear that"
            else:
                payload["event"] = "ASR_OK"
                payload["text"] = raw_text    # normal behavior (or expand_numbers(raw_text))
            publisher.send_string(json.dumps(payload))
            if DEBUG_CONTINUOUS_LOCAL:
                # don't lock up waiting for controller RESUME
                paused_int = False
            else:
                # normal pipeline behavior
                paused_int = True
            print("Published utterance.")
            # 5) Append to per-session manifest
            try:
                with open(manifest_path, "a") as mf:
                    mf.write(json.dumps(payload) + "\n")
            except Exception as e:
                print("Could not write manifest:", e)

            utt_index += 1

        except Exception as e:
            print("[Mic] Recording failed. Re-detecting mic...", e)
            new_idx = find_mic_index(prefer_hw=None, keyword="wireless micro", retries=10, sleep_s=0.5)

            if new_idx is not None:
                external_device_index = new_idx
                print(f"[Mic] Switched to device index: {external_device_index}")
            time.sleep(0.5)
            print("Exception in main loop:", e)
            continue
        time.sleep(0.1)


if __name__ == "__main__":
    main()
