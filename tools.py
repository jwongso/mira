"""Mira tool registry - functions the LLM can call via tool_choice=auto."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Tool schemas sent to the LLM with every request
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
    }
]


def execute(name: str, raw_args: str) -> str:
    """Execute a tool call. raw_args is the JSON string from the LLM."""
    try:
        args = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError:
        args = {}

    if name == "get_time":
        return _get_time(args.get("timezone"))
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
