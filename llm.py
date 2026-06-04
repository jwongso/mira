import re
import time
import httpx

import tools as _tools
import spotify_tool as _spotify

SYSTEM_PROMPT = (
    "You are Mira, a helpful local voice assistant. "
    "Respond in plain spoken sentences only - 1 to 3 sentences maximum. "
    "Never use markdown, asterisks, bullet points, numbered lists, headers, "
    "backticks, bold, italics, or any other formatting. "
    "Never use emoji. Write exactly as you would speak aloud. "
    "When you have a tool available for a request, call it immediately without "
    "lengthy explanation first."
)

_THINK_RE    = re.compile(r"<think>.*?</think>", re.DOTALL)
_BOLD_ITALIC = re.compile(r"\*{1,3}(.*?)\*{1,3}", re.DOTALL)
_CODE_RE     = re.compile(r"`+([^`]*)`+")
_HEADER_RE   = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_BULLET_RE   = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_NUMBERED_RE = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
_EMOJI_RE    = re.compile(
    "[\U0001F300-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002600-\U000027BF"
    "️]+",
    flags=re.UNICODE,
)

_MAX_TOOL_ROUNDS = 4


def _clean(text: str) -> str:
    text = _THINK_RE.sub("", text)
    text = _BOLD_ITALIC.sub(r"\1", text)
    text = _CODE_RE.sub(r"\1", text)
    text = _HEADER_RE.sub("", text)
    text = _BULLET_RE.sub("", text)
    text = _NUMBERED_RE.sub("", text)
    text = _EMOJI_RE.sub("", text)
    return text.strip()


class LLMClient:
    def __init__(self, url: str, model: str, history_turns: int = 6):
        self.url = url
        self.model = model
        self.history_turns = history_turns
        self._history: list[dict] = []

    async def chat(self, text: str) -> tuple[str, float]:
        """Send a user message, execute any tool calls, return (reply, latency_s)."""
        self._history.append({"role": "user", "content": text})

        # Build messages from history (tools in/out are ephemeral, not stored)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *self._history[-(self.history_turns * 2):],
        ]

        t0        = time.monotonic()
        all_tools = _tools.TOOLS + _spotify.TOOLS

        for _round in range(_MAX_TOOL_ROUNDS):
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(self.url, json={
                    "model":       self.model,
                    "messages":    messages,
                    "tools":       all_tools,
                    "tool_choice": "auto",
                    "stream":      False,
                    "temperature": 0.7,
                })
                r.raise_for_status()

            data   = r.json()
            choice = data["choices"][0]
            msg    = choice["message"]

            if choice["finish_reason"] == "tool_calls":
                # Append the assistant turn (tool_calls only, no reasoning)
                messages.append({
                    "role":       "assistant",
                    "content":    msg.get("content") or "",
                    "tool_calls": msg["tool_calls"],
                })
                # Execute each tool and feed results back
                for tc in msg["tool_calls"]:
                    fn   = tc["function"]["name"]
                    farg = tc["function"].get("arguments", "{}")
                    if fn.startswith("spotify_"):
                        result = _spotify.execute(fn, farg)
                    else:
                        result = _tools.execute(fn, farg)
                    messages.append({
                        "role":         "tool",
                        "tool_call_id": tc["id"],
                        "content":      result,
                    })
            else:
                # Final text response
                reply = _clean(msg.get("content", ""))
                self._history.append({"role": "assistant", "content": reply})
                return reply, time.monotonic() - t0

        # Exceeded tool rounds
        reply = "Sorry, I ran into a problem processing that."
        self._history.append({"role": "assistant", "content": reply})
        return reply, time.monotonic() - t0

    def reset(self):
        self._history.clear()
