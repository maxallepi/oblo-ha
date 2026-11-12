"""OBLO Living integration setup."""
import logging
import json

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, CONF_IDENTITY, CONF_SECRET
from .cloud_api import ObloCloudApi
from .coordinator import ObloDataCoordinator
from .hub import ObloHub

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["binary_sensor", "sensor", "switch"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up OBLO Living from a config entry (cloud mode)."""
    # build cloud API from stored credentials
    session = async_get_clientsession(hass)
    api = ObloCloudApi(session, entry.data[CONF_IDENTITY], entry.data[CONF_SECRET])

    # restore cached session if present (avoids one login round-trip)
    if entry.data.get("session_id"):
        api.session_id = entry.data["session_id"]
        api.user_id = entry.data["user_id"]

    # first login + device fetch synchronously before platforms load
    await api.async_login()
    _LOGGER.info("cloud login ok: user_id=%s session_id=%s", api.user_id, api.session_id)

    # coordinator for periodic polling
    coordinator = ObloDataCoordinator(hass, api)
    await coordinator.async_config_entry_first_refresh()
    _LOGGER.info("first refresh fetched %d devices", len(coordinator.devices))

    # build hub from normalised devices
    hub = ObloHub()
    hub.load_from_normalised(coordinator.devices)

    # stash for platforms
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "hub": hub,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
