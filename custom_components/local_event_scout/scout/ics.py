"""iCalendar (.ics) output: a subscribable feed of picks, or a single event to add to a calendar."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

PRODID = "-//TheDevCache//Local Event Scout//EN"


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """Lines longer than 75 octets continue on the next line after a space (RFC 5545 3.1)."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    parts, current, size = [], "", 0
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > (75 if not parts else 74):
            parts.append(current)
            current, size = "", 0
        current += char
        size += width
    parts.append(current)
    return "\r\n ".join(parts)


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def event_times(event: dict[str, Any]) -> tuple[date | datetime, date | datetime] | None:
    """Start and (exclusive) end; all-day events use dates, timed ones aware datetimes."""
    try:
        if event.get("all_day"):
            start = date.fromisoformat(event["start"][:10])
            last = date.fromisoformat((event.get("end") or event["start"])[:10])
            return start, max(last, start) + timedelta(days=1)
        start_dt = datetime.fromisoformat(event["start"])
        end_dt = datetime.fromisoformat(event["end"]) if event.get("end") else start_dt + timedelta(hours=2)
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(hours=2)
        if start_dt.tzinfo is None:
            return None
        return start_dt, end_dt
    except (KeyError, TypeError, ValueError):
        return None


def _vevent(event: dict[str, Any], stamp: str) -> list[str]:
    times = event_times(event)
    if times is None:
        return []
    start, end = times
    if isinstance(start, datetime):
        when = [f"DTSTART:{_utc(start)}", f"DTEND:{_utc(end)}"]  # type: ignore[arg-type]
    else:
        when = [f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end:%Y%m%d}"]
    location = ", ".join(p for p in (event.get("venue"), event.get("address") or event.get("town")) if p)
    description = "\n\n".join(
        p for p in (event.get("why"), event.get("summary"), event.get("weather_note"), event.get("url")) if p
    )
    lines = [
        "BEGIN:VEVENT",
        f"UID:{event['id']}@local-event-scout",
        f"DTSTAMP:{stamp}",
        *when,
        f"SUMMARY:{_escape(event['title'])}",
    ]
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    if event.get("url"):
        lines.append(f"URL:{event['url']}")
    if event.get("category") or event.get("interest"):
        lines.append(f"CATEGORIES:{_escape(event.get('category') or event.get('interest'))}")
    lines.append("END:VEVENT")
    return lines


def build_calendar(events: list[dict[str, Any]], name: str = "Event Scout picks", now: datetime | None = None) -> str:
    stamp = _utc(now or datetime.now(timezone.utc))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(name)}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    for event in events:
        lines.extend(_vevent(event, stamp))
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
