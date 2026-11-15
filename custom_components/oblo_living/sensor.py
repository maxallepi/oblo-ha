"""Sensor platform: creates one HA sensor per mapped cloud device service.

HA imports this module by name because PLATFORMS in __init__.py lists
"sensor" -- it must live at this path, not under entities/.
"""
import logging

from .const import DOMAIN
from .dynamic import async_setup_dynamic_platform
from .entities.sensor import ObloSensor

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Build sensors now and add any that appear on later refreshes."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    return async_setup_dynamic_platform(
        hass, entry, async_add_entities, "sensor",
        lambda ent: ObloSensor(coordinator, ent))