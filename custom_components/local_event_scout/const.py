"""Constants for Local Event Scout."""

from homeassistant.const import Platform

from .scout.defaults import (  # noqa: F401 - re-exported for the Home Assistant modules
    BUDGET_FLOOR,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GEMINI_SEARCH_MODEL,
    DEFAULT_MODEL,
    FEEDBACK_VERDICTS,
    GEMINI_FREE_SEARCHES,
    PROVIDER_GEMINI,
    PROVIDER_OPENROUTER,
    SEARCH_ENGINES,
)
from .scout.engine import VERSION  # noqa: F401 - one version number for every host

DOMAIN = "local_event_scout"
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.CALENDAR]

API_BASE = f"/api/{DOMAIN}"
API_STATE = f"{API_BASE}/config"
API_RUN = f"{API_BASE}/run"
API_FEEDBACK = f"{API_BASE}/feedback"
API_SOURCES = f"{API_BASE}/sources"
API_TEST_SEARCH = f"{API_BASE}/test_search"
API_REPORT = f"{API_BASE}/report"

PANEL_URL = DOMAIN
PANEL_COMPONENT = "local-event-scout-panel"
PANEL_STATIC = f"/{DOMAIN}_panel"

SIGNAL_UPDATE = f"{DOMAIN}_update"

CONF_PROVIDER = "provider"
CONF_GEMINI_SEARCH_KEY = "gemini_search_key"
CONF_GEMINI_KEY = "gemini_key"
