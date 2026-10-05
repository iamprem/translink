"""The Translink integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.storage import Store

from .const import CACHE_KEY, CACHE_VERSION, DOMAIN
from .coordinator import TranslinkCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Set up the shared HTTP session and on-disk response cache."""
    if DOMAIN in hass.data:
        return True

    store: Store = Store(hass, CACHE_VERSION, CACHE_KEY)
    cached = await store.async_load() or {}

    async def _save() -> None:
        await store.async_save(cached)

    hass.data[DOMAIN] = {
        # HA's shared client. Building an httpx.AsyncClient inline loads the
        # CA bundle, which is a blocking call and trips HA's event-loop
        # guard. This client is closed by HA on shutdown, not by us.
        "session": get_async_client(hass),
        "cache": cached,
        "save": _save,
    }
    return True


async def async_shutdown(hass: HomeAssistant) -> None:
    """Flush the cache. The HTTP session is owned by Home Assistant."""
    data = hass.data.get(DOMAIN)
    if data:
        await data["save"]()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one stop from a config entry."""
    await async_setup(hass, {})

    client_session = hass.data[DOMAIN]["session"]
    cache = hass.data[DOMAIN]["cache"]

    # The client is constructed per entry so each one sees the same cache dict;
    # cheap, and it keeps the constructor free of Home Assistant imports.
    from .translink_client import TranslinkClient

    client = TranslinkClient(client_session, cache)

    coordinator = TranslinkCoordinator(hass, client, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
