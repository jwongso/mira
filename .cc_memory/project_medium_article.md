---
name: project-medium-article
description: User wants to write a Medium article about the hybrid VAD + 5s sliding window design someday
metadata:
  type: project
---

User wants to document the hybrid VAD / fixed-chunk architecture for a Medium article.

**Core concept to document:**
- Problem: pure VAD has dynamic timing - adjacent utterances merge, late captures, unpredictable windows
- Problem: pure fixed windows waste CPU (whisper runs every 5s even on silence)
- Solution: hybrid two-state machine
  - SCANNING state: VAD-gated whisper, fires only when speech ends, looking for wake word
  - COMMAND state: fixed 5s chunks with 200ms overlap, deterministic timing
  - Transition: wake word -> command mode; silence (energy gate or BLANK_AUDIO) -> back to scanning
- The 200ms overlap prevents words at chunk boundaries from being cut off
- scan_params forces language="en" to prevent multilingual model transcribing "hey" as "嘿" or "Ai"
- cmd_params uses language="auto" so commands can be in any language

**Why:** User discovered this through iterating on the PoC - VAD caused merging and timing issues in command mode, fixed windows caused CPU waste in scanning mode.

**How to apply:** Keep this in mind when discussing Mira architecture; the article should come from real PoC experience, not theory.
