# Mira - Local Voice Assistant

Personal voice assistant running entirely on local hardware.
Wake word: **"Hey Mira"**

---

## What Mira is

A privacy-first voice assistant that runs on your own machine with no cloud dependency.
Speech recognition via whisper.cpp (wstream), reasoning via a local LLM, and
text-to-speech via Piper. External services (Spotify, etc.) are integrated via their
own APIs - the LLM decides which action to take from the voice command.

---

## What differentiates Mira

Most local voice assistant projects on GitHub are 200-line glue scripts: whisper
transcribes, one LLM replies, piper speaks. Mira is built differently in three ways.

### 1. MCP as the native skill system

Every other voice assistant has a custom plugin/skill format. Mira's skills ARE MCP
servers. The entire MCP ecosystem - GitHub, Slack, Google Drive, home automation,
and domain-specialist servers like astraea - becomes a Mira skill automatically.
Install a new MCP server, Mira gains the capability. No code changes, no skill
manifest files, no custom API to learn.

### 2. Expert routing instead of one LLM for everything

Mira routes each question to the right expert rather than asking one general model
about everything:

```
"what are my rights if my landlord won't fix the heating?"
  -> astraea nz-tenancy MCP (31,000 Tribunal decisions + live legislation)

"play something chill"
  -> Spotify handler

"what time is it in Berlin?"
  -> general LLM
```

Specialized sources give qualitatively better answers than a general LLM guessing.
No other local voice assistant does domain-specialist routing.

### 3. Network-transparent ASR

wstream runs as a WebSocket server. Mira can run on a dedicated machine (server,
Raspberry Pi) while you speak from a phone browser, another computer, or any device
on the local network. The microphone and the brain are decoupled. Most projects
hardwire them to the same machine.

### 4. Built-in observability and benchmarking

Every interaction is logged with full telemetry - latency per component, raw
transcription windows, intent classification result, plugin outcome. Replay any
session with different settings. Benchmark whisper models, LLMs, and TTS voices
against each other. No other local voice assistant ships this out of the box.
(See Observability section below.)

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
  - utterance collection (stabilizer + timeout)
  - state machine: idle -> listening -> processing -> speaking
    |
    v
Intent Router (layered - see below)
    |
    +---> Spotify Handler (spotipy)
    |       search, play, pause, skip, volume, queue, playlists
    |
    +---> LLM (Qwen3 via Ollama :11434)
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

### Intent router (layered)

For command-style interactions, the LLM is not always needed. The router works in
four tiers to minimize latency and maximize reliability:

```
1. Hard command rules
   "pause", "resume", "skip", "next", "previous", "volume up/down"
   -> direct plugin call, no LLM, no classification overhead

2. Plugin-specific command parser
   Pattern-based matching for unambiguous plugin commands
   -> direct plugin call

3. LLM classifier
   For natural language commands that rules cannot handle:
   "play something chill", "throw on some Radiohead", "put on music for cooking"
   -> structured JSON output, Pydantic-validated schema
   -> if JSON invalid or intent = unknown: go to tier 4

4. General LLM fallback
   When routing is uncertain, treat as conversational Q&A
```

Intent schema (validated with Pydantic):

```python
class IntentResult(BaseModel):
    intent: Literal["spotify", "general", "mcp", "command", "unknown"]
    action: Optional[str]    # search_and_play | pause | resume | skip | volume | none
    query: Optional[str]
    reason: str              # short text - useful for debugging
```

Never ask the LLM for a calibrated confidence score and treat it as ground truth.
Use structural metadata instead (see Session log below).

---

## Rolling transcription behaviour

wstream uses a fixed 3-second step with a 10-second sliding window (no VAD).
Every 3 seconds it broadcasts the last 10 seconds re-transcribed. This means:
- Consecutive messages heavily overlap - do not concatenate them
- After wake word detected, wait for the utterance to stabilise
- Take the LAST received message as the complete utterance, strip the wake word
- No end-of-utterance signal exists - timing-based collection is the fallback

### Utterance collection

A fixed 4-second wait is too slow for short commands and too short for long ones.
Use a hybrid stabilizer instead:

```python
MIN_LISTEN_MS = 700
STABLE_WINDOWS_REQUIRED = 2
MAX_LISTEN_MS = 6500
```

Finish early if:
- Latest utterance is non-empty
- Latest utterance is very similar to the previous window (similarity threshold)
- No meaningful new words appeared for `STABLE_WINDOWS_REQUIRED` consecutive windows

This lets short commands ("pause music") finish in ~1 second after speaking, while
long commands continue collecting up to `MAX_LISTEN_MS`. The timing fallback remains
as the hard ceiling.

---

## Tech stack

| Component | Technology | Default | User-configurable |
|---|---|---|---|
| ASR | wstream (whisper.cpp) via WebSocket | `ggml-small.en-q5_1.bin` | Any whisper.cpp GGUF model |
| LLM | Ollama or any OpenAI-compatible server | `qwen3:0.6b` (benchmark candidate) | URL + model name in config |
| TTS | Piper | `en_US-lessac-medium.onnx` | Any Piper voice model |
| Spotify | spotipy (Spotify Web API, OAuth2) | - | Client ID/secret in config |
| Orchestrator | Python 3.11+, asyncio, websockets, httpx | - | - |
| Future tools | MCP protocol (astraea, home automation, etc.) | - | - |

Note: `qwen3:0.6b` is the default for low-end hardware but treat it as a benchmark
candidate, not a trusted production default. Test intent routing accuracy early
before committing to it. See Phase 2 and Phase 5 for validation approach.

---

## Project layout

### Phase 1 minimal layout

```
mira/
  mira.py              - main orchestrator entry point
  config.py            - load and validate mira.toml
  wake.py              - wake word detection and utterance collection
  tts.py               - Piper TTS wrapper (speak, play_ding)
  llm.py               - LLM chat wrapper, conversation history
  observe.py           - session logger, latency timer, interaction record
```

### Full target layout

```
mira/
  mira.py              - main orchestrator entry point
  config.py            - load and validate mira.toml
  wake.py              - wake word detection and utterance collection
  tts.py               - Piper TTS wrapper (speak, play_ding)
  llm.py               - LLM chat wrapper, conversation history
  intent.py            - layered intent router (rules + LLM + fallback)
  observe.py           - session logger, latency timer, interaction record
  cli.py               - mira stats / bench / log / replay / feedback commands
  plugins/
    __init__.py        - plugin registry
    spotify.py         - Spotify handler
  tools/
    __init__.py        - MCP tool adapters
  bench/
    __init__.py
    whisper.py         - whisper model benchmark runner
    intent.py          - intent classification benchmark runner
    latency.py         - end-to-end latency benchmark
    wakeword.py        - wake word false positive/negative analysis
  models/              - Piper voice model files (.onnx + .json)
  sounds/
    ding.wav           - wake acknowledgement sound
  PLAN.md
  README.md
  requirements.txt
  mira.toml.example
```

---

## Milestones

These three milestones determine whether the foundation is solid before building
plugins, MCP integration, or benchmarking tools.

**Milestone 1** - Speed

> "Hey Mira, pause music" responds in less than 1.5 seconds after I stop speaking.

This validates utterance stabilizer quality, hard command routing (no LLM needed),
and TTS startup latency.

**Milestone 2** - Routing accuracy

> "Hey Mira, play something chill" routes to Spotify correctly at least 9 out of 10
> times in a small local test set.

This validates the LLM classifier and schema validation. Run before adding more
plugins, not after.

**Milestone 3** - Debuggability

> Every failed interaction can be fully diagnosed from the JSONL session log alone.

This validates the observability design. If you need to add a print statement to
understand a failure, the log schema is incomplete.

---

## Phases

### Phase 1 - Core voice loop (MVP)

Goal: "Hey Mira, what time is it?" works end to end.

- [ ] Project scaffold (config, entry point, requirements)
- [ ] wstream WebSocket client + wake word detection (`wake.py`)
- [ ] Utterance collection - timing fallback (wait for stable window or max timeout)
- [ ] Piper TTS wrapper (`tts.py`)
- [ ] LLM wrapper with conversation history (`llm.py`)
- [ ] Main state machine (`mira.py`)
- [ ] JSONL session logger + latency timer (`observe.py`)
- [ ] Basic general Q&A working via voice

### Phase 1.5 - Interaction reliability

Goal: Short commands respond quickly; long commands are not cut off.

- [ ] Utterance stabilizer - similarity-based early finish (`wake.py`)
  - `MIN_LISTEN_MS = 700`, `STABLE_WINDOWS_REQUIRED = 2`, `MAX_LISTEN_MS = 6500`
  - Early finish when utterance stable for N windows and no new words appearing
  - Timing fallback remains as hard ceiling
- [ ] Better wake word stripping (handle transcription variants of "hey mira")
- [ ] Live debug output showing every window received, utterance candidate, finish reason
- [ ] Optional: patch wstream to emit `{"type": "silence", "duration_ms": 800}`
  - Simple energy-based detector is enough - does not need full VAD
  - This makes Mira feel significantly more natural
  - Not required to proceed to Phase 2, but do not defer until Phase 6

### Phase 2 - Intent routing MVP

Goal: Commands route to the right handler reliably before Spotify is built.

- [ ] Hard command rules - direct dispatch with no LLM
  - Exact and fuzzy match: pause, resume, skip, next, previous, volume up/down
- [ ] LLM classifier for natural language commands (`intent.py`)
  - Pydantic schema validation - invalid JSON becomes `intent = unknown`
  - Unknown intent falls back to general LLM, never crashes
- [ ] Session log classifier metadata (see Observability section)
- [ ] Small labeled intent test set (~30 samples) in `bench/intent_samples.json`
- [ ] Manual accuracy check before proceeding to Phase 3

Reach Milestone 2 before starting Phase 3.

### Phase 3 - Spotify integration

Goal: "Hey Mira, play some jazz" and "Hey Mira, skip this" work.

- [ ] Spotify OAuth setup + token cache (`plugins/spotify.py`)
- [ ] Pause, resume, skip, previous, volume (command rules first - these need no LLM)
- [ ] LLM-based action extraction for search: maps voice command to structured action
- [ ] Action executor: search_and_play, what_is_playing
- [ ] Playlist support: "play my workout playlist"
- [ ] Spoken feedback: "Playing Radiohead" / "Paused" / "Volume set to 60"

### Phase 4 - MCP tool expansion

Goal: "Hey Mira, ask the tenancy assistant about bond refunds" works.

- [ ] MCP client adapter in `tools/`
- [ ] Connect to astraea MCP servers (nz-tenancy, nz-building)
- [ ] Intent routing: music / general / legal / mcp-tool
- [ ] Plugin registry so new tools register without touching orchestrator

### Phase 5 - Observability and benchmarking

Goal: `mira stats`, `mira latency`, `mira bench` all work.

- [ ] `cli.py`: stats, log, latency, feedback, replay commands
- [ ] `bench/whisper.py`: compare whisper models on audio samples
- [ ] `bench/intent.py`: accuracy benchmark against labeled intent samples
- [ ] `bench/latency.py`: end-to-end latency with fixed prompts
- [ ] `bench/wakeword.py`: false positive/negative rate analysis
- [ ] `--debug` live mode (prints all windows, intent reasoning, LLM prompt)
- [ ] Graceful handling of LLM timeout, Spotify auth expiry, wstream disconnect
- [ ] Auto-reconnect to wstream WebSocket
- [ ] "Hey Mira, stop" interrupt - cancel in-progress TTS
- [ ] systemd user service for always-on operation

### Phase 6 - Hardware/model polish (August 2026)

Target: Mac Mini M4 Pro.

- [ ] Re-benchmark all components with M4 Pro GPU offload
- [ ] Evaluate larger LLM (7B/14B) for intent classification quality
- [ ] Evaluate faster TTS (kokoro, StyleTTS2)
- [ ] Wstream VAD for cleaner utterance detection (if silence event not added earlier)
- [ ] Homebridge / Home Assistant integration (lights, switches)

---

## Key design decisions

**Why LLM for intent classification instead of regex?**
Voice commands are unpredictable. "Throw on some Radiohead", "play that song from
Guardians of the Galaxy", and "I want to listen to something chill" all mean the same
thing. The LLM handles natural variation; regex would need hundreds of patterns.
However, the LLM is tier 3 in the router. Deterministic rules handle obvious commands
first so that common interactions like pause/skip never pay LLM latency.

**Why not trust qwen3:0.6b as the default intent classifier?**
Intent classification is the switchboard. A wrong route makes the assistant feel
broken, not just slow. Use 0.6b as a benchmark starting point, validate against the
labeled intent test set early, and be prepared to use 1.7b or larger if accuracy is
not sufficient. The layered router with Pydantic validation contains failures - an
invalid response becomes `intent = unknown` rather than a crash or silent misroute.

**Why not store confidence scores from the LLM?**
LLMs do not produce calibrated probabilities. A self-reported `confidence: 0.94`
looks precise but is meaningless. Instead, log structural metadata: where the decision
came from (`rule`, `llm`, `fallback`), whether the JSON was valid, and whether a
fallback was used. After you have labeled benchmark data, you can compute real accuracy
numbers for each classifier/model combination (see Phase 5).

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
word                    = "hey mira"
min_listen_ms           = 700
stable_windows_required = 2
max_listen_ms           = 6500

[spotify]
client_id     = ""
client_secret = ""
redirect_uri  = "http://localhost:8888/callback"
token_cache   = "~/.mira/spotify_token"
```

---

## Observability, debugging and benchmarking

This is where Mira goes beyond every comparable project. Core logging is built in
Phase 1 (session log, latency timers). The full CLI and benchmark suite ships in
Phase 5, after the assistant is working and useful.

### Session log

Every interaction is written to `~/.mira/sessions/YYYYMMDD.jsonl`. One JSON object
per interaction, appended in real time:

```json
{
  "id": "uuid",
  "ts": "2026-06-03T21:14:00.123Z",
  "raw_windows": [
    "hey mira play some jazz",
    "hey mira play some jazz music",
    "hey mira play some jazz music please"
  ],
  "wake_window": "hey mira play some jazz",
  "utterance": "play some jazz music please",
  "utterance_finish_reason": "stable",
  "intent": {
    "type": "spotify",
    "action": "search_and_play",
    "query": "jazz",
    "type_arg": "playlist"
  },
  "classifier": {
    "source": "llm",
    "valid_json": true,
    "matched_rule": null,
    "fallback_used": false
  },
  "plugin": "spotify",
  "plugin_result": {
    "success": true,
    "track": "Kind of Blue - Miles Davis"
  },
  "llm_response": "Playing Kind of Blue by Miles Davis.",
  "tts_text": "Playing Kind of Blue by Miles Davis.",
  "latency_ms": {
    "wake_to_utterance": 4012,
    "intent_classify": 218,
    "plugin_execute": 387,
    "llm_generate": null,
    "tts_synthesize": 291,
    "tts_play": 3400,
    "total_end_to_end": 8308
  },
  "outcome": "success",
  "feedback": null
}
```

`classifier.source` is `"rule"`, `"llm"`, or `"fallback"` - tells you exactly how
the decision was made without any fake probability numbers.

`utterance_finish_reason` is `"stable"` (stabilizer fired) or `"timeout"` (max
listen time hit). A high rate of `"timeout"` means commands are being cut off.

`feedback` is set when the user says "hey mira that was wrong" or via CLI.

`llm_generate` is null for pure plugin actions (Spotify play/pause); filled for
responses that go through the LLM.

### Latency breakdown

Mira measures every component individually so you know exactly where time is spent:

| Metric | What it covers |
|---|---|
| `wake_to_utterance` | Time from first wake-word window to utterance collected |
| `intent_classify` | LLM call to classify intent and extract action |
| `plugin_execute` | Spotify API call / MCP tool call / external service |
| `llm_generate` | LLM call for general conversational response |
| `tts_synthesize` | Piper synthesis to raw audio |
| `tts_play` | Audio playback duration |
| `total_end_to_end` | Wake word detected to first audio output |

### CLI tools (`mira` command) - Phase 5

```bash
# Show stats for recent sessions
mira stats
mira stats --since 7d

# Show latency percentiles per component
mira latency
mira latency --since 24h --percentiles 50,95,99

# Show intent classification breakdown
mira intents --since 7d

# Replay a past interaction (re-runs through current model/settings)
mira replay <interaction-id>
mira replay <interaction-id> --llm-model qwen3:1.7b

# Mark an interaction as good or bad
mira feedback <interaction-id> good
mira feedback <interaction-id> bad --note "wrong artist"

# List interactions with optional filter
mira log --intent spotify --since 24h
mira log --outcome failure
```

### Benchmark suite (`mira bench`) - Phase 5

```bash
# Compare whisper models on a set of audio samples
mira bench whisper --samples ~/.mira/bench/audio/ --models tiny.en,base.en,small.en

# Compare LLMs for intent classification accuracy
mira bench intent --samples ~/.mira/bench/intent_samples.json \
                  --models qwen3:0.6b,qwen3:1.7b

# Measure end-to-end latency with fixed prompts
mira bench latency --runs 10

# Wake word false positive rate (feed non-wake audio, count triggers)
mira bench wakeword --audio ~/.mira/bench/background_audio/
```

Benchmark results written to `~/.mira/bench/results/` as JSON + printed as a table.

Sample intent benchmark file (`intent_samples.json`):

```json
[
  {"utterance": "play some jazz",              "expected": {"type": "spotify", "action": "search_and_play"}},
  {"utterance": "skip this song",              "expected": {"type": "spotify", "action": "skip"}},
  {"utterance": "what time is it in Tokyo",    "expected": {"type": "general"}},
  {"utterance": "pause the music",             "expected": {"type": "spotify", "action": "pause"}},
  {"utterance": "what are my rights as tenant","expected": {"type": "mcp", "server": "nz-tenancy"}}
]
```

Accuracy, latency mean/p95, and per-sample diff printed after each run. This is
where benchmark-derived accuracy replaces per-interaction fake confidence: "For
qwen3:0.6b, spotify intent accuracy on this sample set is 91%."

### Wake word analysis

Every transcription window is logged (not just the ones with wake words). This lets
you analyze:
- Which phrasings of "hey mira" whisper transcribes reliably vs. incorrectly
- False trigger rate (how often a non-wake phrase accidentally triggers)
- Which words get dropped or mangled by the whisper model

```bash
mira analyze wakeword --since 7d
# Shows: trigger rate, false positive rate, transcription variants seen
```

### Live debug mode

```bash
mira --debug
```

Prints every transcription window received from wstream, the intent classification
reasoning, plugin calls made, and exact LLM prompt sent. Equivalent to astraea's
`context_debug` SSE event - full transparency into every step.

### Improvement loop

The observability data drives a concrete improvement cycle:

```
1. Run mira stats / mira latency   -> identify slow components
2. Run mira bench whisper          -> find faster/more accurate ASR model
3. Run mira bench intent           -> validate intent accuracy before deploying new LLM
4. Mark bad interactions           -> build ground truth dataset
5. Re-run mira bench intent        -> confirm improvement
```

No other local voice assistant ships this workflow. Most have zero structured logging.

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

**Phase 1.5 priority (do not defer to Phase 6):**

Add a simple energy-based silence detector emitting:

```json
{"type": "silence", "duration_ms": 800}
```

A full VAD implementation is not needed. An energy threshold is enough to detect
when the user has stopped speaking. This makes utterance collection significantly
more natural and removes the dependence on window similarity heuristics. If this
lands in Phase 1.5, the stabilizer's `MAX_LISTEN_MS` becomes a safety net rather
than the primary mechanism.

Possible future additions (not required now):
- `--lang` CLI arg for multilingual support (currently hardcoded English)
- `--vad` flag to enable VAD mode without recompiling
