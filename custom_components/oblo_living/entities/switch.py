"""Switch entity for OBLO relay/outlet devices.

Built from the coordinator data (not the legacy local hub). Uses the OHM
command path with mandatory read-back confirmation.
"""
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..entity_map import (
    device_info_fields, entity_category_attr, entity_label_attr, read_prop_strict
)
from ..coordinator import write_token_for
from ..const import OHM_CONFIRM_TIMEOUT_S


class ObloSwitch(CoordinatorEntity, SwitchEntity):
    """Maps OutletService/outlet0/BasicService/basic0 to on/off."""

    def __init__(self, coordinator, ent):
        super().__init__(coordinator)
        self._ent = ent
        # has_entity_name: device page shows device name as header; row shows short label
        self._attr_has_entity_name = True
        self._attr_name = entity_label_attr(ent)
        _cat = entity_category_attr(ent)
        if _cat:
            self._attr_entity_category = EntityCategory(_cat)
        self._attr_unique_id = ent["unique_id"]
        self._attr_should_poll = False
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
    def is_on(self):
        did = self._ent["device_id"]
        svc_name = self._ent["service"]
        svc = self._service()
        prop = self._ent.get("value_prop") or "State"
        val = read_prop_strict(svc, prop) if svc is not None else None
        if val is None:
            # No poll row for this property (a relay in ohm.db carries only
            # DeviceType). Fall back to what WE last asked for -- the gateway
            # never reports State, so a remembered write is the only truth
            # available. A poll value always wins when one exists.
            val = self.coordinator.local_write(did, svc_name, prop)
        if val is None:
            # Report UNKNOWN, not off. Claiming "off" would be a lie in the one
            # direction this component must never lie: a relay that is
            # physically on and reported as off is worse than one that admits
            # it doesn't know.
            return None
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
        current = read_prop_strict(svc, prop) if svc else None
        value = write_token_for(current, want_on)
        res = await self.coordinator.async_write_service_property(
            did, svc_name, prop, value, service_type=svc_type,
            confirm_timeout=OHM_CONFIRM_TIMEOUT_S)
        if not res.get("ok"):
            # Never reached the gateway (refused, or transport died). The relay
            # did not move, so say so instead of flipping the UI.
            reason = res.get("error") or "write not sent"
            raise HomeAssistantError(f"OHM write failed: {reason}")
        if not res.get("confirmed"):
            if not res.get("observed"):
                # The command went out and no read-back ever returned a value
                # for this property -- unobservable, not refused. Remember what
                # we asked for so the UI flips now and stays honest until a
                # poll carries State. (`observed`, not `readback is None`: the
                # retry loop's last timed-out read overwrites an earlier real
                # answer with None.)
                self.coordinator.remember_local_write(did, svc_name, prop, value)
                self.coordinator.push_local_update()
                return
            # The gateway answered with a different value: a real silent no-op.
            raise HomeAssistantError(
                f"OHM write unconfirmed: gateway reports {res['readback']!r} "
                f"after {value!r}")
        # confirmed=True → _poke_device already fired in coordinator