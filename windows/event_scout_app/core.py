"""Import the shared ``scout`` package.

In the packaged app it is bundled as a top-level package. When running from a
checkout it is loaded from the Home Assistant integration folder by path, because
putting that folder on ``sys.path`` would let its ``calendar.py`` shadow the
standard library.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

try:
    import scout  # noqa: F401 - packaged build
except ImportError:
    _PKG = Path(__file__).resolve().parents[2] / "custom_components" / "local_event_scout" / "scout"
    _spec = importlib.util.spec_from_file_location("scout", _PKG / "__init__.py", submodule_search_locations=[str(_PKG)])
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["scout"] = _module
    _spec.loader.exec_module(_module)

from scout.engine import VERSION, ScoutEngine, debug_info  # noqa: E402,F401

PANEL_JS = Path(__file__).resolve().parents[2] / "custom_components" / "local_event_scout" / "frontend" / "event-scout-panel.js"
