"""Find a town's event calendars (city, tourism, venues, news listings) to save as sources.

One web-search call asks for pages that list many upcoming events; each page is
then loaded to check that Event Scout can actually read it, so the user only
picks from pages that work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from .linkcheck import resolve_redirects
from .models import Interest
from .providers import Completion, LLMProvider
from .sources import fetch_pages, host_of, normalize_url
from .textutil import clean_str, extract_json

MAX_SUGGESTIONS = 12
REDIRECT_HOSTS = ("vertexaisearch.cloud.google.com",)
# Pages Event Scout can't read without logging in, or that are searches rather than listings.
SKIP_HOSTS = (
    "facebook.com", "instagram.com", "x.com", "twitter.com", "tiktok.com", "linkedin.com",
    "reddit.com", "youtube.com", "google.com", "bing.com", "yelp.com", "tripadvisor.com",
)
KINDS = ("city", "tourism", "venue", "news", "library", "university", "organiser", "other")

DISCOVER_SYSTEM = (
    "You find web pages that list upcoming local events, using web search. You only return pages "
    "you actually saw in your search results and never invent URLs. You answer with a single JSON "
    "object and nothing else."
)


def discover_prompt(*, today: date, location_text: str, interests: list[Interest], max_results: int = MAX_SUGGESTIONS) -> str:
    interest_text = ", ".join(i.name for i in interests[:25]) or "anything popular"
    return f"""Today is {today:%A, %B} {today.day}, {today.year}.

Find web pages that each list MANY upcoming in-person events in or near {location_text}, and are kept up to date.
Good examples: the city's official events calendar, the tourism board's "what's on" page, local news
"things to do" listings, library, university and arts-council calendars, event pages of the main venues
(theatres, concert halls, arenas, convention centres, clubs), and organisers related to these interests:
{interest_text}.

Do not return: single-event pages, social media pages, ticket-site search results, or pages for other towns.
Prefer the page that shows the list of events itself, not a site's home page.
Return at most {max_results} pages.

Return exactly this JSON shape:
{{"sources": [{{
  "url": "https://... the events listing page",
  "name": "short name, e.g. City of Example events calendar",
  "kind": one of {list(KINDS)},
  "covers": "a few words on what it lists, e.g. concerts and theatre downtown"
}}]}}
If you find nothing, return {{"sources": []}}."""


@dataclass
class DiscoverResult:
    sources: list[dict[str, Any]] = field(default_factory=list)
    cost: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
    error: str = ""


def _skip(url: str) -> bool:
    host = host_of(url)
    return any(host == h or host.endswith("." + h) for h in SKIP_HOSTS)


def rate_page(page) -> tuple[str, str]:
    """(rating, note): good | ok | weak | broken."""
    if page.error and not page.text:
        return "broken", f"Couldn't load it ({page.error})."
    if page.error:
        return "weak", "Almost no readable text; it probably needs JavaScript, so Event Scout may not see its events."
    if page.feed:
        return "good", f"Has a calendar feed with {page.structured_events} upcoming events."
    if page.structured_events >= 3:
        return "good", f"Lists {page.structured_events} events in a format Event Scout reads exactly."
    if page.structured_events:
        n = page.structured_events
        return "ok", f"Lists {n} event{'s' if n > 1 else ''} in a format Event Scout reads exactly."
    if len(page.text) >= 1500:
        return "ok", "Readable."
    return "weak", "Not much readable text; it may not show many events."


async def discover_sources(
    session: aiohttp.ClientSession,
    llm: LLMProvider,
    *,
    today: date,
    location_text: str,
    interests: list[Interest],
    known_urls: list[str] | None = None,
) -> DiscoverResult:
    result = DiscoverResult()
    completion: Completion = await llm.complete(
        DISCOVER_SYSTEM,
        discover_prompt(today=today, location_text=location_text, interests=interests),
        web_search=True,
        reasoning="low",
    )
    result.cost = completion.cost
    result.usage = completion.usage
    try:
        parsed = extract_json(completion.content)
    except ValueError:
        result.error = "The AI's answer couldn't be read. Try again."
        return result
    raw = parsed.get("sources", []) if isinstance(parsed, dict) else parsed
    candidates: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        url = clean_str(item.get("url"), 500)
        if not normalize_url(url):
            continue
        kind = clean_str(item.get("kind"), 20).lower()
        candidates.append(
            {
                "url": url,
                "name": clean_str(item.get("name"), 80) or host_of(url),
                "kind": kind if kind in KINDS else "other",
                "covers": clean_str(item.get("covers"), 120),
            }
        )

    redirects = [c["url"] for c in candidates if urlsplit(c["url"]).netloc in REDIRECT_HOSTS]
    if redirects:
        resolved = await resolve_redirects(session, redirects)
        for candidate in candidates:
            candidate["url"] = resolved.get(candidate["url"], candidate["url"])

    seen = {normalize_url(u) for u in known_urls or []}
    unique = []
    for candidate in candidates:
        key = normalize_url(candidate["url"])
        if key in seen or _skip(candidate["url"]) or urlsplit(candidate["url"]).netloc in REDIRECT_HOSTS:
            continue
        seen.add(key)
        unique.append(candidate)
    unique = unique[:MAX_SUGGESTIONS]

    pages = await fetch_pages(session, [c["url"] for c in unique])
    order = {"good": 0, "ok": 1, "weak": 2, "broken": 3}
    for candidate, page in zip(unique, pages):
        candidate["rating"], candidate["note"] = rate_page(page)
        candidate["events_found"] = page.structured_events
    result.sources = sorted(unique, key=lambda c: order[c["rating"]])
    return result
