"""Check GitHub for a newer release."""

from __future__ import annotations

import asyncio
import re

import aiohttp

RELEASES_URL = "https://api.github.com/repos/TheDevCache/HA-LocalEventSearch/releases/latest"


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", text or "")[:3]) or (0,)


async def latest_release(session: aiohttp.ClientSession) -> dict[str, str] | None:
    try:
        async with session.get(
            RELEASES_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "LocalEventScout"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            if response.status != 200:
                return None
            data = await response.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None
    exe = next((a.get("browser_download_url") for a in data.get("assets") or [] if str(a.get("name", "")).endswith(".exe")), "")
    return {"version": str(data.get("tag_name") or "").lstrip("v"), "url": data.get("html_url") or "", "download": exe or ""}


def newer(current: str, latest: str) -> bool:
    return parse_version(latest) > parse_version(current)
