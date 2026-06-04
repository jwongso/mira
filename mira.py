#!/usr/bin/env python3
"""Mira - local voice assistant. Phase 1 orchestrator."""
import argparse
import asyncio
import json
import sys
import time
from enum import Enum, auto
from pathlib import Path

import re
import config as cfg_module
import spotify_tool
import tools as _tools
from memory import Memory
from intent import classify_hard, execute_hard

_DUP_WORD_RE = re.compile(r'\b(\w+)\s+\1\b', re.IGNORECASE)


def _dedup(text: str) -> str:
    """Remove consecutive duplicate words caused by chunk overlap."""
    prev = None
    while prev != text:
        prev = text
        text = _DUP_WORD_RE.sub(r'\1', text)
    return text
from llm import LLMClient
from observe import Interaction, SessionLogger
from tts import speak

FOLLOW_UP_S = 12.0  # seconds follow-up window stays open after any response


class State(Enum):
    IDLE      = auto()  # mira-poc in SCANNING, waiting for wake word
    FOLLOW_UP = auto()  # follow-up window open; mira-poc in COMMAND mode


async def _pause_asr(proc):
    proc.stdin.write(b"pause\n")
    await proc.stdin.drain()


async def _resume_asr(proc):
    proc.stdin.write(b"resume\n")
    await proc.stdin.drain()


async def _followup_asr(proc):
    """Tell mira-poc to skip the next wake word and enter COMMAND mode."""
    proc.stdin.write(b"followup\n")
    await proc.stdin.drain()


async def handle_end(ix: Interaction, llm: LLMClient, logger: SessionLogger,
                     proc, tts_model, debug: bool) -> tuple[str, bool]:
    """Route intent, speak response. Returns (response_text, close_session)."""
    t0 = time.monotonic()

    # Tier 1: hard command rules (no LLM)
    intent = classify_hard(ix.text)
    if intent:
        if intent.action == "close":
            response = "Okay, I'm listening for your wake word."
        else:
            response = execute_hard(intent)
        print(f"[rule]  {intent.action}  -> {response}")
        print(f"[mira]  {response}")
        await _pause_asr(proc)
        # Don't pause-for-speech on pause/stop commands - already stopped
        was_playing = False if intent.action in ("pause",) \
                      else spotify_tool.pause_for_speech()
        speak(response, tts_model)
        if was_playing and not intent.close_session:
            spotify_tool.resume_after_speech()
        await _resume_asr(proc)
        logger.write(ix, response,
                     intent_type=intent.type,
                     intent_action=intent.action,
                     classifier_source="rule",
                     latency_ms=round((time.monotonic() - t0) * 1000))
        return response, intent.close_session

    # Tier 2: LLM general
    print("[llm]  thinking...")
    try:
        response, llm_s = await llm.chat(ix.text)
    except Exception as e:
        print(f"[llm]  error: {e}", file=sys.stderr)
        response = "Sorry, I had trouble reaching the language model."
        llm_s = 0.0

    total_ms = round((time.monotonic() - t0) * 1000)
    print(f"[llm]  {llm_s:.1f}s")
    print(f"[mira]  {response}")
    await _pause_asr(proc)
    was_playing = spotify_tool.pause_for_speech()
    speak(response, tts_model)
    if was_playing:
        spotify_tool.resume_after_speech()
    await _resume_asr(proc)
    logger.write(ix, response,
                 intent_type="general",
                 classifier_source="llm",
                 latency_ms={"llm": round(llm_s * 1000), "total": total_ms})
    return response, False


async def run(cfg, debug: bool) -> None:
    logger = SessionLogger()
    llm    = LLMClient(cfg.llm_url, cfg.llm_model)
    if cfg.spotify_id:
        spotify_tool.init(cfg.spotify_id, cfg.spotify_secret, cfg.spotify_redirect)
        print("[mira]  Spotify configured")
    mem = Memory()
    if mem.ready:
        _tools.init_memory(mem)
        print("[mira]  Memory ready (Qdrant)")
    print("[mira]  loading TTS model...")
    speak("", cfg.tts_model)  # pre-load ONNX

    asr_cmd = [str(cfg.asr_bin), str(cfg.asr_model), "--json"]
    if debug:
        print(f"[mira]  ASR cmd: {' '.join(asr_cmd)}")

    stderr = None if debug else asyncio.subprocess.DEVNULL
    proc = await asyncio.create_subprocess_exec(
        *asr_cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=stderr,
    )
    print(f"[mira]  started ASR (PID {proc.pid})")

    state          = State.IDLE
    follow_up_until = 0.0

    def _open_follow_up():
        nonlocal state, follow_up_until
        state = State.FOLLOW_UP
        follow_up_until = time.time() + FOLLOW_UP_S

    def _remaining() -> int:
        return max(0, int(follow_up_until - time.time()))

    try:
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                if debug:
                    print(f"[warn]  bad JSON: {line}", file=sys.stderr)
                continue

            t = ev.get("type")

            if t == "ready":
                print(f"[mira]  ASR ready  model={ev.get('model')}  "
                      f"chunk={ev.get('chunk_ms')}ms")
                print("[mira]  listening for wake word...\n")

            elif t == "wake":
                print(f"\n[wake]  {ev.get('text')}")
                # User used the wake word - cancel any open follow-up.
                state = State.IDLE
                follow_up_until = 0.0

            elif t == "command":
                print(f"[cmd {ev.get('n','?')}]  {ev.get('text')}")

            elif t == "end":
                text = ev.get("text", "").strip()
                if not text:
                    # Silence timeout in COMMAND - skip, "scanning" event follows
                    continue
                ev["text"] = _dedup(text)
                ix = Interaction(ev)
                label = "[follow]" if state == State.FOLLOW_UP else "[end]  "
                print(f"{label}  {ix.text}")
                _open_follow_up()  # extend window before blocking on LLM+TTS
                _, close = await handle_end(ix, llm, logger, proc, cfg.tts_model, debug)
                if close:
                    state = State.IDLE
                    follow_up_until = 0.0
                else:
                    _open_follow_up()  # extend again after response delivered

            elif t == "scanning":
                # mira-poc just returned to SCANNING state.
                # If follow-up window is still open, switch it back to COMMAND.
                if state == State.FOLLOW_UP and time.time() < follow_up_until:
                    rem = _remaining()
                    print(f"[mira]  follow-up open ({rem}s)  -  "
                          f"or say Hey Mira for new command\n")
                    await _followup_asr(proc)
                else:
                    state = State.IDLE
                    print("[mira]  listening for wake word...\n")

            elif t == "scan":
                pass  # VAD fragments during SCANNING; not used in follow-up mode

    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                proc.kill()
                await proc.wait()
        print("\n[mira]  stopped.")


def main():
    parser = argparse.ArgumentParser(description="Mira voice assistant")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--debug",  action="store_true",
                        help="Show ASR debug output and verbose logging")
    args = parser.parse_args()

    cfg_path = args.config or (Path(__file__).parent / "mira.toml")
    if not cfg_path.exists():
        print(f"ERROR: config not found: {cfg_path}")
        print("Copy mira.toml.example to mira.toml and edit it.")
        sys.exit(1)

    cfg = cfg_module.load(cfg_path)

    for p, label in [(cfg.asr_bin, "asr.bin"), (cfg.asr_model, "asr.model"),
                     (cfg.tts_model, "tts.model")]:
        if not p.exists():
            print(f"ERROR: {label} not found: {p}")
            sys.exit(1)

    asyncio.run(run(cfg, debug=args.debug))


if __name__ == "__main__":
    main()
