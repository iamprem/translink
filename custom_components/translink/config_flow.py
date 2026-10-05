"""Config flow for Translink."""

from __future__ import annotations

import logging
from typing import Any

import httpx
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DEFAULT_QUERY_SIZE, DOMAIN, MAX_QUERY_SIZE
from .translink_client import TranslinkClient, TranslinkError, parse_route_directions

_LOGGER = logging.getLogger(__name__)


def _client(hass) -> TranslinkClient:
    """Build a throwaway client sharing the integration's cache and session."""
    data = hass.data.setdefault(DOMAIN, {})
    session = data.get("session")
    if session is None:
        session = httpx.AsyncClient(
            headers={"Accept-Encoding": "gzip"}, timeout=15, follow_redirects=True
        )
        data["session"] = session
    return TranslinkClient(session, data.setdefault("cache", {}))


class TranslinkBusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Walk the user through route -> direction -> stop."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._stop_names: dict[str, str] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a route."""
        errors: dict[str, str] = {}
        client = _client(self.hass)

        if user_input is not None:
            self._data["route"] = user_input["route"]
            return await self.async_step_direction()

        try:
            routes = await client.get_routes()
        except TranslinkError as err:
            _LOGGER.error("Could not load Translink routes: %s", err)
            return self.async_show_form(
                step_id="user", errors={"base": "cannot_connect"}
            )

        options = {
            str(route["routeNumber"]): (
                f"{route['routeNumber']} {route.get('routeDescription', '')}".strip()
            )
            for route in routes
            if route.get("routeNumber")
        }
        if not options:
            errors["base"] = "no_routes"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("route"): vol.In(options)}),
            errors=errors,
        )

    async def async_step_direction(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a direction of travel."""
        errors: dict[str, str] = {}
        client = _client(self.hass)
        route = self._data["route"]

        if user_input is not None:
            self._data["direction"] = int(user_input["direction"])
            return await self.async_step_stop()

        try:
            schedule = await client.get_route_schedule(route, 1)
            directions = parse_route_directions(schedule)
        except TranslinkError as err:
            _LOGGER.error("Could not load directions for route %s: %s", route, err)
            return self.async_show_form(
                step_id="direction", errors={"base": "cannot_connect"}
            )

        options = {
            str(d.get("di")): f"{d.get('dn', '')} ({d.get('cd', '')})".strip()
            for d in directions
            if d.get("di") is not None
        }
        if not options:
            errors["base"] = "no_directions"

        return self.async_show_form(
            step_id="direction",
            data_schema=vol.Schema({vol.Required("direction"): vol.In(options)}),
            errors=errors,
            description_placeholders={"route": route},
        )

    async def async_step_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the stop to watch, then confirm the details."""
        errors: dict[str, str] = {}
        client = _client(self.hass)
        route = self._data["route"]
        direction = self._data["direction"]

        if user_input is not None:
            stop = user_input["stop"]
            self._data["stop"] = stop
            # vol.In hands back the stop *code*; map it to the real stop name so
            # the entity is named after the stop, not a bare number.
            self._data["stop_name"] = self._stop_names.get(stop, stop)
            self._data["query_size"] = int(user_input.get("query_size", DEFAULT_QUERY_SIZE))

            unique = f"{route}_{direction}_{self._data['stop']}"
            await self.async_set_unique_id(unique)
            self._abort_if_unique_id_configured()

            title = (
                f"{route} direction {direction} at "
                f"{self._data['stop_name']}"
            )
            return self.async_create_entry(title=title, data=self._data)

        try:
            stops = await client.get_route_stops(route, direction)
        except TranslinkError as err:
            _LOGGER.error("Could not load stops for route %s: %s", route, err)
            return self.async_show_form(step_id="stop", errors={"base": "cannot_connect"})

        # Node-only and drop-off-only stops are never boarded, so offering them
        # would create a sensor that can never produce a departure.
        boardable = [s for s in stops if s.code and not s.name.lower().startswith("bay")]
        self._stop_names = {s.code: s.name for s in boardable}
        options = {s.code: f"{s.name} ({s.code})" for s in boardable}
        if not options:
            errors["base"] = "no_stops"

        return self.async_show_form(
            step_id="stop",
            data_schema=vol.Schema(
                {
                    vol.Required("stop"): vol.In(options),
                    vol.Optional("query_size", default=DEFAULT_QUERY_SIZE): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=MAX_QUERY_SIZE)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"route": route, "direction": direction},
        )
