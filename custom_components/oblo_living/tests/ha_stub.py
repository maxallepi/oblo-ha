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

    exc = _mod("homeassistant.exceptions")

    class ConfigEntryAuthFailed(Exception):
        pass

    exc.ConfigEntryAuthFailed = ConfigEntryAuthFailed

    core = _mod("homeassistant.core")
    core.HomeAssistant = MagicMock
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

    util = _mod("homeassistant.util")
    util.dt = MagicMock()

    # wire submodules onto the package so 'from homeassistant import X' works
    ha.config_entries = ce
    ha.core = core
    ha.exceptions = exc
    ha.helpers = helpers


install()
