"""Sensor entity for OBLO devices (temperature, humidity, battery, power, illuminance).

Entities are built from the normalised cloud device list (coordinator.data)
via entity_map.build_entities() descriptors, and pull their value from the
coordinator on every refresh -- no extra wiring needed.
"""
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import (
    PERCENTAGE, UnitOfTemperature, UnitOfPower, LIGHT_LUX,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity


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
        self._attr_name = f"{ent['name']} {ent['service']}".strip()
        self._attr_unique_id = ent["unique_id"]
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
            props = dict(svc.get("props") or [])
            val = props.get(self._ent.get("value_prop"))
            if val is None:
                return None
            try:
                return float(val)
            except (ValueError, TypeError):
                return val
        return None
