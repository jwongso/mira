#!/usr/bin/env python3
"""
Mira ASR PoC - wake word detection and audio quality analysis

Records audio continuously into a ring buffer while receiving transcription
windows from wstream over WebSocket. On each wake word detection, saves:
  - A WAV file: PRE_WAKE_S seconds before + POST_WAKE_S seconds after
  - A JSONL record linking the wav, all transcription windows, and timestamps

This lets you compare "what was actually said" vs "what whisper heard" for
each model, and build a ground truth dataset for tuning.

Usage:
    python poc.py                          # start wstream + connect
    python poc.py --model ggml-base.en-q5_1.bin   # use a different model
    python poc.py --list-devices           # show audio input devices
    python poc.py --device 1               # use device index 1

wstream is started automatically as a subprocess and killed on exit.
"""

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone

import numpy as np
import sounddevice as sd
import soundfile as sf

try:
    import websockets
except ImportError:
    print("ERROR: websockets not installed. Run: pip install websockets")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_FRAMES = 1600          # 100ms per callback at 16kHz
RING_SECONDS = 30            # ring buffer depth
WAKE_WORD = "hey mira"
PRE_WAKE_S = 5.0             # audio to save before wake word detection
POST_WAKE_S = 8.0            # audio to save after wake word (covers command)
OUTPUT_DIR = "poc_recordings"

# ---------------------------------------------------------------------------
# Audio ring buffer
# ---------------------------------------------------------------------------
# Thread-safe ring buffer of (wall_time_of_first_sample, float32_array) chunks.
# Written by sounddevice callback thread; read by asyncio thread.

_ring_lock = threading.Lock()
_ring: deque = deque()
_ring_samples = 0
_ring_max = RING_SECONDS * SAMPLE_RATE


def _audio_callback(indata, frames, time_info, status):
    global _ring_samples
    if status:
        print(f"[audio] {status}", file=sys.stderr)
    t = time.time()
    chunk = indata[:, 0].copy()  # mono float32
    with _ring_lock:
        _ring.append((t, chunk))
        _ring_samples += len(chunk)
        while _ring_samples > _ring_max:
            _, evicted = _ring.popleft()
            _ring_samples -= len(evicted)


def _get_audio_window(start_t: float, end_t: float) -> np.ndarray:
    """Return float32 samples from [start_t, end_t] (wall time)."""
    with _ring_lock:
        snapshot = list(_ring)
    parts = []
    for chunk_t, chunk in snapshot:
        chunk_end = chunk_t + len(chunk) / SAMPLE_RATE
        if chunk_end < start_t:
            continue
        if chunk_t > end_t:
            break
        si = max(0, int((start_t - chunk_t) * SAMPLE_RATE))
        ei = min(len(chunk), int((end_t - chunk_t) * SAMPLE_RATE) + 1)
        parts.append(chunk[si:ei])
    return np.concatenate(parts) if parts else np.array([], dtype=np.float32)


def _save_wav(samples: np.ndarray, path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    sf.write(path, samples, SAMPLE_RATE, subtype="PCM_16")


# ---------------------------------------------------------------------------
# Save completed interaction
# ---------------------------------------------------------------------------

def _save_interaction(interaction: dict, log_path: str):
    wake_t = interaction["wake_time"]
    samples = _get_audio_window(wake_t - PRE_WAKE_S, wake_t + POST_WAKE_S)

    stamp = datetime.fromtimestamp(wake_t).strftime("%Y%m%d_%H%M%S_%f")[:-3]
    wav_name = f"wake_{stamp}_{interaction['id']}.wav"
    wav_path = os.path.join(OUTPUT_DIR, wav_name)
    _save_wav(samples, wav_path)

    record = {
        "id": interaction["id"],
        "ts": datetime.fromtimestamp(wake_t, tz=timezone.utc).isoformat(),
        "wake_time": wake_t,
        "windows": interaction["windows"],
        "wav_file": wav_name,
        "wav_duration_s": round(len(samples) / SAMPLE_RATE, 2),
        "pre_wake_s": PRE_WAKE_S,
        "post_wake_s": POST_WAKE_S,
    }
    with open(log_path, "a") as f:
        f.write(json.dumps(record) + "\n")

    dur = record["wav_duration_s"]
    n = len(interaction["windows"])
    print(f"\n  \033[94m[saved]\033[0m {wav_name}  ({dur}s audio, {n} windows)\n")


# ---------------------------------------------------------------------------
# wstream subprocess
# ---------------------------------------------------------------------------

WSTREAM_BIN     = os.path.expanduser("~/proj/priv/wstream/build-cpu/bin/wstream")
WSTREAM_MODELS  = os.path.expanduser("~/proj/priv/wstream/models")
DEFAULT_MODEL   = "ggml-small.en-q5_1.bin"
CONNECT_RETRIES = 15
CONNECT_DELAY   = 1.0   # seconds between retries while wstream loads


def start_wstream(model_path: str, port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["WSTREAM_PORT"] = str(port)
    proc = subprocess.Popen(
        [WSTREAM_BIN, model_path],
        env=env,
        stdout=sys.stderr,
        stderr=sys.stderr,
    )
    return proc


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(args):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    log_path = os.path.join(
        OUTPUT_DIR,
        f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    )

    # Resolve model path
    model_path = args.model
    if not os.path.isabs(model_path):
        model_path = os.path.join(WSTREAM_MODELS, model_path)
    model_path = os.path.expanduser(model_path)
    if not os.path.exists(model_path):
        print(f"ERROR: model not found: {model_path}")
        print(f"Available models in {WSTREAM_MODELS}:")
        for f in sorted(os.listdir(WSTREAM_MODELS)):
            if f.endswith(".bin"):
                print(f"  {f}")
        return

    # Start audio capture
    device_idx = args.device if args.device is not None else 0
    dev_info = sd.query_devices(device_idx)
    if dev_info["max_input_channels"] < 1:
        print(f"ERROR: device {device_idx} ({dev_info['name']}) has no input channels.")
        print("Run with --list-devices to see available input devices.")
        return
    n_channels = max(1, min(int(dev_info["max_input_channels"]), 2))

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=n_channels,
        dtype="float32",
        blocksize=CHUNK_FRAMES,
        device=device_idx,
        callback=_audio_callback,
    )
    stream.start()

    # Start wstream subprocess - its init output floods the terminal,
    # so we print our summary after it connects (see below)
    port = int(args.url.rsplit(":", 1)[-1].rstrip("/"))
    wstream_proc = start_wstream(model_path, port)

    pending: list[dict] = []

    try:
        # Retry loop - wstream needs a moment to load the model
        ws = None
        for attempt in range(CONNECT_RETRIES):
            await asyncio.sleep(CONNECT_DELAY)
            if wstream_proc.poll() is not None:
                print(f"\n[error] wstream exited unexpectedly (code {wstream_proc.returncode})")
                return
            try:
                ws = await websockets.connect(args.url)
                break
            except OSError:
                print(f"[ws]    Waiting for wstream... ({attempt + 1}/{CONNECT_RETRIES})")
        else:
            print(f"[error] Could not connect to wstream after {CONNECT_RETRIES} attempts.")
            return

        async with ws:
            print(f"\n{'='*60}")
            print(f"  audio   : device {device_idx}")
            print(f"  model   : {os.path.basename(model_path)}")
            print(f"  wake    : \"{WAKE_WORD}\"")
            print(f"  saving  : {PRE_WAKE_S}s pre + {POST_WAKE_S}s post -> {OUTPUT_DIR}/")
            print(f"{'='*60}\n")
            print("Listening... Ctrl+C to stop\n")

            async def receive_loop():
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") != "transcribe":
                        continue

                    ts = time.time()
                    text = msg.get("content", "").strip()
                    is_wake = WAKE_WORD in text.lower()

                    dt = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    if is_wake:
                        print(f"  \033[92m*** WAKE *** [{dt}]\033[0m  {text}")
                    else:
                        print(f"              [{dt}]  {text}")

                    window = {"ts": ts, "text": text}

                    if is_wake:
                        interaction = {
                            "id": uuid.uuid4().hex[:8],
                            "wake_time": ts,
                            "save_at": ts + POST_WAKE_S,
                            "windows": [window],
                        }
                        pending.append(interaction)
                    else:
                        for p in pending:
                            if ts < p["save_at"]:
                                p["windows"].append(window)

            async def save_loop():
                while True:
                    now = time.time()
                    due = [p for p in pending if p["save_at"] <= now]
                    for p in due:
                        pending.remove(p)
                        _save_interaction(p, log_path)
                    await asyncio.sleep(0.25)

            await asyncio.gather(receive_loop(), save_loop())

    finally:
        stream.stop()
        stream.close()
        if wstream_proc.poll() is None:
            print("\n[wstream] Stopping...")
            wstream_proc.terminate()
            try:
                wstream_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                wstream_proc.kill()


def list_devices():
    print("Available audio input devices:\n")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            default = " <- default" if i == sd.default.device[0] else ""
            print(f"  [{i:2d}] {d['name']}{default}")
            print(f"        in={d['max_input_channels']}ch  out={d['max_output_channels']}ch  rate={int(d['default_samplerate'])}Hz")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Mira ASR PoC - wake word + audio capture",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--url", default="ws://localhost:9001",
                        help="wstream WebSocket URL (default: ws://localhost:9001)")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Whisper model filename or full path (default: {DEFAULT_MODEL})")
    parser.add_argument("--device", type=int, default=None,
                        help="Audio input device index (default: system default)")
    parser.add_argument("--list-devices", action="store_true",
                        help="List available audio input devices and exit")
    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        sys.exit(0)

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n[done]")
