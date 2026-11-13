"""Raw control path as HA services.

`oblo_living.set_property` exists so the payload shape can be validated with
ONE Developer Tools -> Services call, before any switch entity is trusted.
`oblo_living.get_property` is the read-back probe that proves it.

Both are honest about what a reply means: `code: 0` says the gateway accepted
the command, not that the device moved. A write is only reported as successful
when a read-back matches, and the service raises otherwise -- the caller gets
the sent value, the read-back and the verdict in the error message.
"""
import logging

import voluptuous as vol

from homeassistant.exceptions import HomeAssistantError

try:  # HA >= 2023.7 -- lets a service hand its result back to the caller
    from homeassistant.core import SupportsResponse
except ImportError:  # pragma: no cover - older HA
    SupportsResponse = None

from .const import DOMAIN, OHM_CONFIRM_TIMEOUT_S

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_PROPERTY = "set_property"
SERVICE_GET_PROPERTY = "get_property"

SET_PROPERTY_SCHEMA = vol.Schema({
    vol.Optional("entry_id"): str,
    vol.Required("device_id"): str,
    vol.Required("service_name"): str,
    vol.Required("property_name"): str,
    vol.Required("property_value"): vol.Any(str, int, float, bool),
    vol.Optional("service_type"): vol.Any(str, None),
    vol.Optional("confirm_timeout", default=OHM_CONFIRM_TIMEOUT_S): vol.Coerce(float),
})

GET_PROPERTY_SCHEMA = vol.Schema({
    vol.Optional("entry_id"): str,
    vol.Required("device_id"): str,
    vol.Required("service_name"): str,
    vol.Required("property_name"): str,
    vol.Optional("service_type"): vol.Any(str, None),
})


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
    _LOGGER.debug("registered %s.%s / %s.%s", DOMAIN, SERVICE_SET_PROPERTY,
                  DOMAIN, SERVICE_GET_PROPERTY)
