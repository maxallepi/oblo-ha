"""Select entity for OBLO alert type (SimpleAlertService.PropAlertType).

Maps the 4-value enum (Notification/Alarm/Warning/Emergency) to a HA Select.
PropAlertType is write-only on the gateway: writing it produces no evt, no
poll row, and any read-back returns the gateway default "'0'". We therefore
remember what we last wrote and treat it as the current value until the
gateway itself publishes something different.
"""
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..entity_map import (
    alert_type_label, alert_type_wire, device_info_fields,
    entity_category_attr, entity_label_attr, read_prop_strict,
)
from ..const import OHM_CONFIRM_TIMEOUT_S

_LOGGER = logging.getLogger(__name__)


class ObloAlertTypeSelect(CoordinatorEntity, SelectEntity):
    """Maps SimpleAlertService.PropAlertType to a 4-option select."""

    def __init__(self, coordinator, ent):
        super().__init__(coordinator)
        self._ent = ent
        # has_entity_name: per-entity label ("Alert Type"), not per-service --
        # this service also emits Sound and Silence buttons.
        self._attr_has_entity_name = True
        self._attr_name = entity_label_attr(ent)
        _cat = entity_category_attr(ent)
        if _cat:
            self._attr_entity_category = EntityCategory(_cat)
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

    def _local_type_wire(self):
        """Our last write for PropAlertType, if any."""
        did = self._ent["device_id"]
        svc_name = self._ent["service"]
        return self.coordinator.local_write(did, svc_name, "PropAlertType")

    @property
    def current_option(self):
        # 1) Our own remembered write wins. PropAlertType is write-only, so a
        #    value from the poll/evt is a snapshot default, not a measurement;
        #    letting it win would silently revert the user's choice to
        #    Notification while the button still had to guess. The coordinator
        #    logs an INFO line when a gateway value disagrees.
        wire = self._local_type_wire()
        if wire is not None:
            lbl = alert_type_label(wire)
            if lbl in self._attr_options:
                return lbl
        # 2) Otherwise show whatever the gateway reports, if anything.
        svc = self._service()
        if svc is not None:
            val = read_prop_strict(svc, "PropAlertType")
            if val is not None:
                lbl = alert_type_label(val)
                if lbl in self._attr_options:
                    return lbl
        return None

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
            verify=False,
        )
        if res.get("error"):
            raise HomeAssistantError(f"OHM write failed: {res['error']}")
        # Remember what we asked for; the gateway will not confirm it.
        self.coordinator.remember_local_write(did, svc_name, "PropAlertType", wire)
        # Push immediately so the UI flips without waiting for the next poll.
        self.coordinator.push_local_update()
        _LOGGER.info("OHM write sent (not verified): PropAlertType=%s", wire)