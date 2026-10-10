import json
from datetime import date

import aiohttp

import scout.discover as discover_mod
from scout.models import Interest
from scout.providers import Completion
from scout.sources import Page

from test_engine import make_engine
from test_pipeline import FakeLLM


class DiscoverLLM:
    def __init__(self, sources):
        self.sources = sources
        self.prompts = []

    async def complete(self, system, user, *, web_search, reasoning):
        assert web_search
        self.prompts.append(user)
        return Completion("```json\n" + json.dumps({"sources": self.sources}) + "\n```", 0.004, {"web_searches": 3})


SOURCES = [
    {"url": "https://riverton.example/events", "name": "City of Riverton events", "kind": "city", "covers": "everything"},
    {"url": "https://www.facebook.com/rivertonevents", "name": "FB", "kind": "other"},
    {"url": "https://theatre.example/whats-on", "name": "Riverton Theatre", "kind": "venue", "covers": "plays"},
    {"url": "https://riverton.example/events/", "name": "duplicate", "kind": "city"},
    {"url": "https://js-only.example/cal", "name": "JS calendar", "kind": "venue"},
    {"url": "https://gone.example/events", "name": "Gone", "kind": "news"},
    {"url": "https://saved.example/events", "name": "Already saved", "kind": "venue"},
    {"url": "not a url", "name": "junk"},
]


async def fake_fetch(session, urls, concurrency=4):
    pages = {
        "https://riverton.example/events": Page(urls[0], text="x" * 3000, structured_events=12),
        "https://theatre.example/whats-on": Page("", text="y" * 2000),
        "https://js-only.example/cal": Page("", text="Loading", error="page has almost no text (it may need JavaScript)"),
        "https://gone.example/events": Page("", error="HTTP 404"),
        "https://saved.example/events": Page("", text="z" * 1600),
    }
    return [pages[u] for u in urls]


async def test_discovery_filters_checks_and_ranks(monkeypatch):
    monkeypatch.setattr(discover_mod, "fetch_pages", fake_fetch)
    llm = DiscoverLLM(SOURCES)
    async with aiohttp.ClientSession() as session:
        found = await discover_mod.discover_sources(
            session, llm, today=date(2026, 10, 1), location_text="Riverton, Westshire",
            interests=[Interest("jazz")], known_urls=["https://saved.example/events"],
        )
    assert [s["name"] for s in found.sources] == ["City of Riverton events", "Riverton Theatre", "JS calendar", "Gone"]
    assert [s["rating"] for s in found.sources] == ["good", "ok", "weak", "broken"]
    assert "12 events" in found.sources[0]["note"]
    assert "Riverton, Westshire" in llm.prompts[0] and "jazz" in llm.prompts[0]


async def test_engine_discover_and_add_many(monkeypatch):
    monkeypatch.setattr(discover_mod, "fetch_pages", fake_fetch)
    async with aiohttp.ClientSession() as session:
        engine, _ = await make_engine(session, FakeLLM({}))
        engine.llm = lambda: DiscoverLLM(SOURCES)
        found = await engine.discover()
        assert len(found["sources"]) == 5 and not found["error"]
        assert engine.results["run_history"][-1]["kind"] == "discover"
        assert engine.month_cost() == 0.004
        picked = [{"url": s["url"], "name": s["name"], "bucket": "local"} for s in found["sources"][:2]]
        await engine.source_action({"action": "add_many", "sources": picked})
    assert [s["origin"] for s in engine.results["sources"]] == ["discovered", "discovered"]


async def test_discovered_source_names_survive_a_restart():
    async with aiohttp.ClientSession() as session:
        engine, storage = await make_engine(session, FakeLLM({}))
        await engine.source_action({"action": "add_many", "sources": [{"url": "https://riverton.example/events", "name": "City calendar"}]})
        again, _ = await make_engine(session, FakeLLM({}), results=storage.results)
    assert again.results["sources"][0]["name"] == "City calendar"
