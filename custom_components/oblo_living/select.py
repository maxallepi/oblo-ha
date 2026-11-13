"""Select platform: OBLO enums that need more than on/off.

HA imports this module by name because PLATFORMS in __init__.py lists
"select" -- it must live at this path, not under entities/.

Only services listed in MULTI_ENTITY_MAP with platform "select" appear here
(today: SimpleAlertService.PropAlertType with 4 alert types).
"""
import logging

from .const import DOMAIN
from .dynamic import async_setup_dynamic_platform
from .entities.select import ObloAlertTypeSelect

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Build selects now and add any that appear on later refreshes."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    return async_setup_dynamic_platform(
        hass, entry, async_add_entities, "select",
        lambda ent: ObloAlertTypeSelect(coordinator, ent))