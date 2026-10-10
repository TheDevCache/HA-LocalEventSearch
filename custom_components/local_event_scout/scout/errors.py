"""Turn provider failures into messages that say what to do next."""

from __future__ import annotations

from .openrouter import OpenRouterAuthError

# Hosts replace this with where their keys are changed.
DEFAULT_KEY_HINT = "Change the key in Event Scout's provider settings."


def explain(err: Exception, *, provider: str = "", model: str = "", key_hint: str = DEFAULT_KEY_HINT) -> str:
    """A user-facing message for ``err``: the original text plus the most likely fix."""
    text = str(err).strip() or err.__class__.__name__
    low = text.lower()

    def _add(advice: str) -> str:
        return f"{text.rstrip('.')}. {advice}"

    if isinstance(err, OpenRouterAuthError) or "rejected the api key" in low or "api key not valid" in low:
        return _add(f"The key may be mistyped, deleted or expired. {key_hint}")
    if "http 402" in low or "no credits" in low or "spending limit" in low:
        return _add("Raise the key's limit or add credit at openrouter.ai/settings/keys; searches resume on the next run.")
    if "billing" in low or ("google search" in low and "not available" in low) or ("grounding" in low and "free" in low):
        return _add("Google Search only works with a key from a project that has billing turned on (it includes 5,000 free searches a month).")
    if (
        "not a valid model" in low
        or "no endpoints found" in low
        or ("model" in low and "not found" in low)
        or ("http 404" in low and provider == "gemini")
    ):
        name = f" “{model}”" if model else ""
        return _add(f"The model name{name} wasn't recognised. Check the model fields in Search options.")
    if "quota" in low or "rate limit" in low or "http 429" in low:
        extra = (
            " Free-tier Gemini limits reset daily (midnight Pacific time)."
            if provider == "gemini"
            else ""
        )
        return _add(f"The service is limiting requests. It will be tried again later.{extra}")
    if "took too long" in low or "could not reach" in low:
        return _add("Check that this computer is online. It will be tried again later.")
    if "answered without running any google searches" in low:
        return text  # already says what to do
    if "not valid json" in low:
        return _add("The model gave a garbled answer twice. If this keeps happening, try a different model.")
    return text
