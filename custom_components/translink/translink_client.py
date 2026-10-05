"""Async client for the Translink GTFS JSON API.

This module deliberately avoids importing Home Assistant so the request and
parsing logic can be unit tested with plain pytest on a machine that has no HA
install. `translink_client.fetch_with_cache` takes an injected session.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .const import (
    API_BASE,
    TTL_ARRIVALS,
    TTL_POSITIONS,
    TTL_ROUTES,
    TTL_SCHEDULE,
    TTL_SHAPES,
    TIMEZONE,
)

_LOGGER = logging.getLogger(__name__)

TZ = ZoneInfo(TIMEZONE)


class TranslinkError(Exception):
    """Raised when the API is unreachable, or answers with something unusable."""


@dataclass(frozen=True)
class Arrival:
    """One upcoming departure at a stop."""

    trip_id: str
    route: str
    headsign: str
    destination: str
    direction: int
    arrival: datetime
    scheduled: datetime
    is_realtime: bool
    delay_seconds: int
    timepoint: bool
    bay: str | None
    pickup_only: bool
    dropoff_only: bool
    node_only: bool
    stop_code: str
    stop_name: str

    @property
    def minutes_away(self) -> int:
        delta = self.arrival - datetime.now(TZ)
        return max(0, round(delta.total_seconds() / 60))


@dataclass(frozen=True)
class Vehicle:
    """A bus currently reporting a GPS position."""

    vehicle_id: str
    trip_id: str
    pattern_id: str
    headsign: str
    latitude: float
    longitude: float
    timestamp: datetime


@dataclass(frozen=True)
class Stop:
    """A stop served by a route in one direction."""

    code: str
    name: str
    latitude: float | None
    longitude: float | None
    sequence: int


def _epoch(value: Any) -> datetime | None:
    """Convert a GTFS epoch-seconds field to an aware datetime."""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def parse_arrivals(
    payload: Any, *, route: str, direction: int | None = None
) -> list[Arrival]:
    """Flatten the nested realtimeschedules payload into a list of Arrival.

    The endpoint returns `stops[].r[].t[]`; each leaf is one departure of one
    route at one stop. `ut` is already the *predicted* arrival when a prediction
    exists, which is why it -- not `dt` -- is what we schedule on.
    """
    if not isinstance(payload, list):
        raise TranslinkError("Expected a list of stops")

    arrivals: list[Arrival] = []
    for stop in payload:
        if not isinstance(stop, dict):
            continue
        stop_code = str(stop.get("sc") or "")
        stop_name = str(stop.get("sn") or "")
        for route_block in stop.get("r") or []:
            if not isinstance(route_block, dict):
                continue
            block_route = str(route_block.get("rs") or route)
            block_direction = route_block.get("di")
            if block_direction is None:
                block_direction = direction
            destination = str(route_block.get("dn") or "")
            for trip in route_block.get("t") or []:
                if not isinstance(trip, dict):
                    continue
                arrival = _epoch(trip.get("ut"))
                if arrival is None:
                    continue
                delay_seconds = int(trip.get("dl") or 0)
                # This endpoint carries only the predicted time. The timetable
                # time is recovered by undoing the reported offset, which is
                # signed seconds (negative = running early).
                scheduled = arrival - timedelta(seconds=delay_seconds)
                arrivals.append(
                    Arrival(
                        trip_id=str(trip.get("ti") or ""),
                        route=block_route,
                        headsign=str(trip.get("th") or route_block.get("hs") or ""),
                        destination=destination,
                        direction=int(block_direction),
                        arrival=arrival,
                        scheduled=scheduled,
                        # `rt` marks a real-time prediction; `dl` is signed
                        # seconds relative to the timetable and is 0 without one.
                        is_realtime=bool(trip.get("rt")),
                        delay_seconds=delay_seconds,
                        timepoint=bool(trip.get("tp")),
                        bay=(str(trip["ba"]) if trip.get("ba") else None),
                        pickup_only=bool(trip.get("po")),
                        dropoff_only=bool(trip.get("do")),
                        node_only=bool(trip.get("no")),
                        stop_code=stop_code,
                        stop_name=stop_name,
                    )
                )

    # API order is already chronological, but do not trust it: `dl` shifts
    # predicted times and the list is not re-sorted server side.
    arrivals.sort(key=lambda a: a.arrival)
    return arrivals


def parse_vehicle_positions(payload: Any) -> list[Vehicle]:
    """Flatten the vehiclepositions payload. There is no next-stop field."""
    if not isinstance(payload, list):
        raise TranslinkError("Expected a list of vehicles")

    vehicles: list[Vehicle] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            latitude = float(item["la"])
            longitude = float(item["ln"])
        except (KeyError, TypeError, ValueError):
            continue
        timestamp = _epoch(item.get("ts"))
        if timestamp is None:
            continue
        vehicles.append(
            Vehicle(
                vehicle_id=str(item.get("vi") or ""),
                trip_id=str(item.get("ti") or ""),
                pattern_id=str(item.get("p") or ""),
                headsign=str(item.get("th") or ""),
                latitude=latitude,
                longitude=longitude,
                timestamp=timestamp,
            )
        )
    return vehicles


def parse_route_stops(payload: Any) -> list[Stop]:
    """Pull the ordered stop list out of a route schedule document."""
    if not isinstance(payload, dict):
        raise TranslinkError("Expected a schedule object")

    stops: list[Stop] = []
    for sequence, stop in enumerate(payload.get("s") or []):
        if not isinstance(stop, dict):
            continue
        stops.append(
            Stop(
                code=str(stop.get("sc") or ""),
                name=str(stop.get("sn") or ""),
                latitude=_maybe_float(stop.get("la")),
                longitude=_maybe_float(stop.get("lo")),
                sequence=sequence,
            )
        )
    return stops


def parse_route_directions(payload: Any) -> list[dict[str, Any]]:
    """Read the direction blocks from a schedule document."""
    if not isinstance(payload, dict):
        raise TranslinkError("Expected a schedule object")
    return [d for d in (payload.get("d") or []) if isinstance(d, dict)]


def _maybe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class TranslinkClient:
    """Thin caching wrapper over the endpoints the website itself calls."""

    def __init__(self, session: Any, cache: Any | None = None) -> None:
        self._session = session
        self._cache = cache if cache is not None else {}

    # -- raw fetch ---------------------------------------------------------

    async def _get(
        self, path: str, params: dict[str, Any] | None = None, *, ttl: timedelta
    ) -> Any:
        url = f"{API_BASE}{path}"
        key = url if not params else f"{url}?{sorted(params.items())}"
        now = datetime.now(timezone.utc)

        cached = self._cache.get(key)
        if cached and cached["expires"] > now:
            return cached["data"]

        headers = {}
        if cached and cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]

        try:
            response = await self._session.get(
                url, params=params, headers=headers, timeout=15
            )
        except asyncio.TimeoutError as err:
            # Serve stale rather than dropping the sensor to unavailable: a
            # bus prediction that is 90 seconds old beats no prediction.
            if cached:
                _LOGGER.debug("Timeout for %s, serving stale cache", url)
                return cached["data"]
            raise TranslinkError(f"Timeout requesting {url}") from err
        except Exception as err:  # noqa: BLE001 - httpx raises many types
            if cached:
                _LOGGER.debug("Error for %s, serving stale cache: %s", url, err)
                return cached["data"]
            raise TranslinkError(f"Error requesting {url}: {err}") from err

        if response.status_code == 304 and cached:
            self._cache[key] = {**cached, "expires": now + ttl}
            return cached["data"]

        if response.status_code == 404:
            raise TranslinkError(f"Not found: {url}")
        if response.status_code >= 400:
            raise TranslinkError(f"HTTP {response.status_code} for {url}")

        try:
            data = response.json()
        except ValueError as err:
            raise TranslinkError(f"Non-JSON response from {url}") from err

        self._cache[key] = {
            "data": data,
            "expires": now + ttl,
            "etag": response.headers.get("etag"),
        }
        return data

    # -- endpoints ---------------------------------------------------------

    async def get_routes(self) -> list[dict[str, Any]]:
        """Every route in the system (238 of them), for the config flow."""
        payload = await self._get("/gtfs/routes", ttl=TTL_ROUTES)
        return [r for r in payload if isinstance(r, dict)] if isinstance(payload, list) else []

    async def get_route_schedule(
        self, route: str, direction: int, day: date | None = None
    ) -> Any:
        """Static schedule: the stop list plus every scheduled departure.

        This is the heaviest endpoint the site uses (~375 KB raw, ~42 KB
        gzipped), which is why it carries a long TTL and ETag revalidation.
        """
        day = day or datetime.now(TZ).date()
        return await self._get(
            f"/gtfs/route/{route}/direction/{direction}/schedules/{day.isoformat()}",
            ttl=TTL_SCHEDULE,
        )

    async def get_route_stops(self, route: str, direction: int) -> list[Stop]:
        return parse_route_stops(await self.get_route_schedule(route, direction))

    async def get_shapes(self, route: str, direction: int) -> Any:
        return await self._get(
            f"/gtfs/route/{route}/direction/{direction}/shapes", ttl=TTL_SHAPES
        )

    async def get_arrivals(
        self,
        stop: str,
        route: str,
        query_size: int = 5,
        direction: int | None = None,
    ) -> list[Arrival]:
        """Upcoming departures at one stop for one route. The hot path.

        `route` is mandatory: omitting it returns an empty list rather than an
        error, which is an easy trap to fall into.
        """
        payload = await self._get(
            f"/gtfs/stop/{stop}/route/{route}/realtimeschedules",
            params={"querySize": str(query_size)},
            ttl=TTL_ARRIVALS,
        )
        # The payload carries its own direction per route block, so the
        # configured direction is only a fallback for malformed responses.
        return parse_arrivals(payload, route=route, direction=direction)

    async def get_vehicle_positions(self, route: str, direction: int) -> list[Vehicle]:
        payload = await self._get(
            f"/gtfs/route/{route}/direction/{direction}/vehiclepositions",
            ttl=TTL_POSITIONS,
        )
        return parse_vehicle_positions(payload)
