"""OBLO Living integration setup."""
import logging
import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, CONF_IDENTITY, CONF_SECRET
from .cloud_api import ObloCloudApi
from .coordinator import ObloDataCoordinator
from .hub import ObloHub

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["binary_sensor", "sensor"]  # switch/light need the cloud command API (not yet)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up OBLO Living from a config entry (cloud mode)."""
    # build cloud API from stored credentials. CONF_SECRET holds md5(password)
    # (32 hex chars) for entries created by the current config flow; legacy
    # plaintext entries are detected and hashed on the fly.
    session = async_get_clientsession(hass)
    secret = entry.data[CONF_SECRET]
    api = ObloCloudApi(
        session, entry.data[CONF_IDENTITY], secret,
        secret_hashed=bool(re.fullmatch(r"[0-9a-f]{32}", secret or "")))

    # restore cached session if present (avoids one login round-trip);
    # the coordinator re-authenticates itself on the first auth failure
    if entry.data.get("session_id"):
        api.session_id = entry.data["session_id"]
        api.user_id = entry.data["user_id"]

    # coordinator for periodic polling (re-login + fetch happens inside)
    coordinator = ObloDataCoordinator(hass, api)
    await coordinator.async_config_entry_first_refresh()
    _LOGGER.info("cloud session=%s devices=%d", api.session_id, len(coordinator.devices))

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
