"""Start Local Event Scout: the engine, the local web page and (on Windows) the tray icon."""

from __future__ import annotations

import argparse
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import socket
import sys
import tempfile
import threading
import webbrowser

import aiohttp

from .core import VERSION
from .host import AppHost
from .server import HEADER, Server
from .settings import AppSettings, default_data_dir

_LOGGER = logging.getLogger("event_scout_app")


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _setup_logging(data_dir: Path, verbose: bool) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [RotatingFileHandler(data_dir / "event-scout.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")]
    if sys.stderr is not None:  # the windowed .exe has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


class App:
    def __init__(self, data_dir: Path, port: int | None) -> None:
        self.data_dir = data_dir
        self.port = port or AppSettings.load(data_dir).port
        self.loop: asyncio.AbstractEventLoop | None = None
        self.host: AppHost | None = None
        self.server: Server | None = None
        self.ready = threading.Event()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    async def run(self) -> None:
        self._stop = asyncio.Event()
        async with aiohttp.ClientSession() as session:
            self.host = AppHost(self.data_dir, session)
            await self.host.start()
            self.server = Server(self.host, self.port)
            try:
                await self.server.start()
            except ValueError as err:  # network feed port taken; the app still works
                _LOGGER.warning("%s", err)
            _LOGGER.info("Local Event Scout %s running at %s (data in %s)", VERSION, self.url, self.data_dir)
            self.ready.set()
            await self._stop.wait()
            await self.host.stop()
            await self.server.stop()

    def stop(self) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(self._stop.set)

    def search_now(self) -> None:
        if self.loop and self.host:
            self.loop.call_soon_threadsafe(lambda: self.host.start_scan(force=True))

    def check_updates(self) -> None:
        async def _check() -> None:
            latest = await self.host.check_update()
            from .updates import newer  # noqa: PLC0415

            if latest and newer(VERSION, latest["version"]):
                webbrowser.open(latest["url"])
            else:
                webbrowser.open(self.url)

        if self.loop and self.host:
            asyncio.run_coroutine_threadsafe(_check(), self.loop)

    def open_page(self) -> None:
        webbrowser.open(self.url)


def _run_loop(app: App) -> None:
    app.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(app.loop)
    try:
        app.loop.run_until_complete(app.run())
    finally:
        app.loop.close()


async def self_test(report: Path | None = None) -> int:
    """Start everything on a spare port with a temporary data folder and call the API (used by CI)."""
    with tempfile.TemporaryDirectory() as folder:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        async with aiohttp.ClientSession() as session:
            host = AppHost(Path(folder), session)
            await host.start(background_jobs=False)
            server = Server(host, port)
            await server.start()
            base = f"http://127.0.0.1:{port}"
            ok = {"X-Event-Scout": "1"}
            checks = []
            try:
                async with session.get(base + "/") as r:
                    checks.append(("page", r.status == 200 and "event-scout-panel" in await r.text()))
                async with session.get(base + "/panel/event-scout-panel.js") as r:
                    checks.append(("panel script", r.status == 200 and "customElements.define" in await r.text()))
                async with session.get(base + "/api/local_event_scout/config") as r:
                    checks.append(("API needs header", r.status == 403))
                async with session.get(base + "/api/local_event_scout/config", headers={**ok, "Host": "evil.example"}) as r:
                    checks.append(("host check", r.status == 421))
                async with session.get(base + "/api/local_event_scout/config", headers=ok) as r:
                    state = await r.json()
                    checks.append(("state", r.status == 200 and state["version"] == VERSION and state["host"] == "windows"))
                body = {**state["config"], "locations": [{"city": "Testville"}], "interests": [{"name": "jazz"}]}
                async with session.post(base + "/api/local_event_scout/config", json=body, headers=ok) as r:
                    checks.append(("save settings", r.status == 200 and (await r.json())["config"]["interests"][0]["name"] == "jazz"))
                async with session.post(base + "/api/local_event_scout/run", json={}, headers=ok) as r:
                    checks.append(("needs provider", r.status == 400))
                async with session.get(f"{base}/api/local_event_scout/calendar/{state['calendar_token']}.ics") as r:
                    checks.append(("calendar feed", r.status == 200 and (await r.text()).startswith("BEGIN:VCALENDAR")))
                async with session.get(f"{base}/api/local_event_scout/calendar/wrong.ics") as r:
                    checks.append(("feed token", r.status == 404))
            finally:
                await server.stop()
                await host.stop()
    passed = all(p for _, p in checks)
    lines = [f"{'ok  ' if ok else 'FAIL'} {name}" for name, ok in checks]
    lines.append(f"SELF-TEST {'OK' if passed else 'FAILED'} {VERSION}")
    print("\n".join(lines))  # does nothing in the windowed .exe, hence --report
    if report:
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="LocalEventScout", description="Local Event Scout: AI-curated local events.")
    parser.add_argument("--data-dir", type=Path, default=None, help="where settings and results are kept")
    parser.add_argument("--port", type=int, default=None, help="port for the local page")
    parser.add_argument("--no-tray", action="store_true", help="run without a tray icon (Ctrl+C to stop)")
    parser.add_argument("--background", action="store_true", help="don't open the page on start (used by Start with Windows)")
    parser.add_argument("--self-test", action="store_true", help="start, check the page and API, and exit")
    parser.add_argument("--report", type=Path, default=None, help="with --self-test: also write the results to this file")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args(argv)

    if args.self_test:
        logging.basicConfig(level=logging.WARNING)
        try:
            return asyncio.run(self_test(args.report))
        except Exception as err:  # noqa: BLE001 - report anything to CI
            if args.report:
                args.report.write_text(f"SELF-TEST CRASHED: {err!r}\n", encoding="utf-8")
            raise

    data_dir = args.data_dir or default_data_dir()
    _setup_logging(data_dir, args.verbose)
    app = App(data_dir, args.port)

    if _port_in_use(app.port):
        # Already running (or another program has the port): show the page and leave.
        _LOGGER.info("Event Scout is already running; opening %s", app.url)
        if not args.background:
            app.open_page()
        return 0

    settings = AppSettings.load(data_dir)
    first_run = not settings.opened_once
    use_tray = not args.no_tray and sys.platform == "win32"

    def _open_when_ready() -> None:
        if app.ready.wait(30) and not args.background:
            app.open_page()
            if first_run:
                current = AppSettings.load(data_dir)
                current.opened_once = True
                current.save(data_dir)

    threading.Thread(target=_open_when_ready, daemon=True).start()

    if use_tray:
        from .tray import run_tray  # noqa: PLC0415

        worker = threading.Thread(target=_run_loop, args=(app,), daemon=True)
        worker.start()
        app.ready.wait(30)
        run_tray(open_page=app.open_page, search_now=app.search_now, check_updates=app.check_updates, quit_app=app.stop)
        worker.join(timeout=10)
        return 0

    try:
        _run_loop(app)
    except KeyboardInterrupt:
        pass
    return 0
