"""Spotify tool for Mira - exposes play/pause/skip/volume to the LLM."""
import json
from pathlib import Path
from typing import Any

import spotipy
from spotipy.oauth2 import SpotifyOAuth

CACHE_PATH = Path.home() / ".mira" / "spotify_cache"

SCOPES = (
    "user-modify-playback-state "
    "user-read-playback-state "
    "user-read-currently-playing"
)

# Tool schemas the LLM can call
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "spotify_play",
            "description": (
                "Search Spotify and start playing music. "
                "Use for requests like 'play rock music', 'play Adele', "
                "'play sad songs', 'play Max Richter Sleep'. "
                "type can be 'track', 'album', 'playlist', or 'artist'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query, e.g. 'Max Richter On the Nature of Daylight'"
                    },
                    "type": {
                        "type": "string",
                        "enum": ["track", "album", "playlist", "artist"],
                        "description": "What to search for. Default 'playlist' for genre/mood requests."
                    }
                },
                "required": ["query", "type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "spotify_control",
            "description": "Control Spotify playback: pause, resume, skip to next track, or go to previous track.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["pause", "resume", "next", "previous"],
                        "description": "Playback action to perform."
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "spotify_volume",
            "description": "Set or change Spotify volume. percent is 0-100.",
            "parameters": {
                "type": "object",
                "properties": {
                    "percent": {
                        "type": "integer",
                        "description": "Volume level 0-100."
                    },
                    "delta": {
                        "type": "integer",
                        "description": "Relative change (+10 louder, -10 quieter). Use instead of percent."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "spotify_now_playing",
            "description": "Get the currently playing track and artist on Spotify.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
]


_sp: spotipy.Spotify | None = None
_cfg: dict = {}


def init(client_id: str, client_secret: str, redirect_uri: str) -> None:
    """Call once at startup with credentials from config."""
    global _cfg
    _cfg = {"client_id": client_id, "client_secret": client_secret,
            "redirect_uri": redirect_uri}


def _client() -> spotipy.Spotify:
    global _sp
    if _sp is None:
        auth = SpotifyOAuth(
            client_id=_cfg["client_id"],
            client_secret=_cfg["client_secret"],
            redirect_uri=_cfg["redirect_uri"],
            scope=SCOPES,
            cache_path=str(CACHE_PATH),
            open_browser=True,
        )
        _sp = spotipy.Spotify(auth_manager=auth)
    return _sp


def pause_for_speech() -> bool:
    """Pause Spotify if currently playing. Returns True if it was playing."""
    if not _cfg:
        return False
    try:
        pb = _client().current_playback()
        if pb and pb.get("is_playing"):
            _client().pause_playback()
            return True
    except Exception:
        pass
    return False


def resume_after_speech() -> None:
    """Resume Spotify playback."""
    if not _cfg:
        return
    try:
        _client().start_playback()
    except Exception:
        pass


def execute(name: str, raw_args: str) -> str:
    try:
        args: dict[str, Any] = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError:
        args = {}
    try:
        if name == "spotify_play":
            return _play(args.get("query", ""), args.get("type", "playlist"))
        if name == "spotify_control":
            return _control(args.get("action", ""))
        if name == "spotify_volume":
            return _volume(args.get("percent"), args.get("delta"))
        if name == "spotify_now_playing":
            return _now_playing()
        return f"Unknown spotify tool: {name}"
    except spotipy.SpotifyException as e:
        return f"Spotify error: {e}"
    except Exception as e:
        return f"Error: {e}"


def _play(query: str, stype: str) -> str:
    sp = _client()
    results = sp.search(q=query, type=stype, limit=1)
    items = results.get(stype + "s", {}).get("items", [])
    if not items:
        return f"Nothing found for '{query}'."
    item = items[0]
    name = item.get("name", "Unknown")
    uri  = item.get("uri", "")
    # Prefer the local spotifyd device; fall back to whatever is active
    devices = sp.devices().get("devices", [])
    if not devices:
        return "No Spotify device found. Is spotifyd running?"
    device = next((d for d in devices if d["name"] == "mira"), devices[0])
    device_id = device["id"]
    if stype == "track":
        sp.start_playback(device_id=device_id, uris=[uri])
    else:
        sp.start_playback(device_id=device_id, context_uri=uri)
    artist = ""
    if stype == "track" and item.get("artists"):
        artist = f" by {item['artists'][0]['name']}"
    return f"Playing {name}{artist}."


def _control(action: str) -> str:
    sp = _client()
    if action == "pause":
        sp.pause_playback()
        return "Paused."
    if action == "resume":
        sp.start_playback()
        return "Resumed."
    if action == "next":
        sp.next_track()
        return "Skipped to next track."
    if action == "previous":
        sp.previous_track()
        return "Went back to previous track."
    return f"Unknown action: {action}"


def _volume(percent: int | None, delta: int | None) -> str:
    sp = _client()
    if delta is not None:
        current = (sp.current_playback() or {}).get("device", {}).get("volume_percent", 50)
        percent = max(0, min(100, current + delta))
    if percent is None:
        return "Specify percent or delta."
    sp.volume(percent)
    return f"Volume set to {percent}%."


def _now_playing() -> str:
    sp = _client()
    pb = sp.current_playback()
    if not pb or not pb.get("item"):
        return "Nothing is playing right now."
    track  = pb["item"]["name"]
    artist = pb["item"]["artists"][0]["name"]
    return f"Playing '{track}' by {artist}."
