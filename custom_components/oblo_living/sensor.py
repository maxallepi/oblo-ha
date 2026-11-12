"""Sensor platform: creates one HA sensor per mapped cloud device service.

HA imports this module by name because PLATFORMS in __init__.py lists
"sensor" -- it must live at this path, not under entities/.
"""
import logging

from .const import DOMAIN
from .entities.sensor import ObloSensor
from .entity_map import build_entities

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Build sensor entities from the devices the coordinator already fetched."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities = []
    for ent in build_entities(coordinator.data or []):
        if ent["platform"] != "sensor":
            continue
        entities.append(ObloSensor(coordinator, ent))

    _LOGGER.debug("OBLO sensor platform: %d entities", len(entities))
    async_add_entities(entities)
