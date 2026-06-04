"""Mira tool registry - functions the LLM can call via tool_choice=auto."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Tool schemas sent to the LLM with every request
_mem = None   # set by init_memory()


def init_memory(mem) -> None:
    """Call once at startup to enable remember/recall tools."""
    global _mem
    _mem = mem


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": (
                "Get the current date and time. Optionally pass an IANA timezone "
                "name to get the time in a specific city or country. "
                "Examples: 'Europe/Berlin', 'America/New_York', 'Asia/Tokyo', "
                "'Asia/Jakarta', 'Pacific/Auckland'. "
                "Omit timezone for the local system time. "
                "Use this for any time, date, day-of-week, timezone difference, "
                "or time-calculation question."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone name. Omit for local time."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Save a user preference or personal fact to long-term memory. "
                "Call this when the user states something about themselves: "
                "'I love X', 'I hate Y', 'I live in Z', 'I work as W'. "
                "namespace groups memories by topic for precise later retrieval."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The fact or preference in plain English."
                    },
                    "type": {
                        "type": "string",
                        "enum": ["preference", "fact"],
                        "description": "preference = likes/dislikes; fact = personal info."
                    },
                    "namespace": {
                        "type": "string",
                        "enum": ["music", "food", "daily", "people", "places", "general"],
                        "description": "Topic namespace for efficient filtering."
                    },
                    "confidence": {
                        "type": "number",
                        "description": "How confident (0.0-1.0). Use 0.9 for explicit statements, 0.6 for inferred."
                    }
                },
                "required": ["text", "type", "namespace"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": (
                "Retrieve relevant memories about the user. "
                "Use when you need context to personalise a response - "
                "e.g. before playing music, recommending food, or answering "
                "questions about the user's preferences. "
                "Always filter by namespace for fast, relevant results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for, e.g. 'music for coding'."
                    },
                    "namespace": {
                        "type": "string",
                        "enum": ["music", "food", "daily", "people", "places", "general"],
                        "description": "Filter to this namespace only."
                    },
                    "type": {
                        "type": "string",
                        "enum": ["preference", "fact"],
                        "description": "Optional: filter to preferences or facts only."
                    }
                },
                "required": ["query", "namespace"]
            }
        }
    },
]


def execute(name: str, raw_args: str) -> str:
    """Execute a tool call. raw_args is the JSON string from the LLM."""
    try:
        args = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError:
        args = {}

    if name == "get_time":
        return _get_time(args.get("timezone"))
    if name == "remember":
        if _mem and _mem.ready:
            _mem.store(
                text=args.get("text", ""),
                type=args.get("type", "preference"),
                namespace=args.get("namespace", "general"),
                confidence=float(args.get("confidence", 0.8)),
            )
            return f"Remembered: {args.get('text', '')}"
        return "Memory not available."
    if name == "recall":
        if _mem and _mem.ready:
            hits = _mem.retrieve(
                query=args.get("query", ""),
                namespace=args.get("namespace"),
                type=args.get("type"),
                top_k=4,
            )
            if not hits:
                return "Nothing relevant found in memory."
            return "\n".join(f"- {h.text}" for h in hits)
        return "Memory not available."
    return f"Unknown tool: {name}"


def _get_time(timezone: str | None) -> str:
    if timezone:
        try:
            tz  = ZoneInfo(timezone)
            dt  = datetime.now(tz)
            return dt.strftime("%Y-%m-%d %H:%M:%S %Z (%A)")
        except (ZoneInfoNotFoundError, KeyError):
            # Fall back to local time and tell the LLM the timezone was bad
            dt = datetime.now().astimezone()
            return (f"Unknown timezone '{timezone}'. "
                    f"Local time is {dt.strftime('%Y-%m-%d %H:%M:%S %Z')}.")
    dt = datetime.now().astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z (%A)")
