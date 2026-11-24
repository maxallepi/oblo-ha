"""Raw control path as HA services.

`oblo_living.set_property` exists so the payload shape can be validated with
ONE Developer Tools -> Services call, before any switch entity is trusted.
`oblo_living.get_property` is the read-back probe that proves it.
`oblo_living.remove_device` removes a device and its entities from the HA
registries, and optionally adds it to a permanent ignore list so it never
reappears (even after a restart) -- useful for devices deleted from the
gateway but still present in the cloud payload.

Both are honest about what a reply means: `code: 0` says the gateway accepted
the command, not that the device moved. A write is only reported as successful
when a read-back matches, and the service raises otherwise -- the caller gets
the sent value, the read-back and the verdict in the error message.
"""
import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

try:  # HA >= 2023.7 -- lets a service hand its result back to the caller
    from homeassistant.core import SupportsResponse
except ImportError:  # pragma: no cover - older HA
    SupportsResponse = None

from .const import DOMAIN, OHM_CONFIRM_TIMEOUT_S, CONF_IGNORED_DEVICES
from .entity_map import ohm_id_from_identifier

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_PROPERTY = "set_property"
SERVICE_GET_PROPERTY = "get_property"
SERVICE_REMOVE_DEVICE = "remove_device"

SET_PROPERTY_SCHEMA = vol.Schema({
    vol.Optional("entry_id"): str,
    vol.Required("device_id"): str,
    vol.Required("service_name"): str,
    vol.Required("property_name"): str,
    vol.Required("property_value"): vol.Any(str, int, float, bool),
    vol.Optional("service_type"): vol.Any(str, None),
    vol.Optional("confirm_timeout", default=OHM_CONFIRM_TIMEOUT_S): vol.Coerce(float),
})

def _entry_id_of(coordinator, entry):
    """entry_id for identifier maths, preferring the live entry."""
    return entry.entry_id if entry is not None else coordinator.entry_id


REMOVE_DEVICE_SCHEMA = vol.Schema({
    vol.Optional("entry_id"): str,
    vol.Exclusive("device_id", "identifier"): str,
    vol.Exclusive("entity_id", "identifier"): str,
    vol.Optional("permanent", default=False): bool,
})


GET_PROPERTY_SCHEMA = vol.Schema({
    vol.Optional("entry_id"): str,
    vol.Required("device_id"): str,
    vol.Required("service_name"): str,
    vol.Required("property_name"): str,
    vol.Optional("service_type"): vol.Any(str, None),
})


async def async_handle_remove_device(hass, call):
    """Remove a device and its entities from HA registries.

    Accepts either `device_id` (HA device registry id) or `entity_id` (any
    entity belonging to the device). If `permanent=true`, the OBLO cloud device
    id is added to the config entry's ignored list so the device never
    reappears after a restart.
    """
    data = call.data
    coordinator = _get_coordinator(hass, data.get("entry_id"))
    entry = hass.config_entries.async_get_entry(coordinator.entry_id)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    ha_device_id = data.get("device_id")
    if not ha_device_id:
        entity_id = data.get("entity_id")
        if not entity_id:
            raise HomeAssistantError("pass either device_id (HA device registry id) or entity_id")
        ent = ent_reg.async_get(entity_id)
        if ent is None or ent.device_id is None:
            raise HomeAssistantError(f"{entity_id} is not registered to a device")
        ha_device_id = ent.device_id

    device = dev_reg.async_get(ha_device_id)
    if device is None:
        raise HomeAssistantError(f"no HA device with id {ha_device_id}")
    if entry is not None and entry.entry_id not in device.config_entries:
        raise HomeAssistantError(
            f"device {ha_device_id} does not belong to OBLO entry {coordinator.entry_id}")

    # Extract the OBLO cloud device id from the registry identifiers
    # Format: (DOMAIN, f"{entry_id}_{cloud_device_id}")
    cloud_id = None
    for domain, ident in device.identifiers:
        if domain == DOMAIN:
            cloud_id = ohm_id_from_identifier(_entry_id_of(coordinator, entry), ident)
            break

    # Remove all entities of this device
    removed = 0
    for ent in er.async_entries_for_device(ent_reg, ha_device_id, include_disabled_entities=True):
        ent_reg.async_remove(ent.entity_id)
        removed += 1

    # Remove the device itself
    dev_reg.async_remove_device(ha_device_id)

    _LOGGER.info(
        "OBLO remove_device: ha_device=%s cloud_device=%s entities_removed=%d",
        ha_device_id, cloud_id, removed)

    permanent = bool(data.get("permanent"))
    if permanent and cloud_id and entry is not None:
        ignored = set(entry.options.get(CONF_IGNORED_DEVICES, []))
        if cloud_id not in ignored:
            ignored.add(cloud_id)
            hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_IGNORED_DEVICES: list(ignored)})
            # Also update the coordinator's in-memory set for immediate effect
            if hasattr(coordinator, "ignored_device_ids"):
                coordinator.ignored_device_ids.add(cloud_id)
            _LOGGER.info("OBLO remove_device: added %s to permanent ignore list", cloud_id)

    return {
        "removed_entities": removed,
        "ha_device_id": ha_device_id,
        "cloud_device_id": cloud_id,
        "permanent": permanent,
    }


def _get_coordinator(hass, entry_id=None):
    """Find the OBLO coordinator for this call."""
    entries = hass.data.get(DOMAIN) or {}
    if entry_id:
        if entry_id not in entries:
            raise HomeAssistantError(f"unknown OBLO entry_id {entry_id}")
        return entries[entry_id]["coordinator"]
    live = [v for v in entries.values() if v.get("coordinator") is not None]
    if not live:
        raise HomeAssistantError("no OBLO config entry is loaded")
    if len(live) > 1:
        raise HomeAssistantError(
            "multiple OBLO entries loaded; pass entry_id to disambiguate")
    return live[0]["coordinator"]


async def async_handle_set_property(hass, call):
    """Write one property, confirm by read-back, raise when unconfirmed."""
    data = call.data
    coordinator = _get_coordinator(hass, data.get("entry_id"))
    res = await coordinator.async_write_service_property(
        data["device_id"], data["service_name"], data["property_name"],
        data["property_value"],
        service_type=data.get("service_type"),
        confirm_timeout=data.get("confirm_timeout", OHM_CONFIRM_TIMEOUT_S),
    )
    summary = (f"device_id={data['device_id']} service={data['service_name']} "
               f"property={data['property_name']} sent={res['sent']!r} "
               f"readback={res['readback']!r}")
    if not res.get("confirmed"):
        raise HomeAssistantError(
            f"OHM write unconfirmed ({res.get('error') or 'value unchanged'}): "
            f"{summary}")
    return res


async def async_handle_get_property(hass, call):
    """Read one property straight from the gateway (bypasses the cache)."""
    data = call.data
    coordinator = _get_coordinator(hass, data.get("entry_id"))
    if coordinator.ohm is None:
        raise HomeAssistantError("OHM client not attached (REST-only mode)")
    value = await coordinator.async_read_service_property(
        data["device_id"], data["service_name"], data["property_name"],
        service_type=data.get("service_type"),
    )
    _LOGGER.info("OHM read device=%s service=%s property=%s value=%r",
                 data["device_id"], data["service_name"],
                 data["property_name"], value)
    return {"property_value": value}


async def async_register_services(hass):
    """Register once per HA instance, not per config entry."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_PROPERTY):
        return
    kwargs = {"supports_response": SupportsResponse.OPTIONAL} if SupportsResponse else {}
    hass.services.async_register(
        DOMAIN, SERVICE_SET_PROPERTY, async_handle_set_property,
        schema=SET_PROPERTY_SCHEMA, **kwargs)
    hass.services.async_register(
        DOMAIN, SERVICE_GET_PROPERTY, async_handle_get_property,
        schema=GET_PROPERTY_SCHEMA, **kwargs)
    hass.services.async_register(
        DOMAIN, SERVICE_REMOVE_DEVICE, async_handle_remove_device,
        schema=REMOVE_DEVICE_SCHEMA, **kwargs)
    _LOGGER.debug("registered %s.%s / %s.%s / %s.%s", DOMAIN, SERVICE_SET_PROPERTY,
                  DOMAIN, SERVICE_GET_PROPERTY, DOMAIN, SERVICE_REMOVE_DEVICE)
