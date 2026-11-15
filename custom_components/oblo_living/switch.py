"""Switch platform: one HA switch per writable OBLO relay/outlet service.

HA imports this module by name because PLATFORMS in __init__.py lists
"switch" -- it must live at this path, not under entities/.

Only services whose SERVICE_MAP entry maps to the switch domain appear here.
The write itself is not done by the entity: it goes through
coordinator.async_write_service_property(), which confirms every write by
reading the property back. A switch that silently no-ops is worse than no
switch at all, so an unconfirmed write raises instead of flipping the UI.
"""
import logging

from .const import DOMAIN
from .dynamic import async_setup_dynamic_platform
from .entities.switch import ObloSwitch

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Build switches now and add any writable service that appears later."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    return async_setup_dynamic_platform(
        hass, entry, async_add_entities, "switch",
        lambda ent: ObloSwitch(coordinator, ent))