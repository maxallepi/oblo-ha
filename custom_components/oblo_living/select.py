"""Select platform: OBLO enums that need more than on/off.

HA imports this module by name because PLATFORMS in __init__.py lists
"select" -- it must live at this path, not under entities/.

Only services listed in MULTI_ENTITY_MAP with platform "select" appear here
(today: SimpleAlertService.PropAlertType with 4 alert types).
"""
import logging

from .const import DOMAIN
from .entities.select import ObloAlertTypeSelect
from .entity_map import build_entities

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Build select entities from the devices the coordinator already fetched."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities = []
    for ent in build_entities(coordinator.data or []):
        if ent["platform"] != "select":
            continue
        entities.append(ObloAlertTypeSelect(coordinator, ent))

    _LOGGER.debug("OBLO select platform: %d entities", len(entities))
    async_add_entities(entities)