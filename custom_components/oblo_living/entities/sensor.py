"""Sensor entity for OBLO devices (temperature, humidity, battery, power, illuminance).

Entities are built from the normalised cloud device list (coordinator.data)
via entity_map.build_entities() descriptors, and pull their value from the
coordinator on every refresh -- no extra wiring needed.
"""
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    UnitOfPower,
    UnitOfTemperature,
    LIGHT_LUX,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..entity_map import (
    device_info_fields, entity_category_attr, entity_label_attr, read_prop
)


# HA device-class keys (lowercase, as used in entity_map) -> SensorDeviceClass
DEVICE_CLASS = {
    "temperature": SensorDeviceClass.TEMPERATURE,
    "humidity": SensorDeviceClass.HUMIDITY,
    "illuminance": SensorDeviceClass.ILLUMINANCE,
    "battery": SensorDeviceClass.BATTERY,
    "power": SensorDeviceClass.POWER,
}

# textual units from the cloud -> HA units
UNIT_MAP = {
    "%": PERCENTAGE,
    "°C": UnitOfTemperature.CELSIUS,
    "W": UnitOfPower.WATT,
    "lx": LIGHT_LUX,
}


class ObloSensor(CoordinatorEntity, SensorEntity):
    """One mapped service property on one cloud device."""

    def __init__(self, coordinator, ent):
        # ent is a descriptor dict from entity_map.build_entities():
        # {device_id, name, service, platform, device_class, unit, value_prop, unique_id}
        super().__init__(coordinator)
        self._ent = ent
        # has_entity_name: device page shows device name as header; row shows short label
        self._attr_has_entity_name = True
        self._attr_name = entity_label_attr(ent)
        _cat = entity_category_attr(ent)
        if _cat:
            self._attr_entity_category = EntityCategory(_cat)
        self._attr_unique_id = ent["unique_id"]
        self._attr_device_info = DeviceInfo(**device_info_fields(
            ent, getattr(coordinator, "entry_id", None),
            getattr(coordinator, "gateway_serial", None)))
        if ent.get("device_class"):
            self._attr_device_class = DEVICE_CLASS.get(ent["device_class"])
        if ent.get("unit"):
            self._attr_native_unit_of_measurement = UNIT_MAP.get(ent["unit"])

    def _device(self):
        """Find this entity's device dict in the latest coordinator data."""
        did = self._ent["device_id"]
        for dev in (self.coordinator.data or []):
            if dev.get("id") == did:
                return dev
        return None

    @property
    def native_value(self):
        dev = self._device()
        if dev is None:
            return None
        for svc in dev.get("services", []):
            if svc.get("service") != self._ent["service"]:
                continue
            val = read_prop(svc, self._ent.get("value_prop"))
            if val is None:
                return None
            try:
                return float(val)
            except (ValueError, TypeError):
                return val
        return None
