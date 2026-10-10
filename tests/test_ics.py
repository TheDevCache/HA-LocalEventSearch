from datetime import datetime, timezone

import aiohttp

from scout.ics import build_calendar

from test_engine import SEARCH, make_engine, scan
from test_pipeline import FakeLLM


def test_calendar_format():
    events = [
        {"id": "a1", "title": "Folk Fest, Day 1; outdoors", "start": "2026-10-20", "end": "2026-10-22", "all_day": True,
         "venue": "Bower Ponds", "town": "Pinecrest", "summary": "Three days of folk.\nBring a chair.", "url": "https://example.com/folk"},
        {"id": "b2", "title": "Trivia", "start": "2026-10-02T19:00:00-06:00", "end": None, "all_day": False, "url": "https://example.com/t",
         "why": "x" * 200},
        {"id": "bad", "title": "No date", "start": "", "all_day": True},
    ]
    text = build_calendar(events, now=datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert text.count("BEGIN:VEVENT") == 2
    assert "DTSTART;VALUE=DATE:20261020\r\nDTEND;VALUE=DATE:20261023" in text  # end is exclusive
    assert "DTSTART:20261003T010000Z\r\nDTEND:20261003T030000Z" in text  # 19:00 MDT, 2 hours by default
    assert r"SUMMARY:Folk Fest\, Day 1\; outdoors" in text
    assert "Three days of folk.\\nBring a chair." in text
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))


async def test_feed_needs_the_right_token():
    async with aiohttp.ClientSession() as session:
        engine, _ = await make_engine(session, FakeLLM(SEARCH))
        await scan(engine)
        token = engine.results["calendar_token"]
        assert len(token) > 20
        assert engine.calendar_feed("wrong") is None and engine.calendar_feed("") is None
        feed = engine.calendar_feed(token)
        assert feed.count("BEGIN:VEVENT") == 2
        event_id = next(iter(engine.results["events"]))
        await engine.set_feedback(event_id, "like")
        assert engine.calendar_feed(token, "liked").count("BEGIN:VEVENT") == 1
        assert engine.event_ics(token, event_id).count("BEGIN:VEVENT") == 1
        await engine.new_calendar_token()
        assert engine.calendar_feed(token) is None


async def test_odd_tokens_are_refused_not_crashing():
    async with aiohttp.ClientSession() as session:
        engine, _ = await make_engine(session, FakeLLM(SEARCH))
    assert engine.calendar_feed("é") is None and engine.event_ics("ü", "x") is None
