"""Minimal Home Assistant stub so the component imports without HA installed.

Registers real module objects (packages need __path__) plus the handful of
symbols the component touches. Test-only; never shipped.
"""
import sys
import types
from unittest.mock import MagicMock


def _mod(name, package=False):
    m = types.ModuleType(name)
    if package:
        m.__path__ = []
    sys.modules[name] = m
    return m


def install():
    if "homeassistant.core" in sys.modules:
        return

    ha = _mod("homeassistant", package=True)
    ce = _mod("homeassistant.config_entries")
    ce.ConfigEntry = MagicMock

    class ConfigFlow:
        def __init_subclass__(cls, domain=None, **kwargs):
            cls._domain = domain
            super().__init_subclass__(**kwargs)

    ce.ConfigFlow = ConfigFlow

    class OptionsFlow:
        """Mirrors HA's OptionsFlow: HA injects config_entry, the step returns
        a form dict or an entry dict. Enough to exercise the real schema."""

        config_entry = None

        def async_create_entry(self, title="", data=None):
            self.created = {"title": title, "data": data}
            return self.created

        def async_show_form(self, step_id, data_schema=None, errors=None,
                            description_placeholders=None):
            self.shown = {
                "step_id": step_id,
                "schema": data_schema,
                "errors": errors,
                "placeholders": description_placeholders,
            }
            return self.shown

    ce.OptionsFlow = OptionsFlow

    exc = _mod("homeassistant.exceptions")

    class ConfigEntryAuthFailed(Exception):
        pass

    class HomeAssistantError(Exception):
        pass

    exc.ConfigEntryAuthFailed = ConfigEntryAuthFailed
    exc.HomeAssistantError = HomeAssistantError

    core = _mod("homeassistant.core")
    core.HomeAssistant = MagicMock
    core.SupportsResponse = type("SupportsResponse", (), {"OPTIONAL": "optional"})()
    core.callback = lambda f: f

    helpers = _mod("homeassistant.helpers", package=True)
    helpers.__getattr__ = lambda name: MagicMock()

    uc = _mod("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        def __init__(self, hass, logger, name=None, update_interval=None):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data = None

        async def async_config_entry_first_refresh(self):
            self.data = await self._async_update_data()
            return self.data

    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

    class UpdateFailed(Exception):
        pass

    uc.DataUpdateCoordinator = DataUpdateCoordinator
    uc.CoordinatorEntity = CoordinatorEntity
    uc.UpdateFailed = UpdateFailed

    aio = _mod("homeassistant.helpers.aiohttp_client")
    aio.async_get_clientsession = MagicMock()

    # device registry: entities declare DeviceInfo so HA groups them onto a
    # device page. The stub keeps every DeviceInfo it is given and records
    # async_get_or_create calls, so a test can assert the gateway device and
    # the per-device identifiers actually reach the registry.
    devreg = _mod("homeassistant.helpers.device_registry")

    class DeviceInfo(dict):
        """Dict with attribute access, like HA's DeviceInfo TypedDict."""

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            for key, value in kwargs.items():
                setattr(self, key, value)

    class DeviceRegistry:
        def __init__(self):
            self.created = []

        def async_get_or_create(self, **kwargs):
            self.created.append(kwargs)
            return kwargs

    _REGISTRY = DeviceRegistry()
    devreg.DeviceInfo = DeviceInfo
    devreg.async_get = lambda hass: _REGISTRY
    devreg.DeviceRegistry = DeviceRegistry
    devreg._REGISTRY = _REGISTRY
    helpers.device_registry = devreg

    util = _mod("homeassistant.util")
    util.dt = MagicMock()

    # ----- component-specific submodules -----------------------------------
    # sensor platform
    sensor_mod = _mod("homeassistant.components.sensor", package=True)

    class SensorEntity:
        _attr_name = None
        _attr_unique_id = None
        _attr_device_class = None
        _attr_native_unit_of_measurement = None

        @property
        def native_value(self):
            raise NotImplementedError

    class SensorDeviceClass:
        TEMPERATURE = "temperature"
        HUMIDITY = "humidity"
        ILLUMINANCE = "illuminance"
        BATTERY = "battery"
        POWER = "power"

    sensor_mod.SensorEntity = SensorEntity
    sensor_mod.SensorDeviceClass = SensorDeviceClass

    # binary_sensor platform
    bs_mod = _mod("homeassistant.components.binary_sensor", package=True)

    class BinarySensorEntity:
        _attr_name = None
        _attr_unique_id = None
        _attr_device_class = None

        @property
        def is_on(self):
            raise NotImplementedError

    class BinarySensorDeviceClass:
        MOTION = "motion"
        DOOR = "door"
        WINDOW = "window"
        TAMPER = "tamper"
        SMOKE = "smoke"
        MOISTURE = "moisture"
        GAS = "gas"
        VIBRATION = "vibration"
        CONNECTIVITY = "connectivity"
        SAFETY = "safety"
        PROBLEM = "problem"

    bs_mod.BinarySensorEntity = BinarySensorEntity
    bs_mod.BinarySensorDeviceClass = BinarySensorDeviceClass

    # switch platform
    sw_mod = _mod("homeassistant.components.switch", package=True)

    class SwitchEntity:
        _attr_name = None
        _attr_unique_id = None
        _attr_should_poll = True

        @property
        def is_on(self):
            raise NotImplementedError

        async def async_turn_on(self, **kwargs):
            raise NotImplementedError

        async def async_turn_off(self, **kwargs):
            raise NotImplementedError

    sw_mod.SwitchEntity = SwitchEntity

    # select platform
    sel_mod = _mod("homeassistant.components.select", package=True)

    class SelectEntity:
        _attr_name = None
        _attr_unique_id = None
        _attr_should_poll = True
        _attr_options = []

        @property
        def current_option(self):
            raise NotImplementedError

        async def async_select_option(self, option):
            raise NotImplementedError

    sel_mod.SelectEntity = SelectEntity

    # button platform
    btn_mod = _mod("homeassistant.components.button", package=True)

    class ButtonEntity:
        _attr_name = None
        _attr_unique_id = None
        _attr_should_poll = False

        async def async_press(self):
            raise NotImplementedError

    btn_mod.ButtonEntity = ButtonEntity

    # const units
    const_mod = _mod("homeassistant.const")
    const_mod.PERCENTAGE = "%"
    const_mod.UnitOfTemperature = types.SimpleNamespace(CELSIUS="°C")
    const_mod.UnitOfPower = types.SimpleNamespace(WATT="W")
    const_mod.LIGHT_LUX = "lx"
    const_mod.STATE_ON = "on"
    const_mod.STATE_OFF = "off"

    # wire submodules onto the package
    ha.config_entries = ce
    ha.core = core
    ha.exceptions = exc
    ha.helpers = helpers
    ha.const = const_mod
    ha.components = _mod("homeassistant.components", package=True)
    ha.components.sensor = sensor_mod
    ha.components.binary_sensor = bs_mod
    ha.components.switch = sw_mod
    ha.components.select = sel_mod
    ha.components.button = btn_mod

install()