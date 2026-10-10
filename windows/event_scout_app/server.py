"""The local web server: the Event Scout page and the same API the Home Assistant integration offers.

It listens on 127.0.0.1 only. Requests must name this machine in the Host header
(blocks DNS-rebinding) and API calls must carry the X-Event-Scout header, which
other websites can't add without a CORS preflight this server never approves.
The calendar feed is the exception: calendar apps fetch it with just the secret
link, and it can optionally be shared on the local network on a second port.
"""

from __future__ import annotations

from http import HTTPStatus
import json
import logging
from pathlib import Path
import sys
from typing import Any

from aiohttp import web

from .core import PANEL_JS
from .host import AppHost

from scout.defaults import FEEDBACK_VERDICTS  # noqa: E402 - loaded by .core

_LOGGER = logging.getLogger(__name__)

API = "/api/local_event_scout"
HEADER = "X-Event-Scout"


def web_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "web" if (base / "web").exists() else Path(__file__).resolve().parent / "web"


def panel_js_path() -> Path:
    bundled = web_dir() / "event-scout-panel.js"
    return bundled if bundled.exists() else PANEL_JS


def _message(text: str, status: int) -> web.Response:
    return web.json_response({"message": text}, status=status)


async def _body(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json() if request.can_read_body else {}
    except (json.JSONDecodeError, ValueError) as err:
        raise web.HTTPBadRequest(text="Request body must be JSON.") from err
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="Request body must be a JSON object.")
    return body


def build_app(host: AppHost, port: int) -> web.Application:
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    @web.middleware
    async def guard(request: web.Request, handler):
        if request.host not in allowed_hosts:
            return web.Response(status=421, text="Wrong host.")
        feed = request.path.startswith(f"{API}/calendar/") and request.method == "GET"
        if request.path.startswith("/api/") and not feed:
            origin = request.headers.get("Origin")
            if request.headers.get(HEADER) != "1" or (origin and origin not in {f"http://{h}" for h in allowed_hosts}):
                return web.Response(status=403, text="Forbidden.")
        response = await handler(request)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    app = web.Application(middlewares=[guard], client_max_size=1024 * 1024)
    routes = web.RouteTableDef()

    @routes.get("/")
    async def index(_request: web.Request) -> web.Response:
        return web.FileResponse(web_dir() / "index.html")

    @routes.get("/panel/event-scout-panel.js")
    async def panel(_request: web.Request) -> web.Response:
        return web.FileResponse(panel_js_path(), headers={"Content-Type": "application/javascript"})

    @routes.get(f"{API}/config")
    async def get_state(_request: web.Request) -> web.Response:
        return web.json_response(host.public_state())

    @routes.post(f"{API}/config")
    async def save_config(request: web.Request) -> web.Response:
        try:
            await host.engine.save_config(await _body(request))
        except ValueError as err:
            return _message(str(err), HTTPStatus.BAD_REQUEST)
        host.reschedule()
        return web.json_response(host.public_state())

    @routes.post(f"{API}/run")
    async def run(request: web.Request) -> web.Response:
        body = await _body(request)
        try:
            return web.json_response(host.request_scan(force=bool(body.get("force", True))))
        except ValueError as err:
            return _message(str(err), HTTPStatus.BAD_REQUEST)

    @routes.post(f"{API}/feedback")
    async def feedback(request: web.Request) -> web.Response:
        body = await _body(request)
        if body.get("verdict") not in FEEDBACK_VERDICTS:
            return _message(f"verdict must be one of {FEEDBACK_VERDICTS}", HTTPStatus.BAD_REQUEST)
        try:
            await host.engine.set_feedback(str(body.get("id")), body["verdict"])
        except KeyError:
            return _message("Event not found (it may have expired).", HTTPStatus.NOT_FOUND)
        return web.json_response(host.public_state())

    @routes.post(f"{API}/report")
    async def report(request: web.Request) -> web.Response:
        body = await _body(request)
        try:
            state = await host.engine.report(str(body.get("id")), body)
        except KeyError:
            return _message("Event not found (it may have expired).", HTTPStatus.NOT_FOUND)
        except ValueError as err:
            return _message(str(err), HTTPStatus.BAD_REQUEST)
        return web.json_response(host.public_state(message=state.get("message", "")))

    @routes.post(f"{API}/sources")
    async def sources(request: web.Request) -> web.Response:
        try:
            await host.engine.source_action(await _body(request))
        except ValueError as err:
            return _message(str(err), HTTPStatus.BAD_REQUEST)
        except KeyError:
            return _message("That source is not saved.", HTTPStatus.NOT_FOUND)
        return web.json_response(host.public_state())

    @routes.post(f"{API}/test_search")
    async def test_search(_request: web.Request) -> web.Response:
        if not host.provider_ready:
            return _message("Connect an AI provider first.", HTTPStatus.BAD_REQUEST)
        return web.json_response(await host.engine.test_search())

    @routes.post(f"{API}/discover")
    async def discover(_request: web.Request) -> web.Response:
        if not host.provider_ready:
            return _message("Connect an AI provider first.", HTTPStatus.BAD_REQUEST)
        try:
            return web.json_response(await host.engine.discover())
        except ValueError as err:
            return _message(str(err), HTTPStatus.BAD_REQUEST)

    @routes.get(f"{API}/setup/locate")
    async def locate(_request: web.Request) -> web.Response:
        try:
            return web.json_response(await host.engine.suggest_location())
        except ValueError as err:
            return _message(str(err), HTTPStatus.BAD_REQUEST)

    @routes.get(f"{API}/debug")
    async def debug(_request: web.Request) -> web.Response:
        return web.json_response(host.debug())

    @routes.post(f"{API}/calendar/new_link")
    async def new_link(_request: web.Request) -> web.Response:
        await host.engine.new_calendar_token()
        return web.json_response(host.public_state())

    @routes.post(f"{API}/provider")
    async def provider(request: web.Request) -> web.Response:
        try:
            return web.json_response(await host.set_provider(await _body(request)))
        except ValueError as err:
            return _message(str(err), HTTPStatus.BAD_REQUEST)

    @routes.post(f"{API}/app")
    async def app_settings(request: web.Request) -> web.Response:
        try:
            return web.json_response(await host.set_app(await _body(request)))
        except (ValueError, OSError) as err:
            return _message(str(err), HTTPStatus.BAD_REQUEST)

    @routes.post(f"{API}/update")
    async def check_update(_request: web.Request) -> web.Response:
        await host.check_update()
        return web.json_response(host.public_state())

    app.add_routes(routes)
    add_feed_routes(app, host)
    app.router.add_static("/static", web_dir(), show_index=False)
    return app


def add_feed_routes(app: web.Application, host: AppHost) -> None:
    async def feed(request: web.Request) -> web.Response:
        body = host.engine.calendar_feed(request.match_info["token"], request.query.get("only", ""))
        if body is None:
            raise web.HTTPNotFound
        return web.Response(text=body, content_type="text/calendar", charset="utf-8")

    async def event(request: web.Request) -> web.Response:
        body = host.engine.event_ics(request.match_info["token"], request.match_info["event_id"])
        if body is None:
            raise web.HTTPNotFound
        return web.Response(
            text=body, content_type="text/calendar", charset="utf-8",
            headers={"Content-Disposition": 'attachment; filename="event.ics"'},
        )

    app.router.add_get(API + "/calendar/{token}.ics", feed)
    app.router.add_get(API + "/calendar/{token}/{event_id}.ics", event)


def build_feed_app(host: AppHost) -> web.Application:
    """Calendar feed only, for other devices on the local network."""
    app = web.Application()
    add_feed_routes(app, host)
    return app


class Server:
    """The main site on 127.0.0.1, plus the optional network feed site."""

    def __init__(self, host: AppHost, port: int) -> None:
        self.host = host
        self.port = port
        self._runner: web.AppRunner | None = None
        self._feed_runner: web.AppRunner | None = None
        host.on_lan_change = self.apply_lan_setting

    async def start(self) -> None:
        self._runner = web.AppRunner(build_app(self.host, self.port), access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, "127.0.0.1", self.port).start()
        await self.apply_lan_setting()

    async def apply_lan_setting(self) -> None:
        if self._feed_runner:
            await self._feed_runner.cleanup()
            self._feed_runner = None
        if self.host.settings.lan_feed:
            self._feed_runner = web.AppRunner(build_feed_app(self.host), access_log=None)
            await self._feed_runner.setup()
            try:
                await web.TCPSite(self._feed_runner, "0.0.0.0", self.host.settings.feed_port).start()
            except OSError as err:
                await self._feed_runner.cleanup()
                self._feed_runner = None
                raise ValueError(f"Couldn't share the feed on port {self.host.settings.feed_port}: {err}") from err

    async def stop(self) -> None:
        for runner in (self._feed_runner, self._runner):
            if runner:
                await runner.cleanup()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"
