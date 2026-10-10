"""Model providers: OpenRouter (any model, paid web search) and Google Gemini (AI Studio keys).

Both expose the same ``complete()`` call so the pipeline does not care which one
is in use. ``web_search=True`` lets the model search the web; the other calls
(reading saved pages, ranking) never search.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import json
import logging
from typing import Any, Protocol, TypeVar

import aiohttp

from .openrouter import TRANSIENT_STATUS, OpenRouterAuthError, OpenRouterClient, OpenRouterError

_LOGGER = logging.getLogger(__name__)
T = TypeVar("T")

# Waits between attempts when a provider is overloaded or rate-limited (3 attempts in total).
RETRY_DELAYS: tuple[float, ...] = (15, 45)
MAX_RETRY_AFTER = 120


async def with_retries(
    call: Callable[[], Awaitable[T]],
    *,
    delays: tuple[float, ...] = RETRY_DELAYS,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> T:
    """Run ``call``, retrying transient failures (overload, rate limits, timeouts) with back-off."""
    for attempt in range(len(delays) + 1):
        try:
            return await call()
        except OpenRouterError as err:
            if not getattr(err, "transient", False) or attempt >= len(delays):
                raise
            wait = min(err.retry_after, MAX_RETRY_AFTER) if err.retry_after else delays[attempt]
            _LOGGER.info("Model call failed (%s); retrying in %.0f s", err, wait)
            await sleep(wait)
    raise AssertionError("unreachable")

# Search limits per OpenRouter call: the main cost driver is how many results the
# model reads (and re-reads on every search round), so keep this tight.
DEFAULT_MAX_SEARCHES = 2
OPENROUTER_RESULTS_PER_SEARCH = 6

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# USD per 1M tokens (input, output) on Google's paid tier, used only to estimate
# the cost of calls made with the billing-enabled search key.
GEMINI_PRICES: dict[str, tuple[float, float]] = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.7-flash": (0.75, 3.75),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
}


class ProviderError(OpenRouterError):
    """A model call failed; the message is safe to show."""


class ProviderAuthError(OpenRouterAuthError):
    """An API key was rejected."""


@dataclass
class Completion:
    content: str
    cost: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)  # pages the search used, when known


class LLMProvider(Protocol):
    name: str

    async def complete(self, system: str, user: str, *, web_search: bool, reasoning: str) -> Completion: ...


# ----------------------------------------------------------------- OpenRouter


class OpenRouterProvider:
    name = "openrouter"

    retry_delays = RETRY_DELAYS

    def __init__(
        self, client: OpenRouterClient, model: str, engine: str = "auto", max_searches: int = DEFAULT_MAX_SEARCHES
    ) -> None:
        self._client = client
        self.model = model
        self._engine = engine
        self.max_searches = max(1, min(10, int(max_searches)))

    async def complete(self, system: str, user: str, *, web_search: bool, reasoning: str) -> Completion:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2 if web_search else 0.1,
            "reasoning": {"effort": reasoning},
        }
        if web_search:
            params: dict[str, Any] = {
                "max_uses": self.max_searches,
                "max_results": OPENROUTER_RESULTS_PER_SEARCH,
                "max_total_results": OPENROUTER_RESULTS_PER_SEARCH * self.max_searches,
            }
            if self._engine and self._engine != "auto":
                params["engine"] = self._engine
            payload["tools"] = [{"type": "openrouter:web_search", "parameters": params}]
        chat = await with_retries(lambda: self._client.chat(payload), delays=self.retry_delays)
        usage = chat.usage_summary()
        usage["model"] = self.model
        return Completion(content=chat.content, cost=chat.cost, usage=usage)


# --------------------------------------------------------------------- Gemini


class GeminiProvider:
    """Google AI Studio keys.

    Google Search grounding is not available on the free tier, so searches use
    ``search_key`` (a project with billing enabled, which includes 5,000 free
    searches a month) while everything else can use a free-tier ``process_key``.
    """

    name = "gemini"
    retry_delays = RETRY_DELAYS

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        search_key: str,
        process_key: str | None,
        search_model: str,
        process_model: str,
        process_key_is_free: bool = True,
        timeout: float = 180,
    ) -> None:
        self._session = session
        self._search_key = search_key
        self._process_key = process_key or search_key
        self.search_model = search_model
        self.process_model = process_model
        self._process_free = bool(process_key) and process_key_is_free
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def complete(self, system: str, user: str, *, web_search: bool, reasoning: str) -> Completion:
        model = self.search_model if web_search else self.process_model
        key = self._search_key if web_search else self._process_key
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.2 if web_search else 0.1},
        }
        thinking = _thinking_config(model, reasoning)
        if thinking:
            body["generationConfig"]["thinkingConfig"] = thinking
        if web_search:
            body["tools"] = [{"google_search": {}}]
        data = await self._send(model, key, body)
        if web_search and not _searched(data):
            # Gemini decides for itself whether to search and sometimes answers from memory,
            # which gives stale or invented events. Ask once more, insisting on a search.
            body["contents"][0]["parts"][0]["text"] = (
                user + "\n\nImportant: run Google searches now and use only events found in the results. "
                "Do not answer from memory."
            )
            data = await self._send(model, key, body)
            if not _searched(data):
                raise ProviderError(
                    "Gemini answered without running any Google searches, so its results were not used. "
                    "Check that the search key comes from a Google Cloud project with billing turned on "
                    "(Google Search is not available on the free tier)."
                )
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason")
            raise ProviderError(f"Gemini returned no answer{f' ({reason})' if reason else ''}.")
        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
        meta = data.get("usageMetadata") or {}
        grounding = candidate.get("groundingMetadata") or {}
        searches = (len(grounding.get("webSearchQueries") or []) or int(_searched(data))) if web_search else 0
        input_tokens = (meta.get("promptTokenCount") or 0) + (meta.get("toolUsePromptTokenCount") or 0)
        output_tokens = (meta.get("candidatesTokenCount") or 0) + (meta.get("thoughtsTokenCount") or 0)
        free = (not web_search) and self._process_free
        price = GEMINI_PRICES.get(model)
        cost = 0.0 if free or not price else (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000
        sources = [
            chunk["web"]["uri"]
            for chunk in grounding.get("groundingChunks") or []
            if isinstance(chunk, dict) and isinstance(chunk.get("web"), dict) and chunk["web"].get("uri")
        ]
        usage = {
            "model": model,
            "cost": round(cost, 6),
            "cost_estimated": not free and price is not None,
            "free_tier": free,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": meta.get("thoughtsTokenCount"),
            "web_searches": searches,
        }
        return Completion(content=text, cost=cost, usage=usage, sources=sources)

    async def _send(self, model: str, key: str, body: dict[str, Any]) -> dict[str, Any]:
        async def attempt() -> dict[str, Any]:
            try:
                return await self._post(model, key, body)
            except ProviderError as err:
                # Older/unknown models may reject the thinking settings; retry once without them.
                if "thinkingConfig" in body.get("generationConfig", {}) and "thinking" in str(err).lower():
                    body["generationConfig"].pop("thinkingConfig", None)
                    return await self._post(model, key, body)
                raise

        return await with_retries(attempt, delays=self.retry_delays)

    async def _post(self, model: str, key: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{GEMINI_BASE}/models/{model}:generateContent"
        try:
            async with self._session.post(
                url, json=body, headers={"x-goog-api-key": key}, timeout=self._timeout
            ) as response:
                text = await response.text()
                status = response.status
                retry_after = response.headers.get("Retry-After")
        except asyncio.TimeoutError as err:
            raise ProviderError("Gemini took too long to respond.", transient=True) from err
        except aiohttp.ClientError as err:
            raise ProviderError(f"Could not reach Gemini: {err}", transient=True) from err
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError:
            data = {}
        message = ((data.get("error") or {}).get("message") or text[:200]) if isinstance(data, dict) else text[:200]
        if status in (401, 403) or (status == 400 and "API key" in message):
            raise ProviderAuthError(f"Google rejected the API key: {message}")
        wait = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else None
        if status == 429:
            raise ProviderError(f"Gemini rate limit or quota reached: {message}", transient=True, retry_after=wait)
        if status >= 400:
            raise ProviderError(
                f"Gemini returned HTTP {status}: {message}", transient=status in TRANSIENT_STATUS, retry_after=wait
            )
        return data


def _searched(data: dict[str, Any]) -> bool:
    """True if a Gemini response shows that Google Search actually ran."""
    candidate = (data.get("candidates") or [{}])[0]
    grounding = candidate.get("groundingMetadata") or {}
    if grounding.get("webSearchQueries") or grounding.get("groundingChunks"):
        return True
    return bool((data.get("usageMetadata") or {}).get("toolUsePromptTokenCount"))


def _thinking_config(model: str, reasoning: str) -> dict[str, Any] | None:
    if model.startswith("gemini-3"):
        # Gemini 3 cannot fully disable thinking; "low" is the cheapest supported level.
        return {"thinkingLevel": "low"}
    if model.startswith("gemini-2.5"):
        if "pro" in model:
            return {"thinkingBudget": 128}
        return {"thinkingBudget": 0 if reasoning == "none" else 512}
    return None


async def validate_gemini_key(session: aiohttp.ClientSession, key: str) -> str | None:
    """Return an error key for the config flow, or None if the key works."""
    try:
        async with session.get(
            f"{GEMINI_BASE}/models", headers={"x-goog-api-key": key}, timeout=aiohttp.ClientTimeout(total=20)
        ) as response:
            if response.status in (400, 401, 403):
                return "invalid_auth"
            if response.status >= 400:
                return "cannot_connect"
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return "cannot_connect"
    return None
