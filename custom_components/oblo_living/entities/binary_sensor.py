"""Binary sensor entity for OBLO devices (motion, contact, tamper).

Same descriptor-driven pattern as the sensor platform: the value is read from
coordinator.data on every access, so a 60s cloud poll refreshes the state.
"""
from homeassistant.components.binary_sensor import (
    BinarySensorEntity, BinarySensorDeviceClass,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..entity_map import (
    device_info_fields, entity_category_attr, entity_label_attr,
    resolve_binary_prop, read_prop_strict,
)


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
        # has_entity_name: the device page already shows the device name as a
        # header, so the entity row carries only its short label ("Outlet",
        # "Online") instead of repeating "Hallway Lights Outlet" in full.
        # The label is None when the device name already contains it, which
        # makes HA use the device name alone -- the old de-duplicated display.
        self._attr_has_entity_name = True
        self._attr_name = entity_label_attr(ent)
        _cat = entity_category_attr(ent)
        if _cat:
            self._attr_entity_category = EntityCategory(_cat)
        self._attr_unique_id = ent["unique_id"]
        # Attach to a real device record: without this HA has no device to
        # group the entity under and the integration page shows an empty
        # device list. Redacted nothing here -- identifiers/name/model only.
        self._attr_device_info = DeviceInfo(**device_info_fields(
            ent, getattr(coordinator, "entry_id", None),
            getattr(coordinator, "gateway_serial", None)))
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
            # Read the state-shaped property this service actually carries.
            # The old code read "whatever property exists", so a motion sensor
            # whose only props were DebouncePeriod/Sensitive reported a state
            # invented from a config value. Unknown is the honest answer.
            prop = resolve_binary_prop(svc, self._ent.get("value_prop"))
            val = read_prop_strict(svc, prop)
            if val is None:
                return None
            return str(val).lower() in TRUE_VALUES
        return None
