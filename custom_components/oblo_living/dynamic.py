"""Dynamic entity discovery shared by every OBLO platform.

Every platform used to build its entities exactly once, inside its own
`async_setup_entry`. A device enrolled on the gateway later *did* appear in
`coordinator.data` on the next refresh, but no entity was ever created for it:
only an HA restart picked it up.

This helper keeps the `async_add_entities` callback alive and re-runs
`build_entities()` on every coordinator update, adding only unique_ids that
have not been added yet. Cheap by construction -- the diff is over an in-memory
device tree, and the common case (nothing new) is a single set lookup per
candidate. Entities are never removed here: a device that disappears from the
gateway becomes unavailable through the coordinator, which is HA's own
semantics, and silently deleting entities would drop user customisations.
"""
import logging

from .const import DOMAIN
from .entity_map import build_entities

_LOGGER = logging.getLogger(__name__)


def _entries_for(coordinator, platform):
    """Mapped entity specs for one platform from the cached device tree."""
    return [e for e in build_entities(coordinator.data or [])
            if e.get("platform") == platform]


def async_setup_dynamic_platform(hass, entry, async_add_entities, platform, factory):
    """Set up a platform and keep adding entities as devices turn up.

    `factory(spec)` must return a constructed entity for one spec dict.
    Returns the number of entities added on the first pass (for tests).
    """
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    # Known unique_ids per platform. Kept in hass.data rather than a module
    # global so a reload starts from a clean slate.
    seen = data.setdefault("known_unique_ids", {}).setdefault(platform, set())

    def _build(specs):
        """Construct entities for specs whose unique_id we have not added yet."""
        made, names = [], []
        for spec in specs:
            uid = spec.get("unique_id")
            if not uid or uid in seen:
                continue
            seen.add(uid)
            made.append(factory(spec))
            names.append(spec.get("name") or uid)
        return made, names

    first, _ = _build(_entries_for(coordinator, platform))
    _LOGGER.debug("OBLO %s platform: %d initial entities", platform, len(first))
    if first:
        async_add_entities(first)

    def _on_refresh():
        """Coordinator listener: add entities for devices we have not seen."""
        new, names = _build(_entries_for(coordinator, platform))
        if new:
            _LOGGER.info(
                "OBLO %s platform: %d new entit%s appeared (%s)",
                platform, len(new), "y" if len(new) == 1 else "ies",
                ", ".join(names))
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_on_refresh))
    # HA's async_forward_entry_setups / entity component calls
    # `if not await platform.async_setup_entry(...)` and raises
    # ConfigEntryError on a falsy return (see homeassistant/components/button/
    # __init__.py). Returning the count was wrong: a platform with zero
    # entities (e.g. button when no siren is enrolled) returned 0 -> falsy ->
    # the WHOLE integration entry was torn down, losing every other platform's
    # entities too. Setup succeeded regardless of count, so return True.
    return True
