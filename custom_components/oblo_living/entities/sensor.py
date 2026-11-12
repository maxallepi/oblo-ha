"Sensor entity for OBLO devices (temperature, humidity, battery, power, illuminance)."
from homeassistant.components.sensor import SensorEntity
from homeassistant.const import (
    PERCENTAGE, UnitOfTemperature, UnitOfPower, LIGHT_LUX,
    UnitOfElectricPotential, UnitOfElectricCurrent, UnitOfEnergy,
)


UNIT_MAP = {
    "%": PERCENTAGE,
    "°C": UnitOfTemperature.CELSIUS,
    "W": UnitOfPower.WATT,
    "lx": LIGHT_LUX,
}


class ObloSensor(SensorEntity):
    """Maps service property to a numeric sensor."""

    def __init__(self, hub, device, service_name, device_class, unit, value_prop):
        self._hub, self._device = hub, device
        self._service = service_name
        self._value_prop = value_prop
        self._attr_name = f"{device.name} {service_name}"
        self._attr_unique_id = f"oblo_{device.device_id}_{service_name}"
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = UNIT_MAP.get(unit)

    @property
    def native_value(self):
        val = self._device.properties.get(self._service, {}).get(self._value_prop)
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return val
