"""System tray icon (Windows). Imported only when a tray is wanted."""

from __future__ import annotations

from collections.abc import Callable

import pystray

from .icon import icon_image


def run_tray(*, open_page: Callable[[], None], search_now: Callable[[], None], check_updates: Callable[[], None], quit_app: Callable[[], None]) -> None:
    """Blocks until Quit is chosen."""

    def _quit(icon: pystray.Icon, _item) -> None:
        icon.stop()
        quit_app()

    menu = pystray.Menu(
        pystray.MenuItem("Open Event Scout", lambda: open_page(), default=True),
        pystray.MenuItem("Search now", lambda: search_now()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Check for updates", lambda: check_updates()),
        pystray.MenuItem("Quit", _quit),
    )
    pystray.Icon("LocalEventScout", icon_image(), "Local Event Scout", menu).run()
