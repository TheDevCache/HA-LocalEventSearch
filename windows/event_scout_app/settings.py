"""Where the app keeps its files, and its own small settings file (app.json)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sys

APP_NAME = "LocalEventScout"
DEFAULT_PORT = 47821
DEFAULT_FEED_PORT = 47822


def default_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_NAME


@dataclass
class AppSettings:
    provider: str = ""  # "" until the user connects an AI provider
    port: int = DEFAULT_PORT
    lan_feed: bool = False  # also serve the calendar feed to other devices on the network
    feed_port: int = DEFAULT_FEED_PORT
    opened_once: bool = False

    @classmethod
    def load(cls, folder: Path) -> "AppSettings":
        try:
            raw = json.loads((folder / "app.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in raw.items() if k in fields})

    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        temp = folder / "app.json.tmp"
        temp.write_text(json.dumps(asdict(self), indent=1), encoding="utf-8")
        os.replace(temp, folder / "app.json")
