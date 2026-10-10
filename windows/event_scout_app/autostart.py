"""Start with Windows: a value under HKCU\\...\\Run (no admin rights needed)."""

from __future__ import annotations

import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "LocalEventScout"


def supported() -> bool:
    return sys.platform == "win32" and getattr(sys, "frozen", False)


def _command() -> str:
    return f'"{sys.executable}" --background'


def enabled() -> bool:
    if not supported():
        return False
    import winreg  # noqa: PLC0415

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE)
            return value == _command()  # an entry for a moved or older copy doesn't count
    except OSError:
        return False


def set_enabled(on: bool) -> None:
    if not supported():
        raise ValueError("Start with Windows is only available in the installed Windows app.")
    import winreg  # noqa: PLC0415

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, VALUE, 0, winreg.REG_SZ, _command())
        else:
            try:
                winreg.DeleteValue(key, VALUE)
            except FileNotFoundError:
                pass
