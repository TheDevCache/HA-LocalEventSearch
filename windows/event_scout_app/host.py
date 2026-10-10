"""Runs the shared engine on a PC: storage, keys, the nightly schedule, retries, weather and updates."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from pathlib import Path
import socket
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from . import autostart, updates
from .core import VERSION, ScoutEngine, debug_info
from .keys import KeyStore
from .settings import AppSettings
from .weather import forecast_summary

from scout.defaults import PROVIDER_GEMINI, PROVIDER_OPENROUTER  # noqa: E402 - loaded by .core
from scout.openrouter import OpenRouterAuthError, OpenRouterClient, OpenRouterError  # noqa: E402
from scout.providers import validate_gemini_key  # noqa: E402
from scout.storage import JsonFileStorage  # noqa: E402

_LOGGER = logging.getLogger(__name__)

KEY_HINT = "Change it in Settings → AI provider."
FOLLOWUP_DELAY = timedelta(minutes=30)
MAX_FOLLOWUPS_PER_DAY = 3
SPEND_REFRESH = timedelta(hours=6)
UPDATE_CHECK = timedelta(hours=24)
CATCH_UP_DELAY = 60  # seconds after start before a missed nightly search runs
MAX_SLEEP = 300  # re-check the clock at least this often (the PC may have been asleep)


def local_zone() -> tuple[Any, str]:
    try:
        from tzlocal import get_localzone_name  # noqa: PLC0415

        name = get_localzone_name()
        return ZoneInfo(name), name
    except Exception:  # noqa: BLE001 - fall back to the offset Python knows
        tz = datetime.now().astimezone().tzinfo
        return tz, str(tz)


def lan_address() -> str:
    """This PC's address on the local network (best guess)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # no packet is sent for UDP connect
            return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class AppHost:
    def __init__(self, data_dir: Path, session: aiohttp.ClientSession, *, on_change: Callable[[], None] | None = None) -> None:
        self.data_dir = data_dir
        self.session = session
        self.settings = AppSettings.load(data_dir)
        self.keys = KeyStore(data_dir)
        self.tz, self.tz_name = local_zone()
        self._on_change = on_change
        self.engine = ScoutEngine(
            session,
            JsonFileStorage(data_dir),
            provider=self.settings.provider or PROVIDER_GEMINI,
            keys=self.keys.get_all(),
            tz=self.tz,
            tz_name=self.tz_name,
            home=None,
            weather=self._weather,
            notify=self._notify,
            retry_later=self._retry_later,
            key_hint=KEY_HINT,
            host="windows",
        )
        self.update: dict[str, str] | None = None
        self._tasks: list[asyncio.Task] = []
        self._scan_task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._followup: asyncio.TimerHandle | None = None
        self._followups: tuple[str, int] = ("", 0)
        self.on_lan_change: Callable[[], Any] | None = None  # set by the server

    # ------------------------------------------------------------- lifecycle

    async def start(self, *, background_jobs: bool = True) -> None:
        await self.engine.load()
        if background_jobs:
            self._tasks = [
                asyncio.create_task(self._scheduler(), name="schedule"),
                asyncio.create_task(self._catch_up(), name="catch-up"),
                asyncio.create_task(self._every(SPEND_REFRESH, self.engine.refresh_spend), name="spend"),
                asyncio.create_task(self._every(UPDATE_CHECK, self.check_update), name="updates"),
            ]

    async def stop(self) -> None:
        if self._followup:
            self._followup.cancel()
        for task in [*self._tasks, self._scan_task]:
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*[t for t in [*self._tasks, self._scan_task] if t], return_exceptions=True)

    def _notify(self) -> None:
        if self._on_change:
            self._on_change()

    @property
    def provider_ready(self) -> bool:
        keys = self.keys.get_all()
        if self.settings.provider == PROVIDER_OPENROUTER:
            return bool(keys.get("api_key"))
        if self.settings.provider == PROVIDER_GEMINI:
            return bool(keys.get("gemini_search_key"))
        return False

    def public_state(self, **extra: Any) -> dict[str, Any]:
        keys = self.keys.get_all()
        return self.engine.public_state(
            provider_ready=self.provider_ready,
            keys_set={k: bool(v) for k, v in keys.items()},
            update=self.update if self.update and updates.newer(VERSION, self.update["version"]) else None,
            app={
                "version": VERSION,
                "autostart": autostart.enabled(),
                "autostart_supported": autostart.supported(),
                "lan_feed": self.settings.lan_feed,
                "feed_base": f"http://{lan_address()}:{self.settings.feed_port}" if self.settings.lan_feed else "",
                "data_dir": str(self.data_dir),
                "key_store": self.keys.backend,
            },
            **extra,
        )

    def debug(self) -> dict[str, Any]:
        return debug_info(self.engine, app_settings={**self.settings.__dict__}, key_store=self.keys.backend, scanning=self.engine.scanning)

    # ---------------------------------------------------------------- provider

    async def set_provider(self, body: dict[str, Any]) -> dict[str, Any]:
        provider = body.get("provider")
        if provider not in (PROVIDER_OPENROUTER, PROVIDER_GEMINI):
            raise ValueError("Choose OpenRouter or Google Gemini.")
        keys = self.keys.get_all()
        for name in ("api_key", "gemini_search_key", "gemini_key"):
            if name in body and body[name] is not None:
                value = str(body[name]).strip()
                if value or name == "gemini_key":  # empty search/API key fields mean "keep the saved one"
                    keys[name] = value
        if provider == PROVIDER_OPENROUTER:
            if not keys["api_key"]:
                raise ValueError("Enter your OpenRouter API key.")
            try:
                await OpenRouterClient(self.session, keys["api_key"]).key_info()
            except OpenRouterAuthError as err:
                raise ValueError("OpenRouter rejected this key. Check that it was copied in full and is still active.") from err
            except OpenRouterError as err:
                raise ValueError(f"Couldn't reach OpenRouter to check the key: {err}") from err
        else:
            if not keys["gemini_search_key"]:
                raise ValueError("Enter the Gemini key from your project with billing turned on.")
            for name, label in (("gemini_search_key", "search key"), ("gemini_key", "free-tier key")):
                if keys[name] and (problem := await validate_gemini_key(self.session, keys[name])):
                    raise ValueError(
                        f"Google rejected the {label}." if problem == "invalid_auth" else "Couldn't reach Google to check the key."
                    )
        self.keys.set_all(keys)
        self.settings.provider = provider
        self.settings.save(self.data_dir)
        self.engine.set_provider(provider, keys)
        if body.get("model") and provider == PROVIDER_OPENROUTER:
            await self.engine.save_config({**self.engine.config, "model": str(body["model"])})
        if provider == PROVIDER_OPENROUTER:
            await self.engine.refresh_spend()
        self._notify()
        return self.public_state(message="AI provider saved.")

    async def set_app(self, body: dict[str, Any]) -> dict[str, Any]:
        if "autostart" in body:
            autostart.set_enabled(bool(body["autostart"]))
        if "lan_feed" in body:
            previous = self.settings.lan_feed
            self.settings.lan_feed = bool(body["lan_feed"])
            try:
                if self.on_lan_change:
                    await self.on_lan_change()
            except ValueError:
                self.settings.lan_feed = previous
                raise
            finally:
                self.settings.save(self.data_dir)
        return self.public_state()

    # ------------------------------------------------------------------- scans

    def request_scan(self, force: bool = True) -> dict[str, Any]:
        if not self.provider_ready:
            raise ValueError("Connect an AI provider first (Settings → AI provider).")
        self.engine.check_ready()
        self.start_scan(force)
        return self.public_state()

    def start_scan(self, force: bool, followup: bool = False) -> None:
        if not self.provider_ready:
            return
        job = self.engine.begin_scan(force, followup)
        if job is None:
            return
        self._scan_task = asyncio.create_task(job, name="scan")

        def _done(task: asyncio.Task) -> None:
            self.engine.scanning = False
            if not task.cancelled() and task.exception():
                _LOGGER.error("Scan failed", exc_info=task.exception())
            self._notify()

        self._scan_task.add_done_callback(_done)
        self._notify()

    def _retry_later(self) -> str | None:
        today = datetime.now(self.tz).date().isoformat()
        day, count = self._followups
        count = count if day == today else 0
        if count >= MAX_FOLLOWUPS_PER_DAY:
            return None
        self._followups = (today, count + 1)
        if self._followup:
            self._followup.cancel()
        loop = asyncio.get_running_loop()
        self._followup = loop.call_later(FOLLOWUP_DELAY.total_seconds(), lambda: self.start_scan(False, followup=True))
        return (datetime.now(self.tz) + FOLLOWUP_DELAY).strftime("%H:%M")

    def reschedule(self) -> None:
        self._wake.set()

    def _next_run(self, now: datetime) -> datetime:
        hour, minute = (int(v) for v in self.engine.config["schedule"].split(":"))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return target if target > now else target + timedelta(days=1)

    async def _scheduler(self) -> None:
        """Run the nightly search at the set time, checking the wall clock in short steps so sleep/hibernate can't skip it."""
        while True:
            target = self._next_run(datetime.now(self.tz))
            while True:
                remaining = (target - datetime.now(self.tz)).total_seconds()
                if remaining <= 0:
                    break
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=min(remaining, MAX_SLEEP))
                    target = self._next_run(datetime.now(self.tz))  # the time was changed
                except asyncio.TimeoutError:
                    pass
            if (datetime.now(self.tz) - target).total_seconds() > 180:
                await asyncio.sleep(120)  # the PC just woke up: give the network a moment to come back
            if self.engine.config["locations"] and self.engine.config["interests"]:
                _LOGGER.info("Nightly search")
                self.start_scan(force=False)
            await asyncio.sleep(61)

    async def _catch_up(self) -> None:
        """If the PC was off at search time, run the missed search shortly after starting."""
        await asyncio.sleep(CATCH_UP_DELAY)
        now = datetime.now(self.tz)
        hour, minute = (int(v) for v in self.engine.config["schedule"].split(":"))
        if now < now.replace(hour=hour, minute=minute, second=0, microsecond=0):
            return  # today's search is still to come
        cfg = self.engine.config
        if cfg["locations"] and cfg["interests"] and self.engine.due_buckets(force=False):
            _LOGGER.info("Catching up on a missed search")
            self.start_scan(force=False)

    async def _every(self, interval: timedelta, job: Callable[[], Any]) -> None:
        await asyncio.sleep(5)
        while True:
            try:
                await job()
            except Exception:  # noqa: BLE001 - a background job must never stop the app
                _LOGGER.exception("Background job failed")
            await asyncio.sleep(interval.total_seconds())

    async def check_update(self) -> dict[str, str] | None:
        latest = await updates.latest_release(self.session)
        if latest and latest.get("version"):
            self.update = latest
            self._notify()
        return self.update

    # ----------------------------------------------------------------- weather

    async def _weather(self, choice: str) -> str:
        if choice == "none":
            return ""
        locations = self.engine.config["locations"]
        if not locations or locations[0].get("lat") is None:
            return ""
        loc = locations[0]
        fahrenheit = (loc.get("country") or "").strip().lower() in ("usa", "us", "united states", "united states of america")
        return await forecast_summary(self.session, loc["lat"], loc["lon"], fahrenheit)
