"""Binary sensor entity for OBLO devices (motion, contact, tamper).

Same descriptor-driven pattern as the sensor platform: the value is read from
coordinator.data on every access, so a 60s cloud poll refreshes the state.
"""
from homeassistant.components.binary_sensor import (
    BinarySensorEntity, BinarySensorDeviceClass,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..entity_map import entity_name, read_prop


DEVICE_CLASS = {
    "motion": BinarySensorDeviceClass.MOTION,
    "door": BinarySensorDeviceClass.DOOR,
    "window": BinarySensorDeviceClass.WINDOW,
    "tamper": BinarySensorDeviceClass.TAMPER,
    "smoke": BinarySensorDeviceClass.SMOKE,
    "moisture": BinarySensorDeviceClass.MOISTURE,
    "gas": BinarySensorDeviceClass.GAS,
    "vibration": BinarySensorDeviceClass.VIBRATION,
    "connectivity": BinarySensorDeviceClass.CONNECTIVITY,
    "safety": BinarySensorDeviceClass.SAFETY,
}

TRUE_VALUES = ("1", "true", "yes", "on", "open", "active", "detected")


class ObloBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Maps one service property to on/off."""

    def __init__(self, coordinator, ent):
        super().__init__(coordinator)
        self._ent = ent
        self._attr_name = entity_name(ent["name"], ent["service"])
        self._attr_unique_id = ent["unique_id"]
        if ent.get("device_class"):
            # getattr: tolerate HA builds that lack a given device class
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
            val = read_prop(svc, self._ent.get("value_prop"))
            if val is None:
                return None
            return str(val).lower() in TRUE_VALUES
        return None
