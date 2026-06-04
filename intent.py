"""Layered intent router - Phase 1: hard rules + LLM general fallback."""
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class Intent:
    type:          str
    action:        Optional[str]
    text:          str
    source:        str
    close_session: bool = False


_CLOSE_RE = re.compile(
    r"\b("
    r"stop listening|be quiet|shut up|go to sleep"
    r"|that'?s? (is )?all|that'?s? (is )?enough|no more|nothing else|never mind|nevermind"
    r"|goodbye|good ?bye|bye|see you|see ya"
    r"|tsch[uü]ss|auf wiedersehen"
    r"|sampai jumpa|sudah cukup|terima kasih"
    r")\b",
    re.IGNORECASE,
)

# (pattern, action, spotify_action, volume_delta)
# spotify_action maps directly to spotify_tool.execute()
_RULES: list[tuple[str, str, Optional[str], Optional[int]]] = [
    (r"\b(pause|stop (the )?music|stop playing|pause (the )?music)\b",
     "pause",    "pause",    None),
    (r"\b(resume|unpause|continue playing|play again)\b",
     "resume",   "resume",   None),
    (r"\b(skip|next( song| track)?)\b",
     "skip",     "next",     None),
    (r"\b(previous|go back|last (song|track))\b",
     "previous", "previous", None),
    (r"\bvolume up\b",   "volume_up",   None, +15),
    (r"\bvolume down\b", "volume_down", None, -15),
]


def classify_hard(text: str) -> Optional[Intent]:
    t = text.lower().strip()

    if _CLOSE_RE.search(t):
        return Intent(type="command", action="close", text=text,
                      source="rule", close_session=True)

    for pattern, action, *_ in _RULES:
        if re.search(pattern, t):
            return Intent(type="command", action=action, text=text, source="rule")

    return None


def execute_hard(intent: Intent) -> str:
    """Execute a hard-rule Spotify action. Returns spoken response."""
    import spotify_tool
    import json

    for pattern, action, spotify_action, vol_delta in _RULES:
        if action != intent.action:
            continue
        try:
            if vol_delta is not None:
                result = spotify_tool.execute(
                    "spotify_volume", json.dumps({"delta": vol_delta}))
            elif spotify_action:
                result = spotify_tool.execute(
                    "spotify_control", json.dumps({"action": spotify_action}))
            else:
                result = "Done."
            return result
        except Exception as e:
            return f"Couldn't {action}: {e}"

    return "Done."
