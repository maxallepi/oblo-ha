"""Light entity for ObloDimService (on/off + brightness)."""
from homeassistant.components.light import LightEntity
from homeassistant.const import STATE_ON, STATE_OFF


class ObloLight(LightEntity):
    """Maps serviceName 'dim' property 'level' (0-100 assumed)."""

    def __init__(self, hub, device):
        self._hub, self._device = hub, device
        self._attr_name = device.name or f"OBLO {device.device_id}"
        self._attr_unique_id = f"oblo_{device.device_id}_dim"

    @property
    def level(self):
        return (self._device.properties.get("dim", {}).get("level"))

    @property
    def is_on(self):
        return bool(self.level and self.level > 0)

    async def async_turn_on(self, **kwargs):
        self._hub.publish_dim(self._device.device_id,
                              kwargs.get("brightness") or 100)

    async def async_turn_off(self, **kwargs):
        self._hub.publish_dim(self._device.device_id, 0)
