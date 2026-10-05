"""Data update coordinator for Translink."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_QUERY_SIZE, DEFAULT_SCAN_INTERVAL, DOMAIN, MIN_SCAN_INTERVAL
from .translink_client import Arrival, TranslinkClient, TranslinkError

_LOGGER = logging.getLogger(__name__)


class TranslinkCoordinator(DataUpdateCoordinator[list[Arrival]]):
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

        # `config_entry` was added to DataUpdateCoordinator in a later core
        # release; custom components should load on older installs too.
        coordinator_args = {
            "hass": hass,
            "logger": _LOGGER,
            "name": f"{DOMAIN}_{self.stop}_{self.route}_{self.direction}",
            "update_interval": timedelta(seconds=interval),
        }
        try:
            super().__init__(**coordinator_args, config_entry=entry)
        except TypeError:
            super().__init__(**coordinator_args)

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

        # Drop departures that have already gone. The API can briefly return a
        # stale first entry right after it passes.
        now = datetime.now().astimezone()
        return [a for a in arrivals if a.arrival > now]
