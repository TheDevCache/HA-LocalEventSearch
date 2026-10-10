"""The Windows app's host and local server (they run anywhere; only the tray is Windows-only)."""

from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "windows"))

from event_scout_app.host import AppHost  # noqa: E402
from event_scout_app.keys import KeyStore  # noqa: E402
from event_scout_app.main import self_test  # noqa: E402
from event_scout_app.settings import AppSettings  # noqa: E402
from event_scout_app.updates import newer, parse_version  # noqa: E402


async def test_self_test_passes(tmp_path):
    assert await self_test(tmp_path / "report.txt") == 0
    assert "SELF-TEST OK" in (tmp_path / "report.txt").read_text()


async def test_next_run_and_provider_state(tmp_path):
    async with aiohttp.ClientSession() as session:
        host = AppHost(tmp_path, session)
        await host.start(background_jobs=False)
        host.tz = ZoneInfo("America/Edmonton")
        host.engine.config["schedule"] = "02:30"
        before = datetime(2026, 10, 10, 1, 0, tzinfo=host.tz)
        after = datetime(2026, 10, 10, 3, 0, tzinfo=host.tz)
        assert host._next_run(before) == datetime(2026, 10, 10, 2, 30, tzinfo=host.tz)
        assert host._next_run(after) == datetime(2026, 10, 11, 2, 30, tzinfo=host.tz)
        assert not host.provider_ready
        state = host.public_state()
        assert state["host"] == "windows" and state["provider_ready"] is False and not state["can_locate"]
        try:
            host.request_scan()
        except ValueError as err:
            assert "AI provider" in str(err)
        else:
            raise AssertionError("scan should need a provider")
        await host.stop()


def test_settings_and_file_keys(tmp_path):
    settings = AppSettings.load(tmp_path)
    settings.provider, settings.lan_feed = "gemini", True
    settings.save(tmp_path)
    assert AppSettings.load(tmp_path).lan_feed is True
    store = KeyStore(tmp_path)
    store._keyring = None  # force the file fallback
    store.set_all({"gemini_search_key": "abc"})
    assert store.get_all() == {"api_key": "", "gemini_search_key": "abc", "gemini_key": ""}


def test_versions():
    assert parse_version("v0.10.2") == (0, 10, 2)
    assert newer("0.5.0", "0.5.1") and not newer("0.5.0", "v0.5.0") and not newer("1.0.0", "0.9.9")
