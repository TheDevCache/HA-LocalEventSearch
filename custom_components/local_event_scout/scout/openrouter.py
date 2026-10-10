"""Minimal OpenRouter client: chat completions with cost, plus key usage."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from typing import Any

import aiohttp

BASE_URL = "https://openrouter.ai/api/v1"


TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529}


class OpenRouterError(Exception):
    """A request failed; the message is safe to show to the user.

    ``transient`` marks failures worth retrying (overload, rate limits, timeouts).
    ``retry_after`` is the server's suggested wait in seconds, when it gave one.
    """

    def __init__(self, message: str, *, transient: bool = False, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.transient = transient
        self.retry_after = retry_after


class OpenRouterAuthError(OpenRouterError):
    """The API key was rejected."""


@dataclass
class ChatResult:
    content: str
    cost: float
    raw: dict[str, Any]

    def usage_summary(self) -> dict[str, Any]:
        """Token, search and cost figures for one call (for the usage breakdown)."""
        usage = self.raw.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        tools = usage.get("server_tool_use") or {}
        return {
            "cost": round(self.cost, 6),
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": details.get("reasoning_tokens"),
            "web_searches": tools.get("web_search_requests"),
        }


class OpenRouterClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        *,
        app_title: str = "Home Assistant Local Event Scout",
        timeout: float = 180,
    ) -> None:
        self._session = session
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": app_title,
            "HTTP-Referer": "https://github.com/TheDevCache/HA-LocalEventSearch",
        }
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def chat(self, payload: dict[str, Any]) -> ChatResult:
        body = {**payload, "usage": {"include": True}}
        raw = await self._request("POST", "/chat/completions", body)
        if "error" in raw:
            code = (raw.get("error") or {}).get("code") if isinstance(raw.get("error"), dict) else None
            raise OpenRouterError(_error_message(raw), transient=_as_int(code) in TRANSIENT_STATUS)
        try:
            message = raw["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as err:
            raise OpenRouterError("OpenRouter returned a response with no message.", transient=True) from err
        content = message.get("content")
        if isinstance(content, list):  # some providers return content parts
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        usage = raw.get("usage") or {}
        try:
            cost = float(usage.get("cost") or 0)
        except (TypeError, ValueError):
            cost = 0.0
        return ChatResult(content=content or "", cost=cost, raw=raw)

    async def key_info(self) -> dict[str, Any]:
        """Usage and limit for the current key (GET /key)."""
        raw = await self._request("GET", "/key", None)
        return raw.get("data") or {}

    async def _request(self, method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        try:
            async with self._session.request(
                method, BASE_URL + path, json=body, headers=self._headers, timeout=self._timeout
            ) as response:
                text = await response.text()
                status = response.status
                retry_after = _retry_after(response.headers.get("Retry-After"))
        except asyncio.TimeoutError as err:
            raise OpenRouterError("OpenRouter took too long to respond.", transient=True) from err
        except aiohttp.ClientError as err:
            raise OpenRouterError(f"Could not reach OpenRouter: {err}", transient=True) from err
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError:
            data = {}
        if status in (401, 403):
            raise OpenRouterAuthError(f"OpenRouter rejected the API key (HTTP {status}).")
        if status == 402:
            raise OpenRouterError("OpenRouter spending limit reached or no credits left (HTTP 402).")
        if status >= 400:
            raise OpenRouterError(
                f"OpenRouter returned HTTP {status}: {_error_message(data) or text[:200]}",
                transient=status in TRANSIENT_STATUS,
                retry_after=retry_after,
            )
        return data


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _retry_after(value: str | None) -> float | None:
    try:
        return max(0.0, float(value)) if value else None
    except ValueError:
        return None


def _error_message(data: dict[str, Any]) -> str:
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error or "")
