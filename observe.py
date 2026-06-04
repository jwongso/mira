"""Session logger - writes one JSONL record per completed interaction."""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

SESSIONS_DIR = Path.home() / ".mira" / "sessions"


class Interaction:
    """Wraps the mira-poc 'end' JSON event."""
    def __init__(self, event: dict):
        self._e = event
        self.started_at = time.monotonic()

    @property
    def id(self)        -> str:  return self._e.get("id", "")
    @property
    def text(self)      -> str:  return self._e.get("text", "")
    @property
    def wake_text(self) -> str:  return self._e.get("wake_text", "")
    @property
    def windows(self)   -> list: return self._e.get("windows", [])
    @property
    def wav(self)       -> str:  return self._e.get("wav", "")
    @property
    def ts(self)        -> float: return self._e.get("ts", 0.0)


class SessionLogger:
    def __init__(self):
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        date = datetime.now().strftime("%Y%m%d")
        self.path = SESSIONS_DIR / f"{date}.jsonl"

    def write(self, ix: Interaction, response: str, **extra):
        record = {
            "id":        ix.id,
            "ts":        datetime.now(timezone.utc).isoformat(),
            "wake_text": ix.wake_text,
            "text":      ix.text,
            "windows":   ix.windows,
            "wav":       ix.wav,
            "response":  response,
            **extra,
        }
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")
