"""Select entity for OBLO alert type (SimpleAlertService.PropAlertType).

Maps the 4-value enum (Notification/Alarm/Warning/Emergency) to a HA Select.
Uses loose confirmation with label↔number equivalence because the gateway
may echo the label ("WARNING") while we write the number ("2").
"""
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..entity_map import (
    alert_type_label, alert_type_wire, device_info_fields,
    entity_name_multi, read_prop_strict,
)
from ..const import OHM_CONFIRM_TIMEOUT_S

_LOGGER = logging.getLogger(__name__)


class ObloAlertTypeSelect(CoordinatorEntity, SelectEntity):
    """Maps SimpleAlertService.PropAlertType to a 4-option select."""

    def __init__(self, coordinator, ent):
        super().__init__(coordinator)
        self._ent = ent
        self._attr_name = entity_name_multi(ent["name"], ent.get("name_suffix", "Type"))
        self._attr_unique_id = ent["unique_id"]
        self._attr_should_poll = False
        self._attr_options = list(ent.get("options") or [])
        # Same identifiers as the Sound/Silence buttons -> one device page.
        self._attr_device_info = DeviceInfo(**device_info_fields(
            ent, getattr(coordinator, "entry_id", None),
            getattr(coordinator, "gateway_serial", None)))

    def _service(self):
        """Find this entity's service dict in the latest coordinator data."""
        did = self._ent["device_id"]
        for dev in (self.coordinator.data or []):
            if dev.get("id") != did:
                continue
            for svc in dev.get("services") or []:
                if svc.get("service") == self._ent["service"]:
                    return svc
        return None

    @property
    def current_option(self):
        svc = self._service()
        if svc is None:
            return None
        val = read_prop_strict(svc, "PropAlertType")
        if val is None:
            return None
        lbl = alert_type_label(val)
        return lbl if lbl in self._attr_options else None

    @property
    def available(self):
        """Only available when OHM is connected and the service still exists."""
        return self.coordinator.ohm is not None and self._service() is not None

    async def async_select_option(self, option: str):
        if option not in self._attr_options:
            raise HomeAssistantError(f"invalid alert type: {option!r}")
        wire = alert_type_wire(option)
        did = self._ent["device_id"]
        svc_name = self._ent["service"]
        svc_type = self._ent.get("service_type")
        res = await self.coordinator.async_write_service_property(
            did, svc_name, "PropAlertType", wire,
            service_type=svc_type,
            confirm_timeout=OHM_CONFIRM_TIMEOUT_S,
            matcher=lambda s, r: alert_type_label(s) == alert_type_label(r),
        )
        if res.get("confirmed") is False:
            reason = res.get("error") or "value unchanged"
            raise HomeAssistantError(f"OHM write unconfirmed: {reason}")
        if res.get("confirmed") is None:
            # No read-back on this device class (SimpleAlert has no properties
            # in OHM). The write was sent; we just can't verify it.
            _LOGGER.info("OHM write sent, no read-back available: PropAlertType=%s", wire)