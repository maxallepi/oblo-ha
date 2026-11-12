"""Binary sensor platform: one HA binary sensor per mapped service property.

HA imports this module by name because PLATFORMS in __init__.py lists
"binary_sensor" -- it must live at this path, not under entities/.
"""
import logging

from .const import DOMAIN
from .entities.binary_sensor import ObloBinarySensor
from .entity_map import build_entities

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Build binary sensor entities from the coordinator's device list."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities = []
    for ent in build_entities(coordinator.data or []):
        if ent["platform"] != "binary_sensor":
            continue
        entities.append(ObloBinarySensor(coordinator, ent))

    _LOGGER.debug("OBLO binary_sensor platform: %d entities", len(entities))
    async_add_entities(entities)
