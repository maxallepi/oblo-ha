"Switch entity for OBLO relay/outlet devices."
from homeassistant.components.switch import SwitchEntity


class ObloSwitch(SwitchEntity):
    """Maps basic0/outlet0 to on/off."""

    def __init__(self, hub, device, service_name):
        self._hub, self._device = hub, device
        self._service = service_name
        self._attr_name = f"{device.name} {service_name}"
        self._attr_unique_id = f"oblo_{device.device_id}_{service_name}"

    @property
    def is_on(self):
        val = self._device.properties.get(self._service, {}).get("On")
        if val is None:
            val = self._device.properties.get(self._service, {}).get("State")
        if val is None:
            return False
        return str(val).lower() in ("1", "true", "yes", "on")

    async def async_turn_on(self, **kwargs):
        self._hub.publish(self._device.device_id, self._service, "On", True)

    async def async_turn_off(self, **kwargs):
        self._hub.publish(self._device.device_id, self._service, "On", False)
