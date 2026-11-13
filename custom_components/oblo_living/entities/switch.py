"""Switch entity for OBLO relay/outlet devices.

Built from the coordinator data (not the legacy local hub). Uses the OHM
command path with mandatory read-back confirmation.
"""
from homeassistant.components.switch import SwitchEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..entity_map import entity_name, read_prop
from ..coordinator import write_token_for
from ..const import OHM_CONFIRM_TIMEOUT_S


class ObloSwitch(CoordinatorEntity, SwitchEntity):
    """Maps OutletService/outlet0/BasicService/basic0 to on/off."""

    def __init__(self, coordinator, ent):
        super().__init__(coordinator)
        self._ent = ent
        self._attr_name = entity_name(ent["name"], ent["service"])
        self._attr_unique_id = ent["unique_id"]
        self._attr_should_poll = False

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
    def is_on(self):
        svc = self._service()
        if svc is None:
            return False
        prop = self._ent.get("value_prop") or "State"
        val = read_prop(svc, prop)
        if val is None:
            return False
        s = str(val).strip().lower()
        return s in ("1", "true", "on", "yes")

    @property
    def available(self):
        """Only available when OHM is connected and the service still exists."""
        return self.coordinator.ohm is not None and self._service() is not None

    async def async_turn_on(self, **kwargs):
        await self._async_set(True)

    async def async_turn_off(self, **kwargs):
        await self._async_set(False)

    async def _async_set(self, want_on):
        did = self._ent["device_id"]
        svc_name = self._ent["service"]
        prop = self._ent.get("value_prop") or "State"
        svc_type = self._ent.get("service_type")
        svc = self._service()
        current = read_prop(svc, prop) if svc else None
        value = write_token_for(current, want_on)
        res = await self.coordinator.async_write_service_property(
            did, svc_name, prop, value, service_type=svc_type,
            confirm_timeout=OHM_CONFIRM_TIMEOUT_S)
        if not res.get("confirmed"):
            reason = res.get("error") or "value unchanged"
            raise HomeAssistantError(
                f"OHM write unconfirmed: {reason}")