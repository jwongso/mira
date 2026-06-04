---
name: user-language-profile
description: User is non-native English, also speaks German and Indonesian dialect - affects model choice and wake word design
metadata:
  type: user
---

Non-native English speaker. Also speaks German and Indonesian (possibly Javanese dialect).
Child in household speaks Indonesian/Arabic ("abi" = father).

**Why it matters:**
- `.en.` whisper models fail on accented English and garble German/Indonesian words entirely
- Multilingual model with `language="auto"` in command mode handles mixed-language commands
- Forcing `language="en"` in scan mode prevents "嘿" / "Ai" / Javanese false detections for wake word

**Current working config:**
- Model: `ggml-small-q8_0.bin` (multilingual small)
- scan_params: `language="en"` - wake word always English phonetics
- cmd_params: `language="auto"` - commands can be German/Indonesian

**Wake word pronunciation:**
- User says "Hi Mira" or "Hey Mira" (both work)
- German/Indonesian accent: "Mira" = MEE-rah (not English MY-rah)
- With forced English scan mode, whisper now transcribes "Hi, Mira." cleanly
