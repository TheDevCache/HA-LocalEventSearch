import aiohttp

from scout.providers import GeminiProvider, OpenRouterProvider
from scout.sources import normalize_url, page_to_text

HTML = """<html><head><title>Venue</title>
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
  {"@type":"MusicEvent","name":"The Trews","startDate":"2026-10-10T20:00","url":"/shows/trews",
   "location":{"@type":"Place","name":"Lantern Hall","address":{"streetAddress":"100 Main St","addressLocality":"Riverton"}}},
  {"@type":"Organization","name":"Not an event"}]}</script>
<script>var x = 1;</script><style>.a{}</style></head>
<body><h1>What's on</h1><p>Trivia every Tuesday <a href="/trivia">Details</a></p></body></html>"""


def test_page_to_text_prefers_structured_events_and_keeps_links():
    text, count = page_to_text(HTML, "https://venue.example/events")
    assert count == 1
    assert "The Trews | start 2026-10-10T20:00 | at Lantern Hall, 100 Main St, Riverton | https://venue.example/shows/trews" in text
    assert "Trivia every Tuesday" in text and "[https://venue.example/trivia]" in text
    assert "var x" not in text and ".a{}" not in text


def test_page_to_text_without_structured_data():
    text, count = page_to_text("<body><div>Folk night Oct 12</div><a href='https://t.example/x'>Tickets here</a></body>", "https://v.example/")
    assert count == 0 and "Folk night Oct 12" in text and "[https://t.example/x]" in text


def test_normalize_url():
    assert normalize_url("https://WWW.Venue.example/events/#top") == "https://www.venue.example/events"
    assert normalize_url("ftp://x") == "" and normalize_url("not a url") == ""


class _Client:
    def __init__(self):
        self.payloads = []

    async def chat(self, payload):
        from scout.openrouter import ChatResult

        self.payloads.append(payload)
        return ChatResult("{}", 0.004, {"usage": {"prompt_tokens": 900, "completion_tokens": 200,
                                                  "completion_tokens_details": {"reasoning_tokens": 150},
                                                  "server_tool_use": {"web_search_requests": 2}, "cost": 0.004}})


async def test_openrouter_provider_limits_search_and_reports_usage():
    client = _Client()
    provider = OpenRouterProvider(client, "deepseek/deepseek-v4.1-flash", "exa")
    done = await provider.complete("sys", "user", web_search=True, reasoning="low")
    params = client.payloads[0]["tools"][0]["parameters"]
    assert params == {"max_uses": 2, "max_results": 6, "max_total_results": 12, "engine": "exa"}
    assert client.payloads[0]["reasoning"] == {"effort": "low"}
    assert done.usage == {"cost": 0.004, "input_tokens": 900, "output_tokens": 200, "reasoning_tokens": 150,
                          "web_searches": 2, "model": "deepseek/deepseek-v4.1-flash"}
    await provider.complete("sys", "user", web_search=False, reasoning="none")
    assert "tools" not in client.payloads[1] and client.payloads[1]["reasoning"] == {"effort": "none"}


async def test_gemini_provider_uses_the_right_key_and_estimates_cost(monkeypatch):
    calls = []
    response = {
        "candidates": [{
            "content": {"parts": [{"text": "thinking...", "thought": True}, {"text": '{"events": []}'}]},
            "groundingMetadata": {"webSearchQueries": ["a", "b"], "groundingChunks": [{"web": {"uri": "https://r.example/1"}}]},
        }],
        "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 500, "thoughtsTokenCount": 500, "toolUsePromptTokenCount": 3000},
    }
    async with aiohttp.ClientSession() as session:
        provider = GeminiProvider(session, search_key="PAID", process_key="FREE", search_api="generate",
                                  search_model="gemini-3.1-flash-lite", process_model="gemini-3.8-flash")

        async def fake_post(model, key, body, path=None):
            calls.append((model, key, body, path))
            return response

        monkeypatch.setattr(provider, "_post", fake_post)
        searched = await provider.complete("sys", "find", web_search=True, reasoning="low")
        ranked = await provider.complete("sys", "rank", web_search=False, reasoning="none")

    assert calls[0][:2] == ("gemini-3.1-flash-lite", "PAID") and calls[0][2]["tools"] == [{"google_search": {}}]
    assert calls[1][:2] == ("gemini-3.8-flash", "FREE") and "tools" not in calls[1][2]
    assert searched.content == '{"events": []}' and searched.sources == ["https://r.example/1"]
    assert searched.usage["web_searches"] == 2 and searched.usage["api"] == "generate"
    assert searched.usage["input_tokens"] == 4000 and searched.usage["output_tokens"] == 1000
    assert abs(searched.cost - (4000 * 0.25 + 1000 * 1.50) / 1e6) < 1e-12
    assert ranked.cost == 0 and ranked.usage["free_tier"] is True


INTERACTION = {
    "id": "abc", "status": "completed",
    "steps": [
        {"type": "thought", "content": [{"type": "text", "text": "let me search"}]},
        {"type": "google_search_call", "arguments": {"queries": ["events riverton october", "riverton festival"]}},
        {"type": "google_search_result", "result": [{"search_suggestions": "<div>…</div>"}]},
        {"type": "model_output", "content": [{"type": "text", "text": '{"events": []}',
            "annotations": [{"type": "url_citation", "url": "https://venue.example/e", "start_index": 0, "end_index": 5}]}]},
    ],
    "usage": {"total_input_tokens": 900, "total_output_tokens": 300, "total_thought_tokens": 100,
              "total_tool_use_tokens": 2500, "grounding_tool_count": 1},
}


def test_parse_interaction_response():
    from scout.providers import _parse_interaction

    reply = _parse_interaction(INTERACTION, search=True)
    assert reply.text == '{"events": []}' and reply.searched and reply.searches == 2
    assert reply.sources == ["https://venue.example/e"]
    assert reply.input_tokens == 3400 and reply.output_tokens == 400 and reply.thought_tokens == 100
    no_search = {"status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": "hi"}]}],
                 "usage": {"total_input_tokens": 50, "total_output_tokens": 5}}
    assert not _parse_interaction(no_search, search=True).searched


async def test_gemini_auto_mode_prefers_interactions_and_falls_back(monkeypatch):
    memory_answer = {"status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": '{"events": []}'}]}],
                     "usage": {"total_input_tokens": 700, "total_output_tokens": 50}}
    grounded_generate = {"candidates": [{"content": {"parts": [{"text": '{"events": []}'}]},
                                         "groundingMetadata": {"webSearchQueries": ["q"]}}], "usageMetadata": {}}
    async with aiohttp.ClientSession() as session:
        provider = GeminiProvider(session, search_key="P", process_key="F",
                                  search_model="gemini-3.1-flash-lite", process_model="gemini-3.8-flash")
        paths = []

        async def post(model, key, body, path=None):
            paths.append(path or "generate")
            return memory_answer if path == "/interactions" else grounded_generate

        monkeypatch.setattr(provider, "_post", post)
        done = await provider.complete("sys", "find", web_search=True, reasoning="low")
        assert paths == ["/interactions", "/interactions", "generate"]  # interactions twice (with nudge), then generate
        assert done.usage["api"] == "generate" and provider.search_api == "generate"

        paths.clear()

        async def interactions_work(model, key, body, path=None):
            paths.append(path or "generate")
            assert body["tools"] == [{"type": "google_search"}] and body["system_instruction"] == "sys"
            return INTERACTION

        provider.search_api = "auto"
        monkeypatch.setattr(provider, "_post", interactions_work)
        done = await provider.complete("sys", "find", web_search=True, reasoning="low")
        assert paths == ["/interactions"] and done.usage["api"] == "interactions" and done.usage["web_searches"] == 2


ICS = """BEGIN:VCALENDAR\r
BEGIN:VEVENT\r
SUMMARY:Old show\r
DTSTART:20200101T200000\r
END:VEVENT\r
BEGIN:VEVENT\r
SUMMARY:Riverton Comic\\, Toy & Game Expo\r
DTSTART;TZID=America/Denver:20261017T100000\r
DTEND;TZID=America/Denver:20261017T170000\r
LOCATION:Exhibition Park\\, Riverton\r
URL:https://expo.example/2026\r
 -tickets\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_ics_events_skip_past_and_unfold_lines():
    from datetime import date

    from scout.sources import ics_events

    lines = ics_events(ICS, today=date(2026, 10, 2))
    assert lines == [
        "Riverton Comic, Toy & Game Expo | start 2026-10-17T10:00 | end 2026-10-17T17:00 | at Exhibition Park, Riverton | https://expo.example/2026-tickets"
    ]


def test_rss_items():
    from scout.sources import rss_items

    rss = """<rss><channel><item><title>Harvest Fest</title><link>https://x.example/h</link>
    <description>&lt;p&gt;Oct 12 at the park&lt;/p&gt;</description></item></channel></rss>"""
    assert rss_items(rss) == ["Harvest Fest | https://x.example/h | Oct 12 at the park"]


async def test_thin_javascript_page_falls_back_to_its_calendar_feed():
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    from scout.sources import fetch_page

    async def page(request):
        return web.Response(content_type="text/html", text="""<html><head>
            <link rel="alternate" type="application/rss+xml" title="Comments Feed" href="/comments/feed">
            <link rel="alternate" type="text/calendar" title="iCal Feed" href="/events/?ical=1"></head>
            <body><div id="calendar-app">Loading…</div></body></html>""")

    async def feed(request):
        return web.Response(content_type="text/calendar", text=ICS.replace("2026", "2099"))

    async def events(request):  # the iCal feed lives at the same path with ?ical=1
        return await (feed(request) if request.query.get("ical") else page(request))

    app = web.Application()
    app.add_routes([web.get("/events/", events)])
    server = TestServer(app)
    await server.start_server()
    try:
        async with aiohttp.ClientSession() as session:
            result = await fetch_page(session, str(server.make_url("/events/")))
    finally:
        await server.close()
    assert result.error is None and result.feed.endswith("/events/?ical=1")
    assert result.structured_events == 1 and "Toy & Game Expo | start 2099-10-17T10:00" in result.text


async def test_openrouter_search_count_is_configurable():
    client = _Client()
    provider = OpenRouterProvider(client, "m", max_searches=4)
    await provider.complete("sys", "user", web_search=True, reasoning="low")
    params = client.payloads[0]["tools"][0]["parameters"]
    assert params["max_uses"] == 4 and params["max_total_results"] == 24
