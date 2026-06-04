import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CFG = Path(__file__).parent / "mira.toml"


@dataclass
class Config:
    asr_bin:          Path
    asr_model:        Path
    llm_url:          str
    llm_model:        str
    tts_model:        Path
    spotify_id:       str  = ""
    spotify_secret:   str  = ""
    spotify_redirect: str  = ""
    debug:            bool = False


def load(path: Path = DEFAULT_CFG) -> Config:
    with open(path, "rb") as f:
        d = tomllib.load(f)
    sp = d.get("spotify", {})
    return Config(
        asr_bin          = Path(d["asr"]["bin"]).expanduser(),
        asr_model        = Path(d["asr"]["model"]).expanduser(),
        llm_url          = d["llm"]["url"],
        llm_model        = d["llm"]["model"],
        tts_model        = Path(d["tts"]["model"]).expanduser(),
        spotify_id       = sp.get("client_id", ""),
        spotify_secret   = sp.get("client_secret", ""),
        spotify_redirect = sp.get("redirect_uri", ""),
    )
