"""Ten-day forecast from Open-Meteo (free, no key) for the ranking prompt."""

from __future__ import annotations

import asyncio
from datetime import date
import logging

import aiohttp

_LOGGER = logging.getLogger(__name__)
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes, as used by Open-Meteo.
_CODES = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "rain showers", 81: "rain showers",
    82: "violent rain showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorm",
    96: "thunderstorm with hail", 99: "thunderstorm with hail",
}


async def forecast_summary(session: aiohttp.ClientSession, lat: float, lon: float, fahrenheit: bool = False) -> str:
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "auto",
        "forecast_days": "10",
    }
    if fahrenheit:
        params["temperature_unit"] = "fahrenheit"
    try:
        async with session.get(FORECAST_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)) as response:
            if response.status != 200:
                return ""
            data = await response.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
        _LOGGER.debug("Forecast unavailable: %s", err)
        return ""
    daily = data.get("daily") or {}
    unit = "°F" if fahrenheit else "°C"
    lines = []
    for i, day in enumerate(daily.get("time") or []):
        try:
            when = date.fromisoformat(day)
        except ValueError:
            continue

        def _at(key: str):
            values = daily.get(key) or []
            return values[i] if i < len(values) else None

        parts = [_CODES.get(_at("weather_code"), "unknown")]
        if _at("temperature_2m_max") is not None:
            parts.append(f"high {round(_at('temperature_2m_max'))}{unit}")
        if _at("temperature_2m_min") is not None:
            parts.append(f"low {round(_at('temperature_2m_min'))}{unit}")
        if _at("precipitation_probability_max") is not None:
            parts.append(f"{_at('precipitation_probability_max')}% chance of precipitation")
        lines.append(f"{when:%a %Y-%m-%d}: " + ", ".join(parts))
    return "\n".join(lines)
