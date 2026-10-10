"""The shared engine, run the way the Home Assistant and Windows hosts run it."""

import aiohttp
import pytest

from scout.engine import ScoutEngine
from scout.openrouter import OpenRouterAuthError, OpenRouterError
from scout.storage import JsonFileStorage, MemoryStorage

from test_pipeline import NOW, TZ, FakeLLM, ev

GEOCACHE = {
    "somewhere, riverton, westshire, exampleland": [45.05, -100.20],
    "somewhere, millbrook, westshire, exampleland": [45.13, -100.33],
    "somewhere, stoneford, westshire, exampleland": None,
    "stoneford, westshire, exampleland": [42.54, -100.77],  # ~280 km
}
CONFIG = {
    "locations": [{"id": "home", "city": "Riverton", "region": "Westshire", "country": "Exampleland", "lat": 45.0461, "lon": -100.1938}],
    "interests": [
        {"name": "trivia nights", "bucket": "nearby", "priority": "high"},
        {"name": "indie concerts", "bucket": "local", "priority": "normal"},
        {"name": "jazz", "bucket": "local", "priority": "normal"},
        {"name": "comedy", "bucket": "local", "priority": "normal"},
        {"name": "theatre", "bucket": "local", "priority": "normal"},
    ],
    "check_links": False,
    "interests_per_search": 2,
}


async def make_engine(session, llm, *, provider="gemini", config=None, results=None, retry_later=None):
    storage = MemoryStorage(dict(CONFIG if config is None else config), results or {"geocode_cache": dict(GEOCACHE)})
    engine = ScoutEngine(
        session,
        storage,
        provider=provider,
        keys={"gemini_search_key": "k", "api_key": "k"},
        tz=TZ,
        tz_name="America/Denver",
        retry_later=retry_later,
        now=lambda: NOW,
    )
    await engine.load()
    engine.llm = lambda: llm
    return engine, storage


async def scan(engine, force=True):
    job = engine.begin_scan(force)
    assert engine.scanning and engine.begin_scan(force) is None  # one at a time
    await job
    assert not engine.scanning


SEARCH = {
    "15": [ev("Pub Trivia", "2026-10-02T19:00", "Riverton", "trivia nights")],
    "50": [ev("Indie Band Live", "2026-10-05T20:00", "Millbrook", "indie concerts")],
}


async def test_scan_stores_events_and_run_details():
    async with aiohttp.ClientSession() as session:
        engine, storage = await make_engine(session, FakeLLM(SEARCH))
        await scan(engine)
    run = engine.results["last_run"]
    assert run["status"] == "ok" and run["new"] == 2 and not run["error"]
    assert {e["title"] for e in engine.picks()} == {"Pub Trivia", "Indie Band Live"}
    assert set(engine.results["bucket_scanned"]) == {"nearby", "local"}
    assert storage.results["last_run"]["new"] == 2  # saved
    state = engine.public_state(extra_field=1)
    assert state["extra_field"] == 1 and state["status"] == "ok" and len(state["events"]) == 2


async def test_gemini_makes_several_searches_per_group_on_different_interests():
    llm = FakeLLM(SEARCH)
    async with aiohttp.ClientSession() as session:
        engine, _ = await make_engine(session, llm, config=CONFIG | {"searches_per_group": 2})
        await scan(engine)
    local = [c["user"] for c in llm.calls if c["web_search"] and "within about 50 km" in c["user"]]
    assert len(local) == 2
    covered = [{n for n in ("indie concerts", "jazz", "comedy", "theatre") if f"- {n}" in prompt} for prompt in local]
    assert all(len(c) == 2 for c in covered) and not covered[0] & covered[1]
    assert len(engine.results["last_run"]["interests_searched"]) == 5


async def test_openrouter_keeps_one_search_call_per_group():
    llm = FakeLLM(SEARCH)
    async with aiohttp.ClientSession() as session:
        engine, _ = await make_engine(session, llm, provider="openrouter", config=CONFIG | {"searches_per_group": 3})
        engine.refresh_spend = _noop
        await scan(engine)
    assert len([c for c in llm.calls if c["web_search"]]) == 2  # nearby + local


async def _noop():
    return None


async def test_new_results_never_replace_old_ones():
    async with aiohttp.ClientSession() as session:
        engine, _ = await make_engine(session, FakeLLM(SEARCH))
        await scan(engine)
        engine.llm = lambda: FakeLLM({"15": [], "50": []})
        await scan(engine)
    assert {e["title"] for e in engine.picks()} == {"Pub Trivia", "Indie Band Live"}
    assert engine.results["last_run"]["new"] == 0


async def test_report_correction_survives_the_next_scan():
    async with aiohttp.ClientSession() as session:
        engine, _ = await make_engine(session, FakeLLM(SEARCH))
        await scan(engine)
        event = next(e for e in engine.upcoming_events() if e["title"] == "Indie Band Live")
        state = await engine.report(event["id"], {"problem": "location", "correction": {"town": "Stoneford"}})
        assert "outside Local" in state["message"]
        await scan(engine)  # the search finds it in Millbrook again
    event = engine.results["events"][event["id"]]
    assert event["town"] == "Stoneford" and event["status"] == "dismiss" and event["distance_km"] > 200
    assert "Indie Band Live" not in {e["title"] for e in engine.picks()}


async def test_errors_say_what_to_do():
    class BadKey(FakeLLM):
        async def complete(self, system, user, *, web_search, reasoning):
            raise OpenRouterAuthError("Google rejected the API key: API key not valid.")

    async with aiohttp.ClientSession() as session:
        engine, _ = await make_engine(session, BadKey({}))
        engine.key_hint = "Reconfigure it."
        await scan(engine)
    run = engine.results["last_run"]
    assert run["status"] == "error" and "Reconfigure it." in run["error"]


async def test_overload_schedules_a_follow_up():
    class Busy(FakeLLM):
        async def complete(self, system, user, *, web_search, reasoning):
            if web_search:
                raise OpenRouterError("Gemini returned HTTP 503: overloaded", transient=True)
            return await super().complete(system, user, web_search=web_search, reasoning=reasoning)

    calls = []
    async with aiohttp.ClientSession() as session:
        engine, _ = await make_engine(session, Busy({}), retry_later=lambda: calls.append(1) or "03:00")
        await scan(engine)
    assert calls == [1]
    assert "trying the failed parts again at 03:00" in engine.results["last_run"]["note"]
    assert engine.results["bucket_scanned"] == {}  # failed groups stay due


async def test_check_ready_and_validation():
    async with aiohttp.ClientSession() as session:
        engine, _ = await make_engine(session, FakeLLM({}), config={})
    with pytest.raises(ValueError):
        engine.check_ready()
    with pytest.raises(ValueError):
        await engine.save_config({"schedule": "25:99"})
    state = await engine.save_config({"locations": [{"city": "  Riverton "}], "interests": ["jazz", "Jazz"], "searches_per_group": 99})
    assert state["config"]["locations"][0]["city"] == "Riverton"
    assert [i["name"] for i in state["config"]["interests"]] == ["jazz"]
    assert state["config"]["searches_per_group"] == 10


async def test_json_file_storage_round_trip(tmp_path):
    storage = JsonFileStorage(tmp_path / "data")
    assert await storage.load_results() is None
    await storage.save_results({"events": {"a": {"title": "Café night"}}})
    assert (await storage.load_results())["events"]["a"]["title"] == "Café night"
    (tmp_path / "data" / "config.json").write_text("{broken", encoding="utf-8")
    assert await storage.load_config() is None
    assert (tmp_path / "data" / "config.damaged.json").exists()


def test_explain_adds_the_likely_fix():
    from scout.errors import explain

    assert "openrouter.ai/settings/keys" in explain(OpenRouterError("OpenRouter spending limit reached or no credits left (HTTP 402)."))
    assert "billing" in explain(OpenRouterError("Gemini returned HTTP 400: Google Search grounding is not available on the free tier"), provider="gemini")
    assert "“bad-model”" in explain(OpenRouterError("OpenRouter returned HTTP 400: bad-model is not a valid model ID"), model="bad-model")
    assert "midnight Pacific" in explain(OpenRouterError("Gemini rate limit or quota reached: x"), provider="gemini")
    assert explain(OpenRouterError("something odd")) == "something odd"


async def test_debug_info_has_no_keys_or_coordinates():
    from scout.engine import debug_info

    async with aiohttp.ClientSession() as session:
        engine, _ = await make_engine(session, FakeLLM(SEARCH))
        await scan(engine)
    info = debug_info(engine, home_assistant="2026.10")
    text = str(info)
    assert info["keys_set"] == {"gemini_search_key": True, "api_key": True}
    assert "'k'" not in text and "45.0461" not in text
    assert info["last_run"]["status"] == "ok" and info["events"]["picks"] == 2 and info["home_assistant"] == "2026.10"
