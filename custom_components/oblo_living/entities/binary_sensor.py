"""Binary sensor entity for OBLO devices (motion, contact, tamper).

Same descriptor-driven pattern as the sensor platform: the value is read from
coordinator.data on every access, so a 60s cloud poll refreshes the state.
"""
from homeassistant.components.binary_sensor import (
    BinarySensorEntity, BinarySensorDeviceClass,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity


DEVICE_CLASS = {
    "motion": BinarySensorDeviceClass.MOTION,
    "door": BinarySensorDeviceClass.DOOR,
    "window": BinarySensorDeviceClass.WINDOW,
    "tamper": BinarySensorDeviceClass.TAMPER,
    "smoke": BinarySensorDeviceClass.SMOKE,
}

TRUE_VALUES = ("1", "true", "yes", "on", "open", "active", "detected")


class ObloBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Maps one service property to on/off."""

    def __init__(self, coordinator, ent):
        super().__init__(coordinator)
        self._ent = ent
        self._attr_name = f"{ent['name']} {ent['service']}".strip()
        self._attr_unique_id = ent["unique_id"]
        if ent.get("device_class"):
            self._attr_device_class = DEVICE_CLASS.get(ent["device_class"])

    def _device(self):
        did = self._ent["device_id"]
        for dev in (self.coordinator.data or []):
            if dev.get("id") == did:
                return dev
        return None

    @property
    def is_on(self):
        dev = self._device()
        if dev is None:
            return None
        for svc in dev.get("services", []):
            if svc.get("service") != self._ent["service"]:
                continue
            props = dict(svc.get("props") or [])
            val = props.get(self._ent.get("value_prop"))
            if val is None:
                return None
            return str(val).lower() in TRUE_VALUES
        return None
