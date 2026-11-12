"""OBLO Living integration setup."""
import logging

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
    # build cloud API from stored credentials. CONF_SECRET holds the PLAINTEXT
    # password (v2 entries). v1 entries (stored md5) are migrated via
    # async_migrate_entry and forced to reauth, so only plaintext reaches here.
    session = async_get_clientsession(hass)
    secret = entry.data[CONF_SECRET]
    api = ObloCloudApi(session, entry.data[CONF_IDENTITY], secret)

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


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate a v1 entry (stored md5(password)) to v2 (plaintext password).

    The md5 hash cannot be turned back into the password, and the cloud would
    hash it a second time, so the only honest migration is to mark the entry
    v2 and send the user through reauth once to re-enter the password.

    NOTE: HA looks for async_migrate_entry on the integration __init__ module
    (config_entries.py: `hasattr(component, "async_migrate_entry")`), NOT on
    config_flow. This must live here or version migration silently fails.
    """
    if config_entry.version > 2:
        return False           # written by a newer version -- leave it alone
    if config_entry.version == 1:
        _LOGGER.info("migrating OBLO entry %s from v1 to v2 (reauth required)",
                     config_entry.entry_id)
        hass.config_entries.async_update_entry(config_entry, version=2,
                                               minor_version=1)
        entry = hass.config_entries.async_get_entry(config_entry.entry_id)
        if entry is not None and hasattr(entry, "async_start_reauth"):
            entry.async_start_reauth(hass)
        else:
            _LOGGER.warning(
                "could not start reauth for %s; remove and re-add the "
                "integration", config_entry.entry_id)
    return True
