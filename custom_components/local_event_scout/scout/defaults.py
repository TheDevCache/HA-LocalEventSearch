"""Defaults shared by every host (Home Assistant, the Windows app, the CLI)."""

from __future__ import annotations

PROVIDER_OPENROUTER = "openrouter"
PROVIDER_GEMINI = "gemini"

DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"  # free tier, for reading pages and scoring
DEFAULT_GEMINI_SEARCH_MODEL = "gemini-3.1-flash-lite"  # paid project, cheapest grounded searches
GEMINI_FREE_SEARCHES = 5000  # free Google Search requests per month on a billing-enabled project
SEARCH_ENGINES = ["auto", "exa", "parallel", "native"]
FEEDBACK_VERDICTS = ["like", "dislike", "dismiss", "clear"]

# Skip scans when less than this much of the OpenRouter key limit is left (USD).
BUDGET_FLOOR = 0.02
