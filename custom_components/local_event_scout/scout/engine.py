"""Everything Event Scout does, independent of where it runs.

``ScoutEngine`` owns the settings and results, runs scans, and handles feedback,
reports, saved sources and spending. A host (the Home Assistant integration,
the Windows app, the CLI) supplies storage, an HTTP session, the API keys,
the time zone, and optionally a weather summary; it also decides *when* scans
run. Nothing in here imports Home Assistant.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime, timedelta, timezone, tzinfo
import logging
from typing import Any
from uuid import uuid4

import aiohttp

from . import reports as report_lib
from .dates import in_window, parse_event_time
from .defaults import (
    BUDGET_FLOOR,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GEMINI_SEARCH_MODEL,
    DEFAULT_MODEL,
    GEMINI_FREE_SEARCHES,
    PROVIDER_GEMINI,
    PROVIDER_OPENROUTER,
    SEARCH_ENGINES,
)
from .errors import DEFAULT_KEY_HINT, explain
from .geo import Geocoder
from .models import DEFAULT_BUCKET_ID, DEFAULT_BUCKETS, PRIORITIES, Bucket, Interest, Location, Profile, Source, to_dict
from .openrouter import OpenRouterClient, OpenRouterError
from .pipeline import ScoutPipeline, find_duplicate
from .providers import GeminiProvider, OpenRouterProvider
from .sources import host_of, normalize_url
from .storage import Storage
from .textutil import clean_str

_LOGGER = logging.getLogger(__name__)

VERSION = "0.4.0"

MAX_FEEDBACK = 100
MAX_REPORTS = 200
MAX_RUN_HISTORY = 60
MAX_SOURCES = 40
MAX_AUTO_SOURCES = 30
MAX_SOURCES_PER_RUN = 12
HEALTHY_MISSES = 3  # a source with fewer consecutive empty checks counts as working
AUTO_PAUSE_MISSES = 4  # auto-added sources are paused after this many empty checks
MIN_HEALTHY_SOURCES = 2  # groups with this many working sources only web-search every discovery_days
SOURCE_REPORT_PAUSE = 3  # auto-added sources are paused after this many events from them were reported wrong

DEFAULT_CONFIG: dict[str, Any] = {
    "locations": [],
    "interests": [],
    "dislikes": [],
    "buckets": [to_dict(b) for b in DEFAULT_BUCKETS],
    "schedule": "02:30",
    "model": DEFAULT_MODEL,
    "search_engine": "auto",
    "gemini_model": DEFAULT_GEMINI_MODEL,
    "gemini_search_model": DEFAULT_GEMINI_SEARCH_MODEL,
    "max_results": 8,
    "min_score": 5,
    "weather_entity": "",  # Home Assistant: "" = first weather entity, "none" = off
    "check_links": True,
    "auto_sources": True,
    "discovery_days": 7,
    # OpenRouter: web searches the model may make in its one call per group.
    # Gemini: separate Google-search calls per group, each on different interests.
    "searches_per_group": 2,
    "interests_per_search": 3,  # interests covered by each search; the rest rotate in later
}


def empty_results() -> dict[str, Any]:
    return {
        "events": {},
        "feedback": [],
        "last_run": None,
        "run_history": [],
        "bucket_scanned": {},
        "bucket_searched": {},
        "interest_searched": {},
        "sources": [],
        "reports": [],  # what users said was wrong; outlives the events (see reports.py)
        "geocode_cache": {},
        "gemini_search_api": "auto",  # which Gemini request style actually searches with this key
        "spend": None,
    }


class ScoutEngine:
    """Settings, results and scans for one Event Scout setup."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        storage: Storage,
        *,
        provider: str,
        keys: dict[str, str],
        tz: tzinfo,
        tz_name: str,
        home: tuple[float, float] | None = None,
        place_name: str = "",
        weather: Callable[[str], Awaitable[str]] | None = None,
        notify: Callable[[], None] | None = None,
        retry_later: Callable[[], str | None] | None = None,
        key_hint: str = DEFAULT_KEY_HINT,
        host: str = "app",
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """
        ``keys``: ``api_key`` for OpenRouter; ``gemini_search_key`` and optional ``gemini_key`` for Gemini.
        ``home``: coordinates used when the first location can't be found on the map.
        ``weather(choice)``: returns a short forecast text for the ranking prompt ("" for none).
        ``retry_later()``: schedules a follow-up scan after an overload and returns its local time.
        """
        self.session = session
        self.storage = storage
        self.provider = provider
        self._keys = keys
        self.tz = tz
        self.tz_name = tz_name
        self.home = home
        self.place_name = place_name
        self._weather = weather
        self._notify_cb = notify
        self._retry_later = retry_later
        self.key_hint = key_hint
        self.host = host
        self._now = now or (lambda: datetime.now(self.tz))
        self.config: dict[str, Any] = dict(DEFAULT_CONFIG)
        self.results: dict[str, Any] = empty_results()
        self.progress = ""
        self.scanning = False

    # ------------------------------------------------------------- lifecycle

    async def load(self, *, default_model: str | None = None) -> None:
        stored = await self.storage.load_config() or {}
        self.config = self.validate_config({**DEFAULT_CONFIG, "model": default_model or DEFAULT_MODEL, **stored})
        self.results = {**empty_results(), **(await self.storage.load_results() or {})}
        for source in self.results["sources"]:
            if source.get("origin") != "manual":
                source["name"] = host_of(source["url"])  # older versions named auto sources after a venue
        self.prune()

    def now(self) -> datetime:
        return self._now()

    def utcnow_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def notify(self) -> None:
        if self._notify_cb:
            self._notify_cb()

    async def save_results(self) -> None:
        await self.storage.save_results(self.results)

    def explain(self, err: Exception) -> str:
        model = self.config["gemini_search_model"] if self.provider == PROVIDER_GEMINI else self.config["model"]
        return explain(err, provider=self.provider, model=model, key_hint=self.key_hint)

    # --------------------------------------------------------------- settings

    def validate_config(self, incoming: dict[str, Any]) -> dict[str, Any]:
        old_locations = {loc["id"]: loc for loc in self.config.get("locations", []) if isinstance(loc, dict) and "id" in loc}
        locations = []
        for raw in incoming.get("locations") or []:
            if not isinstance(raw, dict):
                continue
            city = str(raw.get("city", "")).strip()
            if not city:
                continue
            loc = {
                "id": str(raw.get("id") or uuid4().hex),
                "city": city[:80],
                "region": str(raw.get("region", "")).strip()[:80],
                "country": str(raw.get("country", "")).strip()[:80],
                "lat": raw.get("lat"),
                "lon": raw.get("lon"),
            }
            previous = old_locations.get(loc["id"])
            if previous and any(previous.get(k) != loc[k] for k in ("city", "region", "country")):
                loc["lat"] = loc["lon"] = None  # place changed; geocode again
            if not isinstance(loc["lat"], (int, float)) or not isinstance(loc["lon"], (int, float)):
                loc["lat"] = loc["lon"] = None
            locations.append(loc)

        bucket_ids = {b.id for b in DEFAULT_BUCKETS}
        interests, seen = [], set()
        for raw in incoming.get("interests") or []:
            name = str(raw.get("name", "") if isinstance(raw, dict) else raw).strip()[:120]
            bucket = raw.get("bucket") if isinstance(raw, dict) else DEFAULT_BUCKET_ID
            priority = raw.get("priority") if isinstance(raw, dict) else "normal"
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            interests.append(
                {
                    "name": name,
                    "bucket": bucket if bucket in bucket_ids else DEFAULT_BUCKET_ID,
                    "priority": priority if priority in PRIORITIES else "normal",
                }
            )

        raw_buckets = {b.get("id"): b for b in incoming.get("buckets") or [] if isinstance(b, dict)}
        buckets = [to_dict(Bucket.from_dict(raw_buckets.get(d.id, {}), d)) for d in DEFAULT_BUCKETS]

        schedule = str(incoming.get("schedule") or "02:30")[:5]
        try:
            datetime.strptime(schedule, "%H:%M")
        except ValueError as err:
            raise ValueError("Nightly scan time must use 24-hour HH:MM format.") from err

        def _int(key: str, lo: int, hi: int) -> int:
            try:
                return max(lo, min(hi, int(incoming.get(key, DEFAULT_CONFIG[key]))))
            except (TypeError, ValueError):
                return DEFAULT_CONFIG[key]

        def _str(key: str) -> str:
            return str(incoming.get(key) or DEFAULT_CONFIG[key]).strip()[:100]

        engine = str(incoming.get("search_engine") or "auto").strip().lower()
        return {
            "locations": locations[:5],
            "interests": interests[:100],
            "dislikes": clean_words(incoming.get("dislikes", [])),
            "buckets": buckets,
            "schedule": schedule,
            "model": _str("model"),
            "search_engine": engine if engine in SEARCH_ENGINES else "auto",
            "gemini_model": _str("gemini_model"),
            "gemini_search_model": _str("gemini_search_model"),
            "max_results": _int("max_results", 1, 20),
            "min_score": _int("min_score", 0, 10),
            "weather_entity": str(incoming.get("weather_entity") or "").strip(),
            "check_links": bool(incoming.get("check_links", True)),
            "auto_sources": bool(incoming.get("auto_sources", True)),
            "discovery_days": _int("discovery_days", 1, 60),
            "searches_per_group": _int("searches_per_group", 1, 10),
            "interests_per_search": _int("interests_per_search", 1, 20),
        }

    async def save_config(self, incoming: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(incoming, dict):
            raise ValueError("Settings must be a JSON object.")
        self.config = self.validate_config(incoming)
        await self.storage.save_config(self.config)
        self.notify()
        return self.public_state()

    def profile(self) -> Profile:
        cfg = self.config
        return Profile(
            locations=[Location(**loc) for loc in cfg["locations"]],
            interests=[Interest(**i) for i in cfg["interests"]],
            dislikes=list(cfg["dislikes"]),
            buckets=[Bucket(**b) for b in cfg["buckets"]],
            min_score=cfg["min_score"],
            max_results=cfg["max_results"],
        )

    # ------------------------------------------------------------------ state

    def upcoming_events(self) -> list[dict[str, Any]]:
        now = self.now()
        events = []
        for event in self.results["events"].values():
            event_time = parse_event_time(event["start"], event.get("end"), self.tz)
            if event_time and in_window(event_time, now, 3650):
                events.append(event)
        return sorted(events, key=lambda e: e["start"])

    def picks(self) -> list[dict[str, Any]]:
        min_score = self.config["min_score"]
        picks = [
            e
            for e in self.upcoming_events()
            if e.get("status") not in ("dislike", "dismiss") and (e.get("score") is None or e["score"] >= min_score)
        ]
        return sorted(picks, key=lambda e: (-(e.get("score") or 0), e["start"]))

    @property
    def status(self) -> str:
        if self.scanning:
            return "scanning"
        last = self.results.get("last_run")
        if not last:
            return "never"
        return last.get("status", "ok")

    def public_state(self, **extra: Any) -> dict[str, Any]:
        return {
            "version": VERSION,
            "host": self.host,
            "provider": self.provider,
            "config": self.config,
            "events": self.upcoming_events(),
            "sources": self.results["sources"],
            "reports": self.results["reports"][-30:],
            "problems": report_lib.PROBLEMS,
            "last_run": self.results.get("last_run"),
            "status": self.status,
            "scanning": self.scanning,
            "progress": self.progress,
            "spend": self.results.get("spend"),
            "month_cost": self.month_cost(),
            "month_web_searches": self.month_web_searches(),
            "free_searches": GEMINI_FREE_SEARCHES if self.provider == PROVIDER_GEMINI else None,
            "next_due": self.next_due(),
        } | extra

    def _this_month(self) -> list[dict[str, Any]]:
        month = self.now().strftime("%Y-%m")
        return [r for r in self.results["run_history"] if r.get("started", "").startswith(month)]

    def month_cost(self) -> float:
        return round(sum(r.get("cost", 0) for r in self._this_month()), 4)

    def month_web_searches(self) -> int:
        return sum(r.get("web_searches") or 0 for r in self._this_month())

    def next_due(self) -> dict[str, str]:
        today = self.now().date()
        used = {i["bucket"] for i in self.config["interests"]}
        due = {}
        for bucket in self.config["buckets"]:
            if bucket["id"] not in used:
                continue
            last = self.results["bucket_scanned"].get(bucket["id"])
            if not last:
                due[bucket["id"]] = today.isoformat()
                continue
            next_day = datetime.fromisoformat(last).date() + timedelta(days=bucket["refresh_days"])
            due[bucket["id"]] = max(next_day, today).isoformat()
        return due

    # --------------------------------------------------------------- feedback

    async def set_feedback(self, event_id: str, verdict: str) -> dict[str, Any]:
        event = self.results["events"].get(event_id)
        if event is None:
            raise KeyError(event_id)
        event["status"] = "new" if verdict == "clear" else verdict
        if verdict == "clear":
            event.pop("hidden_reason", None)
        log = [f for f in self.results["feedback"] if f.get("id") != event_id]
        if verdict in ("like", "dislike"):
            log.append(
                {
                    "id": event_id,
                    "title": event["title"],
                    "label": event.get("category") or event.get("interest") or "",
                    "verdict": verdict,
                    "at": self.utcnow_iso(),
                }
            )
        self.results["feedback"] = log[-MAX_FEEDBACK:]
        if verdict == "like" and self.config["auto_sources"] and event.get("listing_url"):
            self.add_source(event["listing_url"], event["bucket"], event.get("venue") or "", "liked")
        await self.save_results()
        self.notify()
        return self.public_state()

    def feedback_examples(self, verdict: str) -> list[str]:
        return [
            f"{f['title']} ({f['label']})" if f.get("label") else f["title"]
            for f in self.results["feedback"]
            if f.get("verdict") == verdict
        ]

    # ---------------------------------------------------------------- reports

    async def report(self, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Apply a user's report that an event's details are wrong (or undo it)."""
        event = self.results["events"].get(event_id)
        if event is None:
            raise KeyError(event_id)
        log: list[dict[str, Any]] = self.results["reports"]
        source = self.find_source(event["source_url"]) if event.get("source_url") else None
        if body.get("undo"):
            had_reports = bool(event.get("reports"))
            report_lib.undo_report(event)
            self.results["reports"] = [r for r in log if r["event_id"] != event_id]
            if source and had_reports:
                source["reports"] = max(0, source.get("reports", 0) - 1)
            message = "Report undone."
        else:
            problem = str(body.get("problem") or "")
            note = clean_str(body.get("note"), 300)
            correction = body.get("correction") if isinstance(body.get("correction"), dict) else {}
            when = self.utcnow_iso()
            changed = report_lib.apply_report(event, problem, correction, note, self.tz, when)
            notes = []
            if problem == "location" and {"town", "venue", "address"} & set(changed):
                profile = self.profile()
                geocoder = Geocoder(self.session, self.results["geocode_cache"])
                notes.append(await report_lib.relocate(event, geocoder, profile.locations, profile.bucket(event["bucket"])))
            log.append(report_lib.log_entry(event, problem, changed, note, when))
            self.results["reports"] = log[-MAX_REPORTS:]
            if source and len(event["reports"]) == 1:
                source["reports"] = source.get("reports", 0) + 1
                if source["origin"] != "manual" and source["reports"] >= SOURCE_REPORT_PAUSE and source["enabled"]:
                    source["enabled"] = False
                    source["paused_reason"] = f"{source['reports']} events from this page were reported wrong"
                    notes.append(f"Paused the saved source {source['name']}: {source['paused_reason']}.")
            if event.get("hidden_reason") and event.get("status") == "dismiss" and problem != "location":
                notes.append("It's hidden now; you'll find it under Hidden.")
            message = " ".join(["Thanks, report saved.", *notes])
        await self.save_results()
        self.notify()
        return self.public_state() | {"message": message}

    # ---------------------------------------------------------------- sources

    def find_source(self, url: str) -> dict[str, Any] | None:
        key = normalize_url(url)
        return next((s for s in self.results["sources"] if normalize_url(s["url"]) == key), None)

    def add_source(self, url: str, bucket: str, name: str, origin: str) -> dict[str, Any] | None:
        url = url.strip()
        if not normalize_url(url) or self.find_source(url):
            return None
        sources = self.results["sources"]
        if len(sources) >= MAX_SOURCES:
            return None
        if origin not in ("manual", "discovered") and sum(1 for s in sources if s["origin"] not in ("manual", "discovered")) >= MAX_AUTO_SOURCES:
            return None
        source = {
            "url": url,
            "name": (name or host_of(url))[:80],
            "bucket": bucket if bucket in {b.id for b in DEFAULT_BUCKETS} else DEFAULT_BUCKET_ID,
            "origin": origin,  # manual | discovered | auto | liked
            "enabled": True,
            "added": self.utcnow_iso(),
            "last_checked": None,
            "last_found": 0,
            "found_total": 0,
            "misses": 0,
            "last_error": None,
            "paused_reason": None,
        }
        sources.append(source)
        return source

    async def source_action(self, body: dict[str, Any]) -> dict[str, Any]:
        action = body.get("action")
        url = str(body.get("url") or "").strip()
        if action == "add":
            if not normalize_url(url):
                raise ValueError("Enter a full web address starting with http:// or https://.")
            if self.find_source(url):
                raise ValueError("That page is already saved.")
            origin = "discovered" if body.get("origin") == "discovered" else "manual"
            if not self.add_source(url, str(body.get("bucket") or DEFAULT_BUCKET_ID), str(body.get("name") or ""), origin):
                raise ValueError(f"You can save up to {MAX_SOURCES} sources.")
        else:
            source = self.find_source(url)
            if source is None:
                raise KeyError(url)
            if action == "remove":
                self.results["sources"].remove(source)
            elif action == "update":
                if "bucket" in body and body["bucket"] in {b.id for b in DEFAULT_BUCKETS}:
                    source["bucket"] = body["bucket"]
                if "enabled" in body:
                    source["enabled"] = bool(body["enabled"])
                    if source["enabled"]:
                        source["misses"] = 0
                        source["paused_reason"] = None
                if body.get("name"):
                    source["name"] = str(body["name"])[:80]
            else:
                raise ValueError("Unknown source action.")
        await self.save_results()
        self.notify()
        return self.public_state()

    def sources_to_read(self, bucket_ids: list[str]) -> list[Source]:
        candidates = [s for s in self.results["sources"] if s["enabled"] and s["bucket"] in bucket_ids]
        candidates.sort(key=lambda s: s.get("last_checked") or "")  # least recently checked first
        return [Source(s["url"], s["bucket"], s["name"]) for s in candidates[:MAX_SOURCES_PER_RUN]]

    def healthy_sources(self, bucket_id: str) -> int:
        return sum(
            1 for s in self.results["sources"] if s["enabled"] and s["bucket"] == bucket_id and s["misses"] < HEALTHY_MISSES
        )

    def _update_source_stats(self, stats: dict[str, dict[str, Any]], when: str) -> None:
        for url, stat in stats.items():
            source = self.find_source(url)
            if source is None or stat.get("skipped"):
                continue  # a failed AI call says nothing about the page itself
            source["last_checked"] = when
            source["last_found"] = stat["found"]
            source["found_total"] += stat["found"]
            source["last_error"] = stat["error"]
            source["misses"] = 0 if stat["found"] else source["misses"] + 1
            if source["origin"] in ("auto", "liked") and source["misses"] >= AUTO_PAUSE_MISSES:
                source["enabled"] = False
                source["paused_reason"] = stat["error"] or f"No matching events in the last {AUTO_PAUSE_MISSES} checks"

    # ------------------------------------------------------------------ spend

    async def refresh_spend(self) -> None:
        if self.provider != PROVIDER_OPENROUTER:
            return
        client = OpenRouterClient(self.session, self._keys.get("api_key", ""))
        try:
            info = await client.key_info()
        except OpenRouterError as err:
            _LOGGER.debug("Could not read OpenRouter key usage: %s", err)
            return
        self.results["spend"] = {
            key: info.get(key)
            for key in ("usage", "usage_daily", "usage_weekly", "usage_monthly", "limit", "limit_remaining", "limit_reset")
        } | {"fetched_at": self.utcnow_iso()}
        self.notify()

    def llm(self):
        if self.provider == PROVIDER_GEMINI:
            return GeminiProvider(
                self.session,
                search_key=self._keys.get("gemini_search_key", ""),
                process_key=self._keys.get("gemini_key") or None,
                search_model=self.config["gemini_search_model"],
                process_model=self.config["gemini_model"],
                search_api=self.results.get("gemini_search_api", "auto"),
            )
        return OpenRouterProvider(
            OpenRouterClient(self.session, self._keys.get("api_key", "")),
            self.config["model"],
            self.config["search_engine"],
            max_searches=self.config["searches_per_group"],
        )

    # ------------------------------------------------------------- test search

    def place_text(self) -> str:
        place = self.config["locations"][0] if self.config["locations"] else None
        if place:
            return ", ".join(p for p in (place.get("city"), place.get("region"), place.get("country")) if p)
        return self.place_name or "the user's area"

    async def test_search(self) -> dict[str, Any]:
        """One tiny search with the configured provider, to check that web search really works."""
        where = self.place_text()
        llm = self.llm()
        if isinstance(llm, GeminiProvider):
            report = await llm.test_search(where)
            for item in report.values():
                if item.get("error"):
                    item["error"] = explain(
                        OpenRouterError(item["error"]), provider=self.provider, model=llm.search_model, key_hint=self.key_hint
                    )
            working = next((api for api in GeminiProvider.SEARCH_APIS if report.get(api, {}).get("searched")), None)
            if working:
                self.results["gemini_search_api"] = working
                await self.save_results()
            return {"provider": PROVIDER_GEMINI, "model": llm.search_model, "results": report, "working": working}
        try:
            done = await llm.complete(
                "Answer briefly.",
                f"Name two public events happening in or near {where} in the next two weeks, with a link for each.",
                web_search=True,
                reasoning="low",
            )
        except OpenRouterError as err:
            return {
                "provider": PROVIDER_OPENROUTER,
                "model": llm.model,
                "results": {"openrouter": {"searched": False, "error": self.explain(err)}},
                "working": None,
            }
        searches = done.usage.get("web_searches")
        searched = bool(searches) or (done.usage.get("input_tokens") or 0) > 2000
        return {
            "provider": PROVIDER_OPENROUTER,
            "model": llm.model,
            "results": {
                "openrouter": {
                    # OpenRouter does not always report the count; a large input means results were read.
                    "searched": searched,
                    "searches": searches,
                    "cost": done.cost,
                    "answer": done.content[:300],
                    "error": None,
                }
            },
            "working": "openrouter" if searched else None,
        }

    # ------------------------------------------------------------------- scan

    def check_ready(self) -> None:
        if not self.config["locations"]:
            raise ValueError("Add at least one location before searching.")
        if not self.config["interests"]:
            raise ValueError("Add at least one interest before searching.")

    def begin_scan(self, force: bool, followup: bool = False) -> Coroutine[Any, Any, None] | None:
        """Mark a scan as started and return the coroutine that runs it (None if one is running)."""
        if self.scanning:
            return None
        self.scanning = True
        return self._scan(force, followup)

    def due_buckets(self, force: bool) -> list[str]:
        """Buckets that have interests and are due (or all of them when forced)."""
        used = {i["bucket"] for i in self.config["interests"]}
        today = self.now().date().isoformat()
        due = self.next_due()
        return [b["id"] for b in self.config["buckets"] if b["id"] in used and (force or due[b["id"]] <= today)]

    def search_buckets(self, bucket_ids: list[str], force: bool, now: datetime) -> list[str]:
        """Buckets to web-search: all when forced; otherwise skip groups with enough working sources."""
        if force:
            return list(bucket_ids)
        chosen = []
        for bucket_id in bucket_ids:
            last = self.results["bucket_searched"].get(bucket_id)
            recent = last and now - datetime.fromisoformat(last) < timedelta(days=self.config["discovery_days"], hours=-2)
            if self.healthy_sources(bucket_id) < MIN_HEALTHY_SOURCES or not recent:
                chosen.append(bucket_id)
        return chosen

    def unscored(self) -> list[dict[str, Any]]:
        return [e for e in self.upcoming_events() if e.get("score") is None and e.get("status") not in ("dislike", "dismiss")]

    def known_events(self) -> dict[str, list[str]]:
        """Upcoming events per group, so searches spend their effort on new ones."""
        known: dict[str, list[str]] = {}
        for event in self.upcoming_events():
            known.setdefault(event["bucket"], []).append(f"{event['title']} ({event['start'][:10]})")
        return known

    async def _scan(self, force: bool, followup: bool = False) -> None:
        try:
            bucket_ids = self.due_buckets(force)
            if not bucket_ids and not (followup and self.unscored()):
                _LOGGER.debug("Scheduled check: no distance group is due")
                return
            await self._run_scan(bucket_ids, force, followup)
        finally:
            self.scanning = False
            self.progress = ""
            self.notify()

    async def _run_scan(self, bucket_ids: list[str], force: bool, followup: bool) -> None:
        started = self.now()
        run: dict[str, Any] = {"started": started.isoformat(), "forced": force, "followup": followup, "provider": self.provider}
        self.progress = "Starting…"
        self.notify()
        try:
            search_ids = self.search_buckets(bucket_ids, force, started)
            notes = []
            if self.provider == PROVIDER_OPENROUTER:
                await self.refresh_spend()
                remaining = (self.results.get("spend") or {}).get("limit_remaining")
                if isinstance(remaining, (int, float)) and remaining < BUDGET_FLOOR:
                    run.update(
                        status="budget",
                        error=f"OpenRouter limit nearly used up (${remaining:.2f} left); scan skipped. "
                        "Raise the key's limit at openrouter.ai/settings/keys, or wait for it to reset.",
                    )
                    return
            elif search_ids and self.month_web_searches() >= GEMINI_FREE_SEARCHES - 50:
                search_ids = []
                notes.append("Monthly free Google searches nearly used up; only saved sources were read.")

            weather = await self._weather(self.config["weather_entity"]) if self._weather else ""
            llm = self.llm()
            pipeline = ScoutPipeline(
                self.session,
                llm,
                tz=self.tz,
                tz_name=self.tz_name,
                geocode_cache=self.results["geocode_cache"],
                check_links=self.config["check_links"],
                interests_per_search=self.config["interests_per_search"],
                search_calls=self.config["searches_per_group"] if self.provider == PROVIDER_GEMINI else 1,
                explain=self.explain,
            )
            profile = self.profile()
            # If a location cannot be geocoded, fall back to the host's home coordinates.
            await pipeline.locate_locations(profile.locations)
            if profile.locations and profile.locations[0].lat is None and self.home:
                profile.locations[0].lat, profile.locations[0].lon = self.home

            def _progress(message: str) -> None:
                self.progress = message
                self.notify()

            result = await pipeline.run(
                profile,
                now=started,
                bucket_ids=bucket_ids,
                search_bucket_ids=search_ids,
                sources=self.sources_to_read(bucket_ids),
                liked=self.feedback_examples("like"),
                disliked=self.feedback_examples("dislike"),
                weather=weather,
                known_events=self.known_events(),
                interest_last_searched=self.results["interest_searched"],
                unscored=self.unscored(),
                corrections=report_lib.hints(self.results["reports"]),
                progress=_progress,
            )
            if isinstance(llm, GeminiProvider) and llm.search_api in GeminiProvider.SEARCH_APIS:
                self.results["gemini_search_api"] = llm.search_api
            new_count = self.merge_events(result.events)
            await self._remember_coordinates(profile.locations)
            stamp = started.isoformat()
            # Groups whose search failed stay due, so the retry (or tomorrow's run) covers them.
            for bucket_id in result.buckets_scanned:
                if bucket_id not in result.failed_buckets:
                    self.results["bucket_scanned"][bucket_id] = stamp
            for bucket_id in result.buckets_searched:
                if bucket_id not in result.failed_buckets:
                    self.results["bucket_searched"][bucket_id] = stamp
            for name in result.interests_searched:
                self.results["interest_searched"][name] = stamp
            self._update_source_stats(result.source_stats, stamp)
            added = []
            if self.config["auto_sources"]:
                for suggestion in result.suggested_sources:
                    if self.add_source(suggestion["url"], suggestion["bucket"], suggestion["name"], "auto"):
                        added.append(suggestion["url"])
            if added:
                notes.append(f"Saved {len(added)} new source{'s' if len(added) > 1 else ''}.")
            if result.rescored:
                notes.append(f"Scored {result.rescored} event{'s' if result.rescored > 1 else ''} left unscored earlier.")
            if result.transient_failure:
                retry_at = self._retry_later() if self._retry_later else None
                notes.append(
                    f"The AI service was busy; trying the failed parts again at {retry_at}."
                    if retry_at
                    else "The AI service was busy; the failed parts will be retried on the next run."
                )
            run.update(
                status="error" if result.errors and not result.events else "ok",
                error="; ".join(result.errors[:3]),
                note=" ".join(notes),
                searches=result.searches,
                web_searches=result.web_searches,
                pages_read=result.pages_read,
                cost=round(result.cost, 5),
                candidates=result.candidates,
                kept=len(result.events),
                new=new_count,
                dropped=result.dropped,
                buckets=result.buckets_scanned,
                buckets_searched=result.buckets_searched,
                interests_searched=result.interests_searched,
                calls=result.calls,
                sources_added=added,
            )
        except asyncio.CancelledError:
            run.update(status="error", error="Scan was cancelled.")
            raise
        except OpenRouterError as err:
            run.update(status="error", error=self.explain(err))
        except Exception as err:  # noqa: BLE001 - keep the host alive and show the problem
            _LOGGER.exception("Event Scout scan failed")
            run.update(status="error", error=f"Unexpected error: {err}. Use “Copy debug info” in Settings to report it.")
        finally:
            run["finished"] = self.now().isoformat()
            run.setdefault("cost", 0)
            self.results["last_run"] = run
            history_entry = {k: v for k, v in run.items() if k != "calls"}
            self.results["run_history"] = (self.results["run_history"] + [history_entry])[-MAX_RUN_HISTORY:]
            self.prune()
            await self.save_results()
            if run.get("searches") or run.get("pages_read"):
                await self.refresh_spend()

    def merge_events(self, events: list[dict[str, Any]]) -> int:
        """Add new events and refresh known ones. Nothing is removed here (see prune)."""
        stored: dict[str, dict[str, Any]] = self.results["events"]
        ghosts = report_lib.ghost_events(self.results["reports"])
        now = self.utcnow_iso()
        new = 0
        for event in events:
            existing = stored.get(event["id"]) or find_duplicate(stored, event)
            if existing is None:
                # Seen again with the details the user reported as wrong?
                ghost = find_duplicate(ghosts, event) if ghosts else None
                if ghost:
                    existing = stored.get(ghost["id"])
                    if existing is None:
                        continue  # reported (e.g. cancelled) and since expired: don't bring it back
            if existing:
                keep = {k: existing[k] for k in ("id", "status", "first_seen") if k in existing}
                if event.get("score") is None and existing.get("score") is not None:
                    keep.update(score=existing["score"], why=existing.get("why", ""))
                if existing.get("reports"):
                    event = report_lib.reapply(event, existing)
                existing.clear()
                existing.update(event | keep | {"last_seen": now})
            else:
                stored[event["id"]] = event | {"status": "new", "first_seen": now, "last_seen": now}
                new += 1
        return new

    async def _remember_coordinates(self, locations: list[Location]) -> None:
        changed = False
        for loc, saved in zip(locations, self.config["locations"]):
            if saved.get("lat") is None and loc.lat is not None:
                saved["lat"], saved["lon"] = loc.lat, loc.lon
                changed = True
        if changed:
            await self.storage.save_config(self.config)

    def prune(self) -> None:
        """Remove events a day after they have finished, and stale report log entries."""
        now = self.now()
        keep = {}
        for event_id, event in self.results["events"].items():
            event_time = parse_event_time(event.get("start", ""), event.get("end"), self.tz)
            if event_time and event_time.last_date >= now.date() - timedelta(days=1):
                keep[event_id] = event
        self.results["events"] = keep
        self.results["reports"] = [r for r in self.results["reports"] if not report_lib.expired(r, now)]
        if len(self.results["geocode_cache"]) > 2000:
            self.results["geocode_cache"] = dict(list(self.results["geocode_cache"].items())[-1000:])


def clean_words(values: Any) -> list[str]:
    if not isinstance(values, list):
        values = str(values or "").replace("\n", ",").split(",")
    return list(dict.fromkeys(str(v).strip()[:120] for v in values if str(v).strip()))[:100]
