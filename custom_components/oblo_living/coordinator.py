"""Data update coordinator for the OBLO cloud mode."""
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity, DataUpdateCoordinator, UpdateFailed,
)

from .cloud_api import ObloAuthError, ObloCloudApi, ObloCloudError, normalise_devices
from .const import DOMAIN, SCAN_INTERVAL_S

_LOGGER = logging.getLogger(__name__)


class ObloDataCoordinator(DataUpdateCoordinator):
    """Polls the cloud for gateway + device state, re-authenticating on expiry."""

    def __init__(self, hass: HomeAssistant, api: ObloCloudApi):
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}_coordinator",
            update_interval=__import__("datetime").timedelta(seconds=SCAN_INTERVAL_S))
        self.api = api
        self.devices = []

    async def _async_refresh_relogin(self):
        """Refresh after re-authentication once."""
        await self.api.async_login()
        return await self._async_fetch()

    async def _async_fetch(self):
        gateways = await self.api.async_list_gateways()
        details = await self.api.async_gateway_devices()
        devices = normalise_devices(gateways) + normalise_devices(details)
        # de-dup by id, keep the record carrying the most service data
        merged = {}
        for dev in devices:
            prev = merged.get(dev["id"])
            if prev is None or len(dev["services"]) > len(prev["services"]):
                merged[dev["id"]] = dev
        self.devices = list(merged.values())
        return self.devices

    async def _async_update_data(self):
        try:
            return await self._async_fetch()
        except ObloAuthError as err:
            _LOGGER.info("cloud session expired (%s), re-logging in", err)
            try:
                return await self._async_refresh_relogin()
            except ObloCloudError as err2:
                raise UpdateFailed(str(err2)) from err2
        except ObloCloudError as err:
            raise UpdateFailed(str(err)) from err
