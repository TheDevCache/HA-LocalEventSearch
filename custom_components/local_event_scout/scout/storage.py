"""Where the engine keeps settings and results. Each host supplies its own storage."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Protocol


class Storage(Protocol):
    async def load_config(self) -> dict[str, Any] | None: ...
    async def save_config(self, data: dict[str, Any]) -> None: ...
    async def load_results(self) -> dict[str, Any] | None: ...
    async def save_results(self, data: dict[str, Any]) -> None: ...


class MemoryStorage:
    """Keeps everything in memory (tests, one-off CLI runs)."""

    def __init__(self, config: dict[str, Any] | None = None, results: dict[str, Any] | None = None) -> None:
        self.config = config
        self.results = results
        self.saves = 0

    async def load_config(self) -> dict[str, Any] | None:
        return json.loads(json.dumps(self.config)) if self.config is not None else None

    async def save_config(self, data: dict[str, Any]) -> None:
        self.config = json.loads(json.dumps(data))

    async def load_results(self) -> dict[str, Any] | None:
        return json.loads(json.dumps(self.results)) if self.results is not None else None

    async def save_results(self, data: dict[str, Any]) -> None:
        self.saves += 1
        self.results = json.loads(json.dumps(data))


class JsonFileStorage:
    """Two JSON files in a folder, written atomically (the Windows app and the CLI)."""

    def __init__(self, folder: str | os.PathLike[str]) -> None:
        self.folder = Path(folder)

    async def load_config(self) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._read, "config.json")

    async def save_config(self, data: dict[str, Any]) -> None:
        await asyncio.to_thread(self._write, "config.json", data)

    async def load_results(self) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._read, "results.json")

    async def save_results(self, data: dict[str, Any]) -> None:
        await asyncio.to_thread(self._write, "results.json", data)

    def _read(self, name: str) -> dict[str, Any] | None:
        path = self.folder / name
        try:
            with path.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Keep a damaged file for inspection and start fresh rather than refusing to run.
            # (Other read errors, such as a locked file, are raised so good data is never set aside.)
            path.replace(path.with_suffix(".damaged.json"))
            return None
        return data if isinstance(data, dict) else None

    def _write(self, name: str, data: dict[str, Any]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / name
        temp = path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
        os.replace(temp, path)
