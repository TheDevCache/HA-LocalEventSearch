from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
import pytest

from scout import prompts
from scout import reports as R
from scout.geo import Geocoder
from scout.models import DEFAULT_BUCKETS, Location
from scout.pipeline import find_duplicate

TZ = ZoneInfo("America/Denver")
WHEN = "2026-10-10T12:00:00+00:00"
HOME = Location("home", "Riverton", "Westshire", "Exampleland", 45.0461, -100.1938)
LOCAL = next(b for b in DEFAULT_BUCKETS if b.id == "local")


def event(**overrides):
    base = {
        "id": "abc123",
        "title": "Indie Game Dev Summit",
        "start": "2026-10-24",
        "end": None,
        "all_day": True,
        "venue": "Riverton Hall",
        "address": "12 Main St",
        "town": "Riverton",
        "url": "https://example.com/summit",
        "bucket": "local",
        "location_id": "home",
        "found_via": "search",
        "source_url": "",
        "distance_km": 0.4,
        "distance_source": "map",
        "status": "new",
        "link_ok": True,
    }
    return base | overrides


def test_location_correction_keeps_original_and_clears_wrong_address():
    e = event()
    changed = R.apply_report(e, "location", {"town": "Stoneford"}, "", TZ, WHEN)
    assert changed == ["address", "town"]
    assert e["town"] == "Stoneford" and e["address"] == "" and e["venue"] == "Riverton Hall"
    assert e["original"] == {"town": "Riverton", "address": "12 Main St"}
    assert e["corrections"] == {"town": "Stoneford", "address": ""}
    assert e["status"] == "new"


async def test_relocate_hides_event_now_outside_its_group():
    e = event()
    R.apply_report(e, "location", {"town": "Stoneford"}, "", TZ, WHEN)
    # Stoneford is about 250 km east of Riverton; cached so no network is used.
    cache = {"riverton hall, stoneford, westshire, exampleland": None, "stoneford, westshire, exampleland": [45.05, -97.0]}
    async with aiohttp.ClientSession() as session:
        note = await R.relocate(e, Geocoder(session, cache), [HOME], LOCAL)
    assert e["distance_km"] > 200 and e["distance_source"] == "map"
    assert e["status"] == "dismiss" and "outside Local" in e["hidden_reason"]
    assert "hidden" in note
    R.undo_report(e)
    assert e["town"] == "Riverton" and e["address"] == "12 Main St"
    assert e["distance_km"] == 0.4 and e["status"] == "new"
    assert "corrections" not in e and "hidden_reason" not in e


async def test_relocate_nearby_keeps_event_visible():
    e = event()
    R.apply_report(e, "location", {"town": "Millbrook", "venue": "Old Mill"}, "", TZ, WHEN)
    cache = {"old mill, millbrook, westshire, exampleland": [45.10, -100.10]}
    async with aiohttp.ClientSession() as session:
        await R.relocate(e, Geocoder(session, cache), [HOME], LOCAL)
    assert e["status"] == "new" and e["distance_km"] < 15


def test_date_correction_and_validation():
    e = event()
    changed = R.apply_report(e, "date", {"date": "2026-11-02", "time": "19:30"}, "", TZ, WHEN)
    assert set(changed) == {"start", "all_day"}
    assert e["start"].startswith("2026-11-02T19:30") and e["all_day"] is False
    with pytest.raises(ValueError):
        R.apply_report(event(), "date", {"date": "next tuesday"}, "", TZ, WHEN)


def test_unfixable_reports_hide_the_event():
    for problem in ("cancelled", "location", "date"):
        e = event()
        R.apply_report(e, problem, {}, "", TZ, WHEN)
        assert e["status"] == "dismiss" and e["hidden_reason"]
    e = event()
    R.apply_report(e, "link", {}, "", TZ, WHEN)
    assert e["status"] == "new" and e["link_ok"] is False
    with pytest.raises(ValueError):
        R.apply_report(event(), "other", {}, "", TZ, WHEN)
    with pytest.raises(ValueError):
        R.apply_report(event(), "nonsense", {}, "", TZ, WHEN)


def test_corrections_survive_a_new_sighting():
    old = event()
    R.apply_report(old, "location", {"town": "Stoneford"}, "", TZ, WHEN)
    old["distance_km"], old["corrections"]["distance_km"] = 250.0, 250.0
    fresh = event(town="Riverton", distance_km=0.2, summary="updated summary")
    merged = R.reapply(fresh, old)
    assert merged["town"] == "Stoneford" and merged["distance_km"] == 250.0
    assert merged["summary"] == "updated summary"
    assert merged["original"]["town"] == "Riverton" and merged["reports"]


def test_log_hints_and_ghost_matching():
    e = event()
    changed = R.apply_report(e, "location", {"town": "Stoneford"}, "the big convention centre", TZ, WHEN)
    entry = R.log_entry(e, "location", changed, "the big convention centre", WHEN)
    gone = event(id="zzz", title="Harvest Fair")
    R.apply_report(gone, "cancelled", {}, "", TZ, WHEN)
    log = [entry, R.log_entry(gone, "cancelled", [], "", WHEN)]
    lines = R.hints(log)
    assert lines[0].startswith("Harvest Fair (2026-10-24) is cancelled")
    assert "takes place in Stoneford, not Riverton" in lines[1] and "convention centre" in lines[1]
    text = prompts.search_prompt(
        today=datetime(2026, 10, 10).date(), tz_name="America/Denver", location_text="Riverton",
        bucket=LOCAL, interests=[], dislikes=[], max_results=5, corrections=lines,
    )
    assert "Trust them over any web page" in text and "Stoneford" in text
    ghosts = R.ghost_events(log)
    sighting = {"id": "new1", "title": "Harvest Fair!", "start": "2026-10-24"}
    assert find_duplicate(ghosts, sighting)["id"] == "zzz"


def test_log_entries_expire_after_both_dates():
    entry = {"start": "2026-10-24", "end": None, "now": {"start": "2026-11-02T19:30:00-07:00"}}
    assert not R.expired(entry, datetime(2026, 11, 2, 12, tzinfo=TZ))
    assert R.expired(entry, datetime(2026, 11, 5, 12, tzinfo=TZ))
