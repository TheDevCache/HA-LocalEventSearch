import json

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from scout.openrouter import OpenRouterClient, OpenRouterError
from scout.providers import GeminiProvider, ProviderError, with_retries


async def test_with_retries_retries_only_transient_errors():
    waits, calls = [], []

    async def sleep(seconds):
        waits.append(seconds)

    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise OpenRouterError("busy", transient=True, retry_after=7 if len(calls) == 2 else None)
        return "ok"

    assert await with_retries(flaky, delays=(15, 45), sleep=sleep) == "ok"
    assert waits == [15, 7]  # second wait follows the server's Retry-After

    calls.clear()

    async def broken():
        calls.append(1)
        raise OpenRouterError("bad key")

    with pytest.raises(OpenRouterError):
        await with_retries(broken, delays=(15, 45), sleep=sleep)
    assert len(calls) == 1


async def test_with_retries_gives_up_after_last_attempt():
    async def sleep(_):
        pass

    async def always_busy():
        raise OpenRouterError("busy", transient=True)

    with pytest.raises(OpenRouterError, match="busy"):
        await with_retries(always_busy, delays=(1, 1), sleep=sleep)


async def test_openrouter_client_marks_overload_as_transient():
    async def overloaded(request):
        return web.json_response({"error": {"message": "Provider overloaded"}}, status=503, headers={"Retry-After": "12"})

    async def in_body(request):
        return web.json_response({"error": {"message": "Rate limited upstream", "code": 429}})

    app = web.Application()
    app.add_routes([web.post("/a/chat/completions", overloaded), web.post("/b/chat/completions", in_body)])
    server = TestServer(app)
    await server.start_server()
    import scout.openrouter as mod

    original = mod.BASE_URL
    try:
        async with aiohttp.ClientSession() as session:
            client = OpenRouterClient(session, "k")
            mod.BASE_URL = str(server.make_url("/a"))
            with pytest.raises(OpenRouterError) as err:
                await client.chat({"model": "m", "messages": []})
            assert err.value.transient and err.value.retry_after == 12
            mod.BASE_URL = str(server.make_url("/b"))
            with pytest.raises(OpenRouterError) as err:
                await client.chat({"model": "m", "messages": []})
            assert err.value.transient
    finally:
        mod.BASE_URL = original
        await server.close()


def _gemini_answer(searched: bool, text='{"events": []}'):
    candidate = {"content": {"parts": [{"text": text}]}}
    if searched:
        candidate["groundingMetadata"] = {"webSearchQueries": ["events riverton"]}
    return {"candidates": [candidate], "usageMetadata": {"promptTokenCount": 700, "candidatesTokenCount": 200}}


async def test_gemini_insists_on_searching_then_reports_it(monkeypatch):
    async with aiohttp.ClientSession() as session:
        provider = GeminiProvider(session, search_key="P", process_key=None, search_api="generate",
                                  search_model="gemini-3.1-flash-lite", process_model="gemini-3.8-flash")
        sent = []

        async def answers_from_memory(model, key, body, path=None):
            sent.append((path, json.loads(json.dumps(body))))
            if path == "/interactions":
                return {"status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": "{}"}]}]}
            return _gemini_answer(searched=False)

        monkeypatch.setattr(provider, "_post", answers_from_memory)
        with pytest.raises(ProviderError, match="Test web search"):
            await provider.complete("sys", "find events", web_search=True, reasoning="low")
        assert [p for p, _ in sent] == [None, None, "/interactions", "/interactions"]
        assert "run Google searches now" in sent[1][1]["contents"][0]["parts"][0]["text"]

        replies = iter([_gemini_answer(searched=False), _gemini_answer(searched=True)])

        async def searches_second_time(model, key, body, path=None):
            return next(replies)

        provider.search_api = "generate"
        monkeypatch.setattr(provider, "_post", searches_second_time)
        done = await provider.complete("sys", "find events", web_search=True, reasoning="low")
        assert done.usage["web_searches"] == 1


async def test_gemini_retries_when_overloaded(monkeypatch):
    async with aiohttp.ClientSession() as session:
        provider = GeminiProvider(session, search_key="P", process_key="F",
                                  search_model="gemini-3.1-flash-lite", process_model="gemini-3.8-flash")
        provider.retry_delays = (0, 0)
        replies = iter([ProviderError("high demand", transient=True), _gemini_answer(searched=False, text='{"rankings": []}')])

        async def post(model, key, body, path=None):
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply

        monkeypatch.setattr(provider, "_post", post)
        done = await provider.complete("sys", "rank", web_search=False, reasoning="none")
        assert done.content == '{"rankings": []}'
