"""Button platform: stateless OBLO actions (siren start/stop).

HA imports this module by name because PLATFORMS in __init__.py lists
"button" -- it must live at this path, not under entities/.

Only services listed in MULTI_ENTITY_MAP with platform "button" appear here
(today: SimpleAlertService → "Sound" and "Silence" buttons).
"""
import logging

from .const import DOMAIN
from .dynamic import async_setup_dynamic_platform
from .entities.button import ObloSoundAlertButton, ObloSilenceAlertButton

_LOGGER = logging.getLogger(__name__)


def _make_button(coordinator, ent):
    """Pick the button class from the spec: action == "stop" is Silence."""
    if ent.get("action", "press") == "stop":
        return ObloSilenceAlertButton(coordinator, ent)
    return ObloSoundAlertButton(coordinator, ent)


async def async_setup_entry(hass, entry, async_add_entities):
    """Build buttons now and add any that appear on later refreshes."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    return async_setup_dynamic_platform(
        hass, entry, async_add_entities, "button",
        lambda ent: _make_button(coordinator, ent))