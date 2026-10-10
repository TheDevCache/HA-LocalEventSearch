"""Authenticated HTTP endpoints used by the sidebar panel."""

from __future__ import annotations

from http import HTTPStatus
import json

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .const import API_CALENDAR, API_DEBUG, API_DISCOVER, API_SETUP, API_FEEDBACK, API_REPORT, API_RUN, API_SOURCES, API_STATE, API_TEST_SEARCH, DOMAIN, FEEDBACK_VERDICTS


def _coordinator(hass: HomeAssistant):
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    return None


class _ScoutView(HomeAssistantView):
    requires_auth = True

    def _get(self, request: web.Request):
        coordinator = _coordinator(request.app["hass"])
        if coordinator is None:
            raise web.HTTPServiceUnavailable(text="Local Event Scout is not set up or still starting.")
        return coordinator

    @staticmethod
    def _require_admin(request: web.Request) -> None:
        user = request.get("hass_user")
        if user is None or not user.is_admin:
            raise web.HTTPForbidden(text="Only administrators can change Event Scout.")

    @staticmethod
    async def _body(request: web.Request) -> dict:
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError) as err:
            raise web.HTTPBadRequest(text="Request body must be JSON.") from err
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="Request body must be a JSON object.")
        return body


class EventScoutStateView(_ScoutView):
    """GET current state; POST updated settings."""

    url = API_STATE
    name = f"api:{DOMAIN}:config"

    async def get(self, request: web.Request) -> web.Response:
        return self.json(self._get(request).public_state())

    async def post(self, request: web.Request) -> web.Response:
        self._require_admin(request)
        coordinator = self._get(request)
        try:
            state = await coordinator.async_save_config(await self._body(request))
        except ValueError as err:
            return self.json_message(str(err), HTTPStatus.BAD_REQUEST)
        return self.json(state)


class EventScoutRunView(_ScoutView):
    """Start a scan in the background and return immediately."""

    url = API_RUN
    name = f"api:{DOMAIN}:run"

    async def post(self, request: web.Request) -> web.Response:
        self._require_admin(request)
        coordinator = self._get(request)
        body = await self._body(request) if request.can_read_body else {}
        try:
            state = coordinator.async_request_scan(force=bool(body.get("force", True)))
        except ValueError as err:
            return self.json_message(str(err), HTTPStatus.BAD_REQUEST)
        return self.json(state)


class EventScoutFeedbackView(_ScoutView):
    """Record like / dislike / dismiss for an event."""

    url = API_FEEDBACK
    name = f"api:{DOMAIN}:feedback"

    async def post(self, request: web.Request) -> web.Response:
        coordinator = self._get(request)
        body = await self._body(request)
        verdict = body.get("verdict")
        if verdict not in FEEDBACK_VERDICTS:
            return self.json_message(f"verdict must be one of {FEEDBACK_VERDICTS}", HTTPStatus.BAD_REQUEST)
        try:
            state = await coordinator.async_set_feedback(str(body.get("id")), verdict)
        except KeyError:
            return self.json_message("Event not found (it may have expired).", HTTPStatus.NOT_FOUND)
        return self.json(state)


class EventScoutReportView(_ScoutView):
    """Report wrong details on an event (or undo a report)."""

    url = API_REPORT
    name = f"api:{DOMAIN}:report"

    async def post(self, request: web.Request) -> web.Response:
        coordinator = self._get(request)
        body = await self._body(request)
        try:
            state = await coordinator.async_report(str(body.get("id")), body)
        except KeyError:
            return self.json_message("Event not found (it may have expired).", HTTPStatus.NOT_FOUND)
        except ValueError as err:
            return self.json_message(str(err), HTTPStatus.BAD_REQUEST)
        return self.json(state)


class EventScoutSourcesView(_ScoutView):
    """Add, update or remove a saved source page."""

    url = API_SOURCES
    name = f"api:{DOMAIN}:sources"

    async def post(self, request: web.Request) -> web.Response:
        self._require_admin(request)
        coordinator = self._get(request)
        try:
            state = await coordinator.async_source_action(await self._body(request))
        except ValueError as err:
            return self.json_message(str(err), HTTPStatus.BAD_REQUEST)
        except KeyError:
            return self.json_message("That source is not saved.", HTTPStatus.NOT_FOUND)
        return self.json(state)


class EventScoutTestSearchView(_ScoutView):
    """Run one tiny web search and report whether the provider really searched."""

    url = API_TEST_SEARCH
    name = f"api:{DOMAIN}:test_search"

    async def post(self, request: web.Request) -> web.Response:
        self._require_admin(request)
        return self.json(await self._get(request).async_test_search())


class EventScoutDebugView(_ScoutView):
    """Settings, recent runs and errors for a bug report (no API keys)."""

    url = API_DEBUG
    name = f"api:{DOMAIN}:debug"

    async def get(self, request: web.Request) -> web.Response:
        self._require_admin(request)
        return self.json(self._get(request).debug_info())


class EventScoutCalendarFeedView(_ScoutView):
    """The .ics feed. Calendar apps can't log in, so the link carries a secret token instead."""

    url = API_CALENDAR + "/{token}.ics"
    name = f"api:{DOMAIN}:calendar_feed"
    requires_auth = False

    async def get(self, request: web.Request, token: str) -> web.Response:
        body = self._get(request).engine.calendar_feed(token, request.query.get("only", ""))
        if body is None:
            raise web.HTTPNotFound
        return web.Response(text=body, content_type="text/calendar", charset="utf-8", headers={"Cache-Control": "no-cache"})


class EventScoutEventIcsView(_ScoutView):
    """One event as an .ics file, for "Add to calendar"."""

    url = API_CALENDAR + "/{token}/{event_id}.ics"
    name = f"api:{DOMAIN}:calendar_event"
    requires_auth = False

    async def get(self, request: web.Request, token: str, event_id: str) -> web.Response:
        body = self._get(request).engine.event_ics(token, event_id)
        if body is None:
            raise web.HTTPNotFound
        return web.Response(
            text=body,
            content_type="text/calendar",
            charset="utf-8",
            headers={"Content-Disposition": 'attachment; filename="event.ics"'},
        )


class EventScoutCalendarTokenView(_ScoutView):
    """Make a new calendar feed link (the old one stops working)."""

    url = API_CALENDAR + "/new_link"
    name = f"api:{DOMAIN}:calendar_token"

    async def post(self, request: web.Request) -> web.Response:
        self._require_admin(request)
        return self.json(await self._get(request).async_new_calendar_token())


class EventScoutDiscoverView(_ScoutView):
    """Search for the town's event calendars (one web search)."""

    url = API_DISCOVER
    name = f"api:{DOMAIN}:discover"

    async def post(self, request: web.Request) -> web.Response:
        self._require_admin(request)
        try:
            return self.json(await self._get(request).async_discover())
        except ValueError as err:
            return self.json_message(str(err), HTTPStatus.BAD_REQUEST)


class EventScoutLocateView(_ScoutView):
    """Suggest the user's town from Home Assistant's home location."""

    url = API_SETUP + "/locate"
    name = f"api:{DOMAIN}:setup_locate"

    async def get(self, request: web.Request) -> web.Response:
        self._require_admin(request)
        try:
            return self.json(await self._get(request).async_suggest_location())
        except ValueError as err:
            return self.json_message(str(err), HTTPStatus.BAD_REQUEST)


def async_register_views(hass: HomeAssistant) -> None:
    hass.http.register_view(EventScoutLocateView)
    hass.http.register_view(EventScoutDiscoverView)
    hass.http.register_view(EventScoutDebugView)
    hass.http.register_view(EventScoutCalendarTokenView)
    hass.http.register_view(EventScoutCalendarFeedView)
    hass.http.register_view(EventScoutEventIcsView)
    hass.http.register_view(EventScoutTestSearchView)
    hass.http.register_view(EventScoutSourcesView)
    hass.http.register_view(EventScoutStateView)
    hass.http.register_view(EventScoutRunView)
    hass.http.register_view(EventScoutFeedbackView)
    hass.http.register_view(EventScoutReportView)
