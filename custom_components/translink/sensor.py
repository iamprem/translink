"""Sensor platform for Translink."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MAX_BUS_SENSORS
from .coordinator import TranslinkCoordinator
from .translink_client import Arrival

ICON_LIVE = "mdi:bus"
ICON_TIMETABLE = "mdi:bus-clock"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the next-departure sensor and one sensor per upcoming bus."""
    coordinator: TranslinkCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [NextArrivalSensor(coordinator, entry)]
    for index in range(min(coordinator.query_size, MAX_BUS_SENSORS)):
        entities.append(BusArrivalSensor(coordinator, entry, index))
    async_add_entities(entities)


def _arrival_attributes(arrival: Arrival) -> dict[str, Any]:
    return {
        "trip_id": arrival.trip_id,
        "vehicle_number": arrival.vehicle_number,
        "scheduled_time": arrival.scheduled.isoformat(),
        "is_realtime": arrival.is_realtime,
        "delay_seconds": arrival.delay_seconds,
        "early_minutes": round(-arrival.delay_seconds / 60)
        if arrival.delay_seconds
        else 0,
        "timepoint": arrival.timepoint,
        "bay": arrival.bay,
        "pickup_only": arrival.pickup_only,
        "dropoff_only": arrival.dropoff_only,
        "node_only": arrival.node_only,
        "stop_code": arrival.stop_code,
        "stop_name": arrival.stop_name,
    }


class _BaseTranslinkSensor(SensorEntity):
    """Shared plumbing for the sensors built from one config entry."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = "min"

    def __init__(self, coordinator: TranslinkCoordinator, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        self._base = (
            f"{entry.data['route']}_{entry.data['direction']}_{entry.data['stop']}"
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def _arrival(self) -> Arrival | None:
        arrivals = self.coordinator.data or []
        return arrivals[0] if arrivals else None

    @staticmethod
    def _icon(arrival: Arrival | None) -> str:
        """Green bus = tracked vehicle, clock = timetable only."""
        if arrival is None:
            return ICON_TIMETABLE
        return ICON_LIVE if arrival.is_realtime else ICON_TIMETABLE

    @staticmethod
    def _icon_color(arrival: Arrival | None) -> str:
        if arrival is None:
            return "grey"
        return "green" if arrival.is_realtime else "orange"


class NextArrivalSensor(_BaseTranslinkSensor):
    """Minutes until the next bus, with the full upcoming list as attributes."""

    _attr_icon = ICON_TIMETABLE

    def __init__(self, coordinator: TranslinkCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = self._base
        self._attr_name = (
            f"{coordinator.stop_name} {coordinator.route} "
            f"direction {coordinator.direction}"
        )

    @property
    def native_value(self) -> int | None:
        arrival = self._arrival
        return arrival.minutes_away if arrival else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
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
            attributes["next_vehicle_number"] = next_arrival.vehicle_number
            attributes["headsign"] = next_arrival.headsign
            attributes["destination"] = next_arrival.destination
            attributes.update(
                {
                    f"next_{key}": value
                    for key, value in _arrival_attributes(next_arrival).items()
                    if key != "trip_id"
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


class BusArrivalSensor(_BaseTranslinkSensor):
    """The nth upcoming bus: its own entity, so dashboards need no templating.

    Without these, showing three colour-coded buses means a stack of Jinja
    template cards per stop -- which gets unwieldy past a couple of stops.
    """

    def __init__(
        self, coordinator: TranslinkCoordinator, entry: ConfigEntry, index: int
    ) -> None:
        super().__init__(coordinator, entry)
        self._index = index
        self._attr_unique_id = f"{self._base}_bus_{index + 1}"
        self._attr_name = (
            f"{coordinator.stop_name} {coordinator.route} "
            f"direction {coordinator.direction} bus {index + 1}"
        )

    @property
    def _arrival(self) -> Arrival | None:
        arrivals = self.coordinator.data or []
        if self._index < len(arrivals):
            return arrivals[self._index]
        return None

    @property
    def native_value(self) -> int | None:
        arrival = self._arrival
        return arrival.minutes_away if arrival else None

    @property
    def icon(self) -> str:
        return self._icon(self._arrival)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        arrival = self._arrival
        if arrival is None:
            return {"position": self._index + 1, "has_arrival": False}
        return {"position": self._index + 1, "has_arrival": True, **_arrival_attributes(arrival)}
