"""Piper TTS - synthesize text and play via aplay."""
import subprocess
from pathlib import Path

from piper import PiperVoice

DEFAULT_MODEL = Path.home() / ".mira/models/piper/en_US-lessac-medium.onnx"

_voice: PiperVoice | None = None
_model_path: Path | None = None


def _get_voice(model_path: Path) -> PiperVoice:
    global _voice, _model_path
    if _voice is None or _model_path != model_path:
        _voice = PiperVoice.load(str(model_path))
        _model_path = model_path
    return _voice


def speak(text: str, model_path: Path = DEFAULT_MODEL) -> None:
    if not text.strip():
        return
    voice = _get_voice(model_path)

    chunks = list(voice.synthesize(text))
    if not chunks:
        return

    rate     = chunks[0].sample_rate
    channels = chunks[0].sample_channels
    width    = chunks[0].sample_width

    proc = subprocess.Popen(
        ["play", "-q",
         "-t", "raw",
         "-r", str(rate),
         "-e", "signed-integer",
         "-b", "16",
         "-c", str(channels),
         "-"],
        stdin=subprocess.PIPE,
    )
    for chunk in chunks:
        proc.stdin.write(chunk.audio_int16_bytes)
    proc.stdin.close()
    proc.wait()
