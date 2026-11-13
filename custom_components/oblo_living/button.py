"""Button platform: stateless OBLO actions (siren start/stop).

HA imports this module by name because PLATFORMS in __init__.py lists
"button" -- it must live at this path, not under entities/.

Only services listed in MULTI_ENTITY_MAP with platform "button" appear here
(today: SimpleAlertService → "Sound" and "Silence" buttons).
"""
import logging

from .const import DOMAIN
from .entities.button import ObloSoundAlertButton, ObloSilenceAlertButton
from .entity_map import build_entities

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Build button entities from the devices the coordinator already fetched."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities = []
    for ent in build_entities(coordinator.data or []):
        if ent["platform"] != "button":
            continue
        action = ent.get("action", "press")
        if action == "stop":
            entities.append(ObloSilenceAlertButton(coordinator, ent))
        else:
            entities.append(ObloSoundAlertButton(coordinator, ent))

    _LOGGER.debug("OBLO button platform: %d entities", len(entities))
    async_add_entities(entities)