"""Data update coordinator for Translink."""

from __future__ import annotations

import inspect
import logging
from dataclasses import replace
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    TimestampDataUpdateCoordinator,
    UpdateFailed,
)

from .const import DEFAULT_QUERY_SIZE, DEFAULT_SCAN_INTERVAL, DOMAIN, MIN_SCAN_INTERVAL
from .translink_client import Arrival, TranslinkClient, TranslinkError

_LOGGER = logging.getLogger(__name__)


class TranslinkCoordinator(TimestampDataUpdateCoordinator[list[Arrival]]):
    """Poll one stop on one route and keep the arrivals list fresh."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, client: TranslinkClient, entry: ConfigEntry
    ) -> None:
        self.client = client
        self.route: str = entry.data["route"]
        self.direction: int = entry.data["direction"]
        self.stop: str = entry.data["stop"]
        self.stop_name: str = entry.data.get("stop_name", self.stop)
        self.query_size: int = entry.data.get("query_size", DEFAULT_QUERY_SIZE)

        interval = max(
            MIN_SCAN_INTERVAL, entry.data.get("scan_interval", DEFAULT_SCAN_INTERVAL)
        )

        # `config_entry` is only accepted by newer cores; custom components
        # should still load on older installs. Inspect rather than catching
        # TypeError, so __init__ never runs twice.
        kwargs: dict[str, object] = {
            "hass": hass,
            "logger": _LOGGER,
            "name": f"{DOMAIN}_{self.stop}_{self.route}_{self.direction}",
            "update_interval": timedelta(seconds=interval),
        }
        if "config_entry" in inspect.signature(
            TimestampDataUpdateCoordinator.__init__
        ).parameters:
            kwargs["config_entry"] = entry

        super().__init__(**kwargs)

    async def _async_update_data(self) -> list[Arrival]:
        try:
            arrivals = await self.client.get_arrivals(
                stop=self.stop,
                route=self.route,
                query_size=self.query_size,
                direction=self.direction,
            )
        except TranslinkError as err:
            raise UpdateFailed(f"Translink API error: {err}") from err

        arrivals = await self._attach_vehicle_numbers(arrivals)

        # Drop departures that have already gone. The API can briefly return a
        # stale first entry right after it passes.
        now = datetime.now().astimezone()
        return [a for a in arrivals if a.arrival > now]

    async def _attach_vehicle_numbers(self, arrivals: list[Arrival]) -> list[Arrival]:
        """Map each trip to the bus actually running it.

        This is the join the website does: vehiclepositions is tiny (~700
        bytes) and keyed by trip id. If it fails the ETAs are still good, so
        the error is swallowed rather than failing the whole update.
        """
        try:
            vehicles = await self.client.get_vehicle_positions(
                self.route, self.direction
            )
        except TranslinkError as err:
            _LOGGER.debug("Could not fetch vehicle positions: %s", err)
            return arrivals

        by_trip = {v.trip_id: v.vehicle_id for v in vehicles if v.trip_id}
        if not by_trip:
            return arrivals
        return [
            replace(a, vehicle_number=by_trip.get(a.trip_id)) for a in arrivals
        ]
