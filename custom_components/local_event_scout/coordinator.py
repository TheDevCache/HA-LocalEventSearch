"""Home Assistant glue around the shared engine: storage, scheduling, weather and updates.

All of Event Scout's logic lives in ``scout.engine.ScoutEngine``; this module only
connects it to Home Assistant.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, __version__ as HA_VERSION
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_change, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_GEMINI_KEY,
    CONF_GEMINI_SEARCH_KEY,
    CONF_PROVIDER,
    DEFAULT_MODEL,
    DOMAIN,
    PROVIDER_OPENROUTER,
    SIGNAL_UPDATE,
)
from .scout.engine import ScoutEngine, debug_info, empty_results
from .scout.models import DEFAULT_BUCKET_ID

_LOGGER = logging.getLogger(__name__)

CONFIG_VERSION = 2
RESULTS_VERSION = 2
SPEND_REFRESH = timedelta(hours=6)
FOLLOWUP_DELAY = timedelta(minutes=30)  # try again after the AI service was busy
MAX_FOLLOWUPS_PER_DAY = 3
KEY_HINT = "Change it in Settings → Devices & services → Local Event Scout → ⋮ → Reconfigure."


class _ConfigStore(Store[dict[str, Any]]):
    async def _async_migrate_func(self, old_major, old_minor, old_data):
        if old_major < 2:
            old_data = dict(old_data)
            old_data["interests"] = [
                {"name": str(i), "bucket": DEFAULT_BUCKET_ID} if not isinstance(i, dict) else i
                for i in old_data.get("interests", [])
            ]
            old_data["locations"] = [
                {k: v for k, v in loc.items() if k != "radius_km"} for loc in old_data.get("locations", [])
            ]
        return old_data


class _ResultsStore(Store[dict[str, Any]]):
    async def _async_migrate_func(self, old_major, old_minor, old_data):
        # v1 results had no parsed dates or scores; start fresh.
        return empty_results() if old_major < 2 else old_data


class HAStorage:
    """The engine's storage, kept in Home Assistant's .storage folder."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._config = _ConfigStore(hass, CONFIG_VERSION, f"{DOMAIN}.{entry_id}.config")
        self._results = _ResultsStore(hass, RESULTS_VERSION, f"{DOMAIN}.{entry_id}.results")

    async def load_config(self) -> dict[str, Any] | None:
        return await self._config.async_load()

    async def save_config(self, data: dict[str, Any]) -> None:
        await self._config.async_save(data)

    async def load_results(self) -> dict[str, Any] | None:
        return await self._results.async_load()

    async def save_results(self, data: dict[str, Any]) -> None:
        await self._results.async_save(data)


class EventScoutCoordinator:
    """Runs one ScoutEngine inside Home Assistant."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        data = entry.data
        self.engine = ScoutEngine(
            async_get_clientsession(hass),
            HAStorage(hass, entry.entry_id),
            provider=data.get(CONF_PROVIDER, PROVIDER_OPENROUTER),
            keys={
                "api_key": data.get(CONF_API_KEY, ""),
                "gemini_search_key": data.get(CONF_GEMINI_SEARCH_KEY, ""),
                "gemini_key": data.get(CONF_GEMINI_KEY, ""),
            },
            tz=dt_util.get_default_time_zone(),
            tz_name=hass.config.time_zone,
            home=(hass.config.latitude, hass.config.longitude),
            place_name=hass.config.location_name,
            weather=self._weather_summary,
            notify=self._notify,
            retry_later=self._schedule_followup,
            key_hint=KEY_HINT,
            host="homeassistant",
            now=dt_util.now,
        )
        self._scan_task: asyncio.Task | None = None
        self._unsubs: list[Callable[[], None]] = []
        self._unsub_schedule: Callable[[], None] | None = None
        self._unsub_followup: Callable[[], None] | None = None
        self._followups: tuple[str, int] = ("", 0)  # (date, count) of automatic retries

    # Shortcuts used by the sensors and calendar.
    @property
    def provider(self) -> str:
        return self.engine.provider

    @property
    def results(self) -> dict[str, Any]:
        return self.engine.results

    @property
    def config(self) -> dict[str, Any]:
        return self.engine.config

    @property
    def progress(self) -> str:
        return self.engine.progress

    @property
    def status(self) -> str:
        return self.engine.status

    def picks(self) -> list[dict[str, Any]]:
        return self.engine.picks()

    def next_due(self) -> dict[str, str]:
        return self.engine.next_due()

    def month_cost(self) -> float:
        return self.engine.month_cost()

    def month_web_searches(self) -> int:
        return self.engine.month_web_searches()

    def public_state(self, **extra: Any) -> dict[str, Any]:
        return self.engine.public_state(weather_entities=sorted(self.hass.states.async_entity_ids("weather")), **extra)

    # ------------------------------------------------------------- lifecycle

    async def async_initialize(self) -> None:
        await self.engine.load(default_model=self.entry.data.get("model", DEFAULT_MODEL))
        self._schedule()
        if self.provider == PROVIDER_OPENROUTER:
            self._unsubs.append(async_track_time_interval(self.hass, self._refresh_spend_cb, SPEND_REFRESH))
            self.entry.async_create_background_task(self.hass, self.engine.refresh_spend(), f"{DOMAIN} spend")

    async def async_shutdown(self) -> None:
        for unsub in (self._unsub_schedule, self._unsub_followup, *self._unsubs):
            if unsub:
                unsub()
        self._unsub_schedule = self._unsub_followup = None
        self._unsubs.clear()
        if self._scan_task and not self._scan_task.done():
            self._scan_task.cancel()

    def _schedule(self) -> None:
        if self._unsub_schedule:
            self._unsub_schedule()
        hour, minute = (int(v) for v in self.config["schedule"].split(":"))

        @callback
        def _nightly(_now: datetime) -> None:
            if self.config["locations"] and self.config["interests"]:
                self._start_scan(force=False)

        self._unsub_schedule = async_track_time_change(self.hass, _nightly, hour=hour, minute=minute, second=0)

    @callback
    def _refresh_spend_cb(self, _now: datetime) -> None:
        self.entry.async_create_background_task(self.hass, self.engine.refresh_spend(), f"{DOMAIN} spend")

    @callback
    def _notify(self) -> None:
        async_dispatcher_send(self.hass, f"{SIGNAL_UPDATE}_{self.entry.entry_id}")

    # ---------------------------------------------------------------- actions

    async def async_save_config(self, incoming: dict[str, Any]) -> dict[str, Any]:
        await self.engine.save_config(incoming)
        self._schedule()
        return self.public_state()

    async def async_set_feedback(self, event_id: str, verdict: str) -> dict[str, Any]:
        await self.engine.set_feedback(event_id, verdict)
        return self.public_state()

    async def async_report(self, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        state = await self.engine.report(event_id, body)
        return self.public_state(message=state.get("message", ""))

    async def async_source_action(self, body: dict[str, Any]) -> dict[str, Any]:
        await self.engine.source_action(body)
        return self.public_state()

    def debug_info(self) -> dict[str, Any]:
        return debug_info(
            self.engine,
            home_assistant=HA_VERSION,
            weather_entities=len(self.hass.states.async_entity_ids("weather")),
            scanning=self.engine.scanning,
        )

    async def async_new_calendar_token(self) -> dict[str, Any]:
        await self.engine.new_calendar_token()
        return self.public_state()

    async def async_test_search(self) -> dict[str, Any]:
        return await self.engine.test_search()

    def async_request_scan(self, force: bool = True) -> dict[str, Any]:
        """Validate and start a scan in the background; returns immediately."""
        self.engine.check_ready()
        self._start_scan(force=force)
        return self.public_state()

    @callback
    def _start_scan(self, force: bool, followup: bool = False) -> None:
        job = self.engine.begin_scan(force, followup)
        if job is None:
            return  # already scanning
        self._scan_task = self.entry.async_create_background_task(self.hass, job, f"{DOMAIN} scan")

        @callback
        def _done(_task: asyncio.Task) -> None:
            self.engine.scanning = False  # also covers a task cancelled before it started
            self._notify()

        self._scan_task.add_done_callback(_done)
        self._notify()

    @callback
    def _schedule_followup(self) -> str | None:
        """Retry later when the AI service was busy. Returns the local time of the retry."""
        today = dt_util.now().date().isoformat()
        day, count = self._followups
        count = count if day == today else 0
        if count >= MAX_FOLLOWUPS_PER_DAY:
            return None
        self._followups = (today, count + 1)
        if self._unsub_followup:
            self._unsub_followup()

        @callback
        def _retry(_now: datetime) -> None:
            self._unsub_followup = None
            self._start_scan(force=False, followup=True)

        self._unsub_followup = async_call_later(self.hass, FOLLOWUP_DELAY, _retry)
        return (dt_util.now() + FOLLOWUP_DELAY).strftime("%H:%M")

    # ---------------------------------------------------------------- weather

    async def _weather_summary(self, choice: str) -> str:
        if choice == "none":
            return ""
        entity_id = choice or next(iter(sorted(self.hass.states.async_entity_ids("weather"))), None)
        if not entity_id or not self.hass.services.has_service("weather", "get_forecasts"):
            return ""
        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": entity_id, "type": "daily"},
                blocking=True,
                return_response=True,
            )
        except Exception as err:  # noqa: BLE001 - weather is optional
            _LOGGER.debug("Weather forecast unavailable from %s: %s", entity_id, err)
            return ""
        forecast = (response or {}).get(entity_id, {}).get("forecast", [])
        unit = self.hass.config.units.temperature_unit
        lines = []
        for day in forecast[:10]:
            when = dt_util.parse_datetime(str(day.get("datetime", "")))
            if not when:
                continue
            parts = [str(day.get("condition", "unknown"))]
            if day.get("temperature") is not None:
                parts.append(f"high {day['temperature']}{unit}")
            if day.get("templow") is not None:
                parts.append(f"low {day['templow']}{unit}")
            if day.get("precipitation_probability") is not None:
                parts.append(f"{day['precipitation_probability']}% chance of precipitation")
            lines.append(f"{dt_util.as_local(when):%a %Y-%m-%d}: " + ", ".join(parts))
        return "\n".join(lines)
