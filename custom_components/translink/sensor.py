"""Sensor platform for Translink."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import TranslinkCoordinator
from .translink_client import Arrival


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the next-departure sensor from a config entry."""
    coordinator: TranslinkCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([NextArrivalSensor(coordinator, entry)])


def _arrival_attributes(arrival: Arrival) -> dict[str, Any]:
    return {
        "trip_id": arrival.trip_id,
        "scheduled_time": arrival.scheduled.isoformat(),
        "is_realtime": arrival.is_realtime,
        "delay_seconds": arrival.delay_seconds,
        "early_minutes": round(-arrival.delay_seconds / 60) if arrival.delay_seconds else 0,
        "timepoint": arrival.timepoint,
        "bay": arrival.bay,
        "pickup_only": arrival.pickup_only,
        "dropoff_only": arrival.dropoff_only,
        "node_only": arrival.node_only,
    }


class NextArrivalSensor(SensorEntity):
    """Minutes until the next bus, with the full upcoming list as attributes."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = "min"
    _attr_should_poll = False

    def __init__(self, coordinator: TranslinkCoordinator, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.data['route']}_{entry.data['direction']}_{entry.data['stop']}"
        self._attr_name = (
            f"{coordinator.stop_name} {coordinator.route} "
            f"direction {coordinator.direction}"
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Available whenever the last poll succeeded, even with no buses."""
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> int | None:
        """Minutes until the next departure."""
        arrivals = self.coordinator.data
        if not arrivals:
            return None
        return arrivals[0].minutes_away

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Everything a dashboard or automation needs about this stop."""
        arrivals: list[Arrival] = self.coordinator.data or []
        coordinator = self.coordinator
        attributes: dict[str, Any] = {
            "stop_code": coordinator.stop,
            "stop_name": coordinator.stop_name,
            "route": coordinator.route,
            "direction": coordinator.direction,
            "last_updated": (
                coordinator.last_update_success_time.isoformat()
                if coordinator.last_update_success_time
                else None
            ),
            "upcoming_count": len(arrivals),
        }

        if arrivals:
            next_arrival = arrivals[0]
            attributes["next_arrival"] = next_arrival.arrival.isoformat()
            attributes["next_scheduled"] = next_arrival.scheduled.isoformat()
            attributes["next_is_realtime"] = next_arrival.is_realtime
            attributes["next_delay_seconds"] = next_arrival.delay_seconds
            attributes["headsign"] = next_arrival.headsign
            attributes["destination"] = next_arrival.destination
            attributes.update(
                {
                    f"next_{key}": value
                    for key, value in _arrival_attributes(next_arrival).items()
                    if key not in ("trip_id",)
                }
            )

        attributes["upcoming"] = [
            {
                "arrival": a.arrival.isoformat(),
                "minutes_away": a.minutes_away,
                **_arrival_attributes(a),
            }
            for a in arrivals
        ]
        return attributes
