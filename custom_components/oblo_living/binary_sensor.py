"""Binary sensor platform: one HA binary sensor per mapped service property.

HA imports this module by name because PLATFORMS in __init__.py lists
"binary_sensor" -- it must live at this path, not under entities/.
"""
import logging

from .const import DOMAIN
from .dynamic import async_setup_dynamic_platform
from .entities.binary_sensor import ObloBinarySensor

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Build binary sensors now and add any that appear on later refreshes."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    return async_setup_dynamic_platform(
        hass, entry, async_add_entities, "binary_sensor",
        lambda ent: ObloBinarySensor(coordinator, ent))