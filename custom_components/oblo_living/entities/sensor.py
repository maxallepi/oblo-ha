"""Sensor entity for OBLO devices (temperature, humidity, battery, power, illuminance).

Entities are built from the normalised cloud device list (coordinator.data)
via entity_map.build_entities() descriptors, and pull their value from the
coordinator on every refresh -- no extra wiring needed.
"""
import logging

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
    POWER_SERVICES, device_info_fields, entity_category_attr, entity_label_attr,
    read_prop, read_prop_strict, resolve_power_prop,
)
from ..entity_map import props_as_map

_LOGGER = logging.getLogger(__name__)


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
            # Tolerant service match: exact, then case-insensitive.
            if svc.get("service") != self._ent["service"]:
                if str(svc.get("service", "")).lower() != str(self._ent["service"]).lower():
                    continue
            service_name = self._ent["service"]
            value_prop = self._ent.get("value_prop")
            # Power services: re-resolve the property name on every read
            # in case the gateway starts reporting a different key, or the
            # entity was created before the meter value appeared.
            if service_name in POWER_SERVICES:
                resolved = resolve_power_prop(svc, value_prop)
                if resolved != value_prop:
                    value_prop = resolved
            # Power services read STRICTLY: read_prop() falls back to "the
            # first property whatever it is", which on a metering service can
            # return a flag or a DeviceType and display it as watts. A power
            # sensor showing 16 W when nobody measured anything is the lie we
            # refuse -- Unknown is the honest answer.
            if service_name in POWER_SERVICES:
                val = read_prop_strict(svc, value_prop)
            else:
                val = read_prop(svc, value_prop)
            if val is None:
                # One-time WARNING naming the props actually present on this
                # power service, so the log answers the "what's the key?"
                # question with data instead of another round of speculation.
                if (service_name in POWER_SERVICES
                        and not getattr(self, "_power_warned", False)):
                    m = props_as_map(svc)
                    _LOGGER.warning(
                        "Power sensor '%s' (dev %s, svc %s): value_prop=%s, "
                        "no numeric value. Props present: %s",
                        self._ent.get("unique_id"),
                        self._ent.get("device_id"),
                        service_name,
                        value_prop,
                        list(m.keys()) or "[] (service carries no properties)",
                    )
                    self._power_warned = True
                return None
            try:
                return float(val)
            except (ValueError, TypeError):
                return val
        return None
