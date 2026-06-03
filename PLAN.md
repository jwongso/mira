# Mira - Local Voice Assistant

Personal voice assistant running entirely on local hardware.
Wake word: **"Hey Mira"**

---

## What Mira is

A privacy-first voice assistant that runs on your own machine with no cloud dependency.
Speech recognition via whisper.cpp (wstream), reasoning via a local LLM (Qwen3-8B),
and text-to-speech via Piper. External services (Spotify, etc.) are integrated via their
own APIs - the LLM decides which action to take from the voice command.

---

## Architecture

```
Microphone
    |
    v
wstream (C++, SDL2)
  whisper.cpp small.en model
  WebSocket server :9001
    |
    v  {"type": "transcribe", "content": "..."}
    |
    v
Mira Orchestrator (Python)
  - wake word detection ("hey mira") on transcribed text
  - state machine: idle -> listening -> processing -> speaking
  - intent classifier: music / general / command
    |
    +---> Spotify Handler (spotipy)
    |       search, play, pause, skip, volume, queue, playlists
    |
    +---> LLM (Qwen3-8B via llama.cpp :8080)
    |       general Q&A, chat, reasoning
    |
    +---> Tool Handlers (future)
            MCP servers, home automation, calendar, etc.
    |
    v
Piper TTS
  synthesize response to raw audio
    |
    v
Speaker (aplay)
```

---

## Rolling transcription behaviour

wstream uses a fixed 3-second step with a 10-second sliding window (no VAD).
Every 3 seconds it broadcasts the last 10 seconds re-transcribed. This means:
- Consecutive messages heavily overlap - do not concatenate them
- After wake word detected, wait ~4 seconds for the window to stabilise
- Take the LAST received message as the complete utterance, strip the wake word
- No end-of-utterance signal exists - timing-based collection only

---

## Tech stack

| Component | Technology | Default | User-configurable |
|---|---|---|---|
| ASR | wstream (whisper.cpp) via WebSocket | `ggml-small.en-q5_1.bin` | Any whisper.cpp GGUF model |
| LLM | Ollama or any OpenAI-compatible server | `qwen3:0.6b` via Ollama | URL + model name in config |
| TTS | Piper | `en_US-lessac-medium.onnx` | Any Piper voice model |
| Spotify | spotipy (Spotify Web API, OAuth2) | - | Client ID/secret in config |
| Orchestrator | Python 3.11+, asyncio, websockets, httpx | - | - |
| Future tools | MCP protocol (astraea, home automation, etc.) | - | - |

---

## Project layout

```
mira/
  mira.py              - main orchestrator entry point
  config.py            - paths, ports, model names, wake word
  wake.py              - wake word detection and utterance collection
  tts.py               - Piper TTS wrapper (speak, play_ding)
  llm.py               - Qwen3 chat wrapper, conversation history
  intent.py            - LLM-based intent classifier (music / general / tool)
  plugins/
    spotify.py         - Spotify handler (spotipy wrapper + action executor)
    __init__.py
  tools/
    __init__.py        - future MCP tool adapters
  models/              - Piper voice model files (.onnx + .json)
  sounds/
    ding.wav           - wake acknowledgement sound
  PLAN.md
  README.md
  requirements.txt
  .env.example         - SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, WSTREAM_WS, etc.
```

---

## Phases

### Phase 1 - Core loop (MVP)

Goal: "Hey Mira, what time is it?" works end to end.

- [ ] Project scaffold (config, entry point, requirements)
- [ ] wstream WebSocket client + wake word detection (`wake.py`)
- [ ] Piper TTS wrapper (`tts.py`)
- [ ] LLM wrapper with conversation history (`llm.py`)
- [ ] Main state machine (`mira.py`)
- [ ] Basic general Q&A working via voice

### Phase 2 - Spotify integration

Goal: "Hey Mira, play some jazz" and "Hey Mira, skip this" work.

- [ ] Spotify OAuth setup + token cache (`plugins/spotify.py`)
- [ ] Intent classifier - distinguishes music commands from general Q&A (`intent.py`)
- [ ] LLM-based action extraction: maps voice command to structured Spotify action
- [ ] Action executor: search_and_play, pause, resume, skip, previous, volume, what_is_playing
- [ ] Playlist support: "play my workout playlist"
- [ ] Spoken feedback: "Playing Radiohead" / "Paused" / "Volume set to 60"

### Phase 3 - Tool expansion via MCP

Goal: "Hey Mira, ask the tenancy assistant about bond refunds" works.

- [ ] MCP client adapter in `tools/`
- [ ] Connect to astraea MCP servers (nz-tenancy, nz-building)
- [ ] Intent routing: music / general / legal / tool
- [ ] Plugin registry so new tools can be added without touching orchestrator

### Phase 4 - Polish and reliability

- [ ] Graceful handling of LLM timeout, Spotify auth expiry, wstream disconnect
- [ ] Auto-reconnect to wstream WebSocket
- [ ] "Hey Mira, stop" interrupt - cancel in-progress TTS
- [ ] Config file (TOML) instead of env vars
- [ ] Logging to ~/.mira/mira.log
- [ ] systemd user service for always-on operation

### Phase 5 - Mac Mini M4 Pro (August 2026)

- [ ] Re-benchmark with M4 Pro GPU offload - may allow larger LLM (14B/30B)
- [ ] Evaluate faster TTS models (kokoro, StyleTTS2)
- [ ] Consider enabling wstream VAD for cleaner utterance detection
- [ ] Homebridge / Home Assistant integration (lights, switches)

---

## Key design decisions

**Why LLM for intent classification instead of regex?**
Voice commands are unpredictable. "Throw on some Radiohead", "play that song from
Guardians of the Galaxy", and "I want to listen to something chill" all mean the same
thing. The LLM handles natural variation; regex would need hundreds of patterns.

**Why text-based wake word instead of audio-level (openWakeWord)?**
wstream is already transcribing everything. Text matching requires zero extra binary,
zero extra model. False positives are rare for "hey mira". Can always add audio-level
detection later if needed.

**Why Piper for TTS?**
Fastest local TTS with acceptable voice quality. Latency ~300ms for a short sentence.
Runs on CPU, no GPU required, actively maintained.

**Why keep conversation history in memory?**
Simple and sufficient for a voice assistant. History bounded to last 6 turns
(~12 messages) to avoid context overflow. Not persisted across restarts intentionally -
voice sessions are transient.

---

## Models

### Whisper (ASR)

wstream takes the model path as its first CLI argument. Default bundled model:

```
ggml-small.en-q5_1.bin   (~57MB) - recommended, already wstream's default
```

| Model file | Size | Notes |
|---|---|---|
| `ggml-tiny.en-q5_1.bin` | ~15MB | Fastest, use on very low-end hardware |
| `ggml-base.en-q5_1.bin` | ~42MB | Good balance of speed and accuracy |
| `ggml-small.en-q5_1.bin` | ~57MB | **Default** - reliable wake word detection |
| `ggml-medium.en-q5_1.bin` | ~150MB | Better accuracy, noticeably slower |

Download:

```bash
# via whisper.cpp script
bash /path/to/whisper.cpp/models/download-ggml-model.sh small.en

# or direct from HuggingFace
wget https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en-q5_1.bin \
  -O ~/.mira/models/whisper/ggml-small.en-q5_1.bin
```

English-only models (`.en`) are faster and more accurate for English than the multilingual equivalents.
Set `whisper_model` in config to switch.

### LLM

Default: Ollama with `qwen3:0.6b` (~400MB). Install:

```bash
curl -fsSL https://ollama.ai/install.sh | sh
ollama pull qwen3:0.6b
```

Set `llm_url` and `llm_model` in config to use a different Ollama model or point
to an existing llama.cpp server (any OpenAI-compatible endpoint works).

### Piper TTS voice

```bash
# download voice model pair (.onnx + .onnx.json)
wget https://github.com/rhasspy/piper/releases/download/2023.11.14-2/en_US-lessac-medium.onnx
wget https://github.com/rhasspy/piper/releases/download/2023.11.14-2/en_US-lessac-medium.onnx.json
```

---

## Configuration (mira.toml)

```toml
[wstream]
ws_url        = "ws://localhost:9001"      # wstream WebSocket address
whisper_model = "~/.mira/models/whisper/ggml-small.en-q5_1.bin"

[llm]
url   = "http://localhost:11434/v1/chat/completions"  # Ollama default
model = "qwen3:0.6b"
# To use llama.cpp instead:
# url   = "http://localhost:8080/v1/chat/completions"
# model = "qwen3"

[tts]
piper_bin   = "piper"
voice_model = "~/.mira/models/piper/en_US-lessac-medium.onnx"

[wake]
word    = "hey mira"
timeout = 4.0   # seconds to wait for utterance to stabilise after wake word

[spotify]
client_id     = ""
client_secret = ""
redirect_uri  = "http://localhost:8888/callback"
token_cache   = "~/.mira/spotify_token"
```

---

## Running

```bash
# Terminal 1 - start wstream on port 9001 with default model
WSTREAM_PORT=9001 ./stream ~/.mira/models/whisper/ggml-small.en-q5_1.bin

# Terminal 2 - start Mira
python mira.py

# Or with a custom config
python mira.py --config /path/to/mira.toml
```

Later: both managed by systemd user services.

## wstream changes needed

**None for Phase 1.** wstream already supports:
- Custom model path via first CLI argument
- Custom port via `WSTREAM_PORT` env var
- Non-browser clients (empty Origin header is whitelisted)

Possible future additions (not required now):
- `{"type": "silence"}` event after N seconds of no speech - cleaner than timing hack
- `--lang` CLI arg for multilingual support (currently hardcoded English)
- `--vad` flag to enable VAD mode without recompiling
