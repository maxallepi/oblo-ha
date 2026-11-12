"Binary sensor entity for OBLO devices (motion, contact, tamper)."
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import STATE_ON, STATE_OFF


class ObloBinarySensor(BinarySensorEntity):
    """Maps service property to on/off."""

    def __init__(self, hub, device, service_name, device_class, value_prop):
        self._hub, self._device = hub, device
        self._service = service_name
        self._value_prop = value_prop
        self._attr_name = f"{device.name} {service_name}"
        self._attr_unique_id = f"oblo_{device.device_id}_{service_name}"
        self._attr_device_class = device_class

    @property
    def is_on(self):
        val = self._device.properties.get(self._service, {}).get(self._value_prop)
        if val is None:
            return None
        return str(val).lower() in ("1", "true", "yes", "on")

    async def async_update(self):
        """Hub pushes updates via listener; no polling needed."""
        pass
