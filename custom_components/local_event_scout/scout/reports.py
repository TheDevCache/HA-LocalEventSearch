"""User reports of wrong event details: apply corrections, keep them, and teach later searches.

A report changes the stored event (town, date, link…) and remembers the original
values so it can be undone. Corrections are pinned: when a later search finds the
same event again with the old, wrong details, the corrections are put back on top.
A short log of reports outlives the events, so a cancelled or wrongly dated event
is not added again, and recent corrections are passed to the AI as hints.
"""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo
from typing import Any

from .dates import parse_event_time
from .geo import Geocoder, haversine_km
from .models import Bucket, Location
from .textutil import clean_str

PROBLEMS: dict[str, str] = {
    "location": "Wrong town or venue",
    "date": "Wrong date or time",
    "cancelled": "Cancelled or doesn't exist",
    "link": "Wrong or broken link",
    "other": "Something else",
}

# Fields a correction may change, per problem.
_FIELDS = {
    "location": ("town", "venue", "address"),
    "date": ("start", "end", "all_day"),
    "link": ("url",),
}
MAP_TOLERANCE = 1.15  # same leniency as the pipeline's distance check
MAX_HINTS = 15


def apply_report(
    event: dict[str, Any], problem: str, correction: dict[str, Any] | None, note: str, tz: tzinfo, when: str
) -> list[str]:
    """Change ``event`` in place for a report. Returns the names of the fields that changed.

    Raises ValueError with a message for the user when the report can't be used.
    """
    if problem not in PROBLEMS:
        raise ValueError(f"Problem must be one of: {', '.join(PROBLEMS)}.")
    correction = correction or {}
    note = clean_str(note, 300)
    changes: dict[str, Any] = {}

    if problem == "location":
        for key, limit in (("town", 80), ("venue", 120), ("address", 200)):
            value = clean_str(correction.get(key), limit)
            if value and value != event.get(key):
                changes[key] = value
        if changes.get("town") and "address" not in changes and event.get("address"):
            changes["address"] = ""  # a street address in the wrong town would mislead the map lookup
    elif problem == "date":
        day = clean_str(correction.get("date"), 10)
        if day:
            clock = clean_str(correction.get("time"), 5)
            end_day = clean_str(correction.get("end_date"), 10)
            parsed = parse_event_time(f"{day}T{clock}" if clock else day, end_day or None, tz)
            if parsed is None:
                raise ValueError("Enter the correct date as YYYY-MM-DD.")
            start, end = parsed.iso()
            for key, value in (("start", start), ("end", end), ("all_day", parsed.all_day)):
                if value != event.get(key):
                    changes[key] = value
    elif problem == "link":
        url = clean_str(correction.get("url"), 500)
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("The correct link must start with http:// or https://.")
        if url and url != event.get("url"):
            changes["url"] = url
    elif problem == "other" and not note:
        raise ValueError("Describe what's wrong in the note.")

    original = event.setdefault("original", {})
    for key, value in changes.items():
        original.setdefault(key, event.get(key))  # keep the very first value for undo
        event[key] = value
    corrections = event.setdefault("corrections", {})
    corrections.update(changes)
    if problem == "link":
        event["link_ok"] = None if "url" in changes else False
    event.setdefault("reports", []).append(
        {"problem": problem, "note": note, "at": when, "changed": sorted(changes)}
    )

    # A report the details can't fix makes the event unreliable: hide it.
    if problem == "cancelled":
        hide(event, "Reported as cancelled or not real")
    elif problem == "location" and not changes:
        hide(event, "Reported as being somewhere else")
    elif problem == "date" and not changes:
        hide(event, "Reported as having the wrong date")
    return sorted(changes)


def hide(event: dict[str, Any], reason: str) -> None:
    event["status"] = "dismiss"
    event["hidden_reason"] = reason


def undo_report(event: dict[str, Any]) -> None:
    """Put back the original details and forget the reports."""
    for key, value in (event.pop("original", None) or {}).items():
        event[key] = value
    event.pop("corrections", None)
    event.pop("reports", None)
    if event.pop("hidden_reason", None) and event.get("status") == "dismiss":
        event["status"] = "new"
    if event.get("relocated"):
        for key in ("distance_km", "distance_source"):
            event[key] = (event.get("relocated") or {}).get(key)
        event.pop("relocated", None)


async def relocate(
    event: dict[str, Any], geocoder: Geocoder, locations: list[Location], bucket: Bucket
) -> str:
    """Find a corrected venue on the map and re-check its distance group.

    Returns a short note for the user. Hides the event when it is now too far away.
    """
    origins = [(l.lat, l.lon) for l in locations if l.lat is not None and l.lon is not None]
    loc = next((l for l in locations if l.id == event.get("location_id")), locations[0] if locations else None)
    town = event.get("town") or ""
    if "," in town:
        suffix = loc.country if loc else ""  # the user typed "Town, Province" themselves
    else:
        suffix = ", ".join(p for p in ((loc.region, loc.country) if loc else ()) if p)
    queries = []
    if event.get("address"):
        queries.append(", ".join(p for p in (event["address"], town, suffix) if p))
    if event.get("venue") and town:
        queries.append(", ".join(p for p in (event["venue"], town, suffix) if p))
    if town:
        queries.append(", ".join(p for p in (town, suffix) if p))
    event.setdefault("relocated", {"distance_km": event.get("distance_km"), "distance_source": event.get("distance_source")})
    point = None
    for query in queries:
        point = await geocoder.lookup(query)
        if point:
            break
    if not point or not origins:
        event["distance_km"] = None
        event["distance_source"] = None
        return f"Couldn't find {town or 'the new place'} on the map, so the distance is unknown."
    distance = round(min(haversine_km(o[0], o[1], point[0], point[1]) for o in origins), 1)
    event["distance_km"] = distance
    event["distance_source"] = "map"
    event["corrections"]["distance_km"] = distance
    event["corrections"]["distance_source"] = "map"
    if distance > bucket.km * MAP_TOLERANCE + 5:
        hide(event, f"{distance:.0f} km away, outside {bucket.label} ({bucket.km:g} km)")
        return f"It's {distance:.0f} km away, outside {bucket.label} ({bucket.km:g} km), so it was hidden."
    return f"It's {distance:.0f} km away."


def reapply(fresh: dict[str, Any], old: dict[str, Any]) -> dict[str, Any]:
    """A new sighting of a reported event: keep the user's corrections on top of it."""
    corrections = old.get("corrections") or {}
    merged = fresh | corrections
    for key in ("original", "corrections", "reports", "hidden_reason", "relocated"):
        if key in old:
            merged[key] = old[key]
    if any(r.get("problem") == "link" for r in old.get("reports", [])) and "url" not in corrections:
        merged["link_ok"] = old.get("link_ok")
    return merged


def log_entry(event: dict[str, Any], problem: str, changed: list[str], note: str, when: str) -> dict[str, Any]:
    """What is kept after the event itself expires."""
    original = event.get("original") or {}
    return {
        "event_id": event["id"],
        "title": event["title"],
        "problem": problem,
        "note": note,
        "at": when,
        "start": original.get("start", event["start"]),  # as first reported, for matching new sightings
        "end": event.get("end"),
        "was": {k: original.get(k) for k in changed if k in original},
        "now": {k: event.get(k) for k in changed},
        "found_via": event.get("found_via"),
        "source_url": event.get("source_url") or "",
    }


def describe(entry: dict[str, Any]) -> str:
    """One line for prompts: what the user said was wrong."""
    title = entry["title"]
    day = (entry.get("start") or "")[:10]
    was, now = entry.get("was") or {}, entry.get("now") or {}
    problem = entry["problem"]
    if problem == "cancelled":
        text = f"{title} ({day}) is cancelled or does not exist. Never return it."
    elif problem == "location":
        if now.get("town"):
            text = f"{title} ({day}) takes place in {now['town']}" + (f", not {was['town']}" if was.get("town") else "") + "."
        elif now.get("venue"):
            text = f"{title} ({day}) is at {now['venue']}" + (f", not {was['venue']}" if was.get("venue") else "") + "."
        else:
            text = f"{title} ({day}) is not where the listing says."
    elif problem == "date":
        if now.get("start"):
            text = f"{title} is on {now['start'][:16].replace('T', ' ')}" + (f", not {day}" if day else "") + "."
        else:
            text = f"The date given for {title} ({day}) is wrong."
    elif problem == "link":
        text = f"{title} ({day}): the correct link is {now['url']}." if now.get("url") else f"{title} ({day}): the link given before was wrong."
    else:
        text = f"{title} ({day})"
    if entry.get("note"):
        text += f" Note: {entry['note']}"
    return text


def hints(log: list[dict[str, Any]], limit: int = MAX_HINTS) -> list[str]:
    """Most recent corrections first, as prompt lines."""
    return [describe(e) for e in reversed(log[-limit:])]


def ghost_events(log: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Reported events as they were first found, for matching later sightings with find_duplicate."""
    ghosts: dict[str, dict[str, Any]] = {}
    for entry in log:
        ghosts.setdefault(entry["event_id"], {"id": entry["event_id"], "title": entry["title"], "start": entry["start"]})
    return ghosts


def expired(entry: dict[str, Any], now: datetime) -> bool:
    """Log entries are kept until a day after both the reported and the corrected dates."""
    dates = [entry.get("start"), entry.get("end"), (entry.get("now") or {}).get("start"), (entry.get("now") or {}).get("end")]
    days = [d[:10] for d in dates if isinstance(d, str) and len(d) >= 10]
    if not days:
        return False
    return max(days) < (now.date() - timedelta(days=1)).isoformat()
