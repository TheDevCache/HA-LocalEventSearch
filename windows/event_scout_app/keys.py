"""API keys, kept in Windows Credential Manager (via keyring).

If no secure store is available (some Linux setups used for development), keys
fall back to a file in the data folder that only the current user can read.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

_LOGGER = logging.getLogger(__name__)
SERVICE = "LocalEventScout"
NAMES = ("api_key", "gemini_search_key", "gemini_key")


class KeyStore:
    def __init__(self, folder: Path) -> None:
        self._file = folder / "keys.json"
        self.backend = "file"
        self._keyring = None
        try:
            import keyring  # noqa: PLC0415
            from keyring.backends import fail  # noqa: PLC0415

            if not isinstance(keyring.get_keyring(), fail.Keyring):
                keyring.get_password(SERVICE, "probe")  # raises if the backend can't work
                self._keyring = keyring
                self.backend = keyring.get_keyring().__class__.__name__
        except Exception as err:  # noqa: BLE001 - any keyring problem means: use the file
            _LOGGER.info("No secure key store (%s); keeping keys in %s", err, self._file)

    def get_all(self) -> dict[str, str]:
        if self._keyring:
            return {name: self._keyring.get_password(SERVICE, name) or "" for name in NAMES}
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return {name: str(data.get(name) or "") for name in NAMES}

    def set_all(self, keys: dict[str, str]) -> None:
        if self._keyring:
            for name in NAMES:
                value = keys.get(name) or ""
                if value:
                    self._keyring.set_password(SERVICE, name, value)
                else:
                    try:
                        self._keyring.delete_password(SERVICE, name)
                    except Exception:  # noqa: BLE001 - nothing stored
                        pass
            return
        self._file.parent.mkdir(parents=True, exist_ok=True)
        temp = self._file.with_suffix(".tmp")
        temp.write_text(json.dumps({n: keys.get(n) or "" for n in NAMES}), encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, self._file)
