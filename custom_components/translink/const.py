"""Constants for the Translink integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "translink"

# The map view on translink.ca is backed by this undocumented JSON API. It
# serves the same GTFS-derived data the website renders, needs no API key, and
# sends `access-control-allow-origin: *`, so it is reachable from a poller.
API_BASE = "https://getaway.translink.ca/api"

# Translink publishes in Pacific time; all epoch values in the payload are
# absolute, but we need the zone to render scheduled clock times.
TIMEZONE = "America/Vancouver"

CACHE_VERSION = 1
CACHE_KEY = f"{DOMAIN}_http_cache"

# The server sends `cache-control: max-age=30` on real-time endpoints, so there
# is no point polling faster than this.
DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 15

DEFAULT_QUERY_SIZE = 5
MAX_QUERY_SIZE = 50

# Endpoint TTLs. Only the last two are hot; everything else is fetched once and
# served from `.storage` until it expires.
TTL_ROUTES = timedelta(days=1)
TTL_SCHEDULE = timedelta(hours=6)
TTL_SHAPES = timedelta(days=7)
TTL_ARRIVALS = timedelta(seconds=25)
TTL_POSITIONS = timedelta(seconds=10)
