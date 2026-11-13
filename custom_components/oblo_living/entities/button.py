"""Button entities for OBLO SimpleAlertService (start/stop siren).

PropAlertStartStop is an EDGE, not a persistent state: the OBLO app writes
"true" then "false" in scenes. A switch would lie by staying "on" after the
siren stops. Model as stateless buttons: press = write, wait, write stop.
Write order: set PropAlertType first, then start.

Two buttons:
- Sound: writes selected type, then "true", waits ALERT_SOUND_S, then "false"
- Silence: writes "false" immediately
"""
import asyncio
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..entity_map import (
    alert_type_label, alert_type_wire,
    ALERT_START_TOKEN, ALERT_STOP_TOKEN, ALERT_SOUND_S,
    entity_name_multi, read_prop_strict
)
from ..const import OHM_CONFIRM_TIMEOUT_S

_LOGGER = logging.getLogger(__name__)


class ObloSoundAlertButton(CoordinatorEntity, ButtonEntity):
    """Press = sound the alert with the currently selected type."""

    def __init__(self, coordinator, ent):
        super().__init__(coordinator)
        self._ent = ent
        self._attr_name = entity_name_multi(ent["name"], ent.get("name_suffix", "Sound"))
        self._attr_unique_id = ent["unique_id"]
        self._attr_should_poll = False

    def _service(self):
        did = self._ent["device_id"]
        for dev in (self.coordinator.data or []):
            if dev.get("id") != did:
                continue
            for svc in dev.get("services") or []:
                if svc.get("service") == self._ent["service"]:
                    return svc
        return None

    @property
    def available(self):
        return self.coordinator.ohm is not None and self._service() is not None

    async def async_press(self):
        did = self._ent["device_id"]
        svc_name = self._ent["service"]
        svc_type = self._ent.get("service_type")
        svc = self._service()

        # 1) Ensure the type is set (write order matters for the siren)
        cur_type = read_prop_strict(svc, "PropAlertType") if svc else None
        type_wire = None
        if cur_type is not None:
            try:
                type_label = alert_type_label(cur_type)
                type_wire = alert_type_wire(type_label)
            except ValueError:
                # garbage value -> don't guess; the device keeps its own type
                _LOGGER.warning(
                    "OBLO alert: unrecognised PropAlertType %r on %s, "
                    "sounding with the stored type", cur_type, self._attr_name)
                type_wire = None
                type_label = None
            if type_wire is not None:
                _LOGGER.debug("OBLO alert: setting type to %s (%s) before sounding",
                              type_label, type_wire)
                r = await self.coordinator.async_write_service_property(
                    did, svc_name, "PropAlertType", type_wire,
                    service_type=svc_type,
                    confirm_timeout=OHM_CONFIRM_TIMEOUT_S,
                    matcher=lambda s, r: alert_type_label(s) == alert_type_label(r),
                )
                if r.get("confirmed") is False:
                    raise HomeAssistantError(f"Alert type write refused: {r.get('error')}")

        # 2) Start the siren, and make the stop unmissable. If HA reloads or
        # the job is cancelled during the wait, the coroutine is cancelled --
        # a bare `await sleep(); write(false)` never runs, and the siren keeps
        # sounding with nobody left to silence it. The `finally` below sends
        # the stop anyway, and the registered stopper lets async_unload_entry
        # retry it if this final await is itself cut short.
        key = f"{did}:{svc_name}"
        started = False
        try:
            _LOGGER.info("OBLO alert: sounding %s", self._attr_name)
            r = await self.coordinator.async_write_service_property(
                did, svc_name, "PropAlertStartStop", ALERT_START_TOKEN,
                service_type=svc_type,
                confirm_timeout=OHM_CONFIRM_TIMEOUT_S,
            )
            if r.get("confirmed") is False:
                raise HomeAssistantError(f"Alert start refused: {r.get('error')}")
            started = True
            self.coordinator.register_alert_stopper(
                key, lambda: self._async_stop(did, svc_name, svc_type))

            # 3) Wait the configured duration, then stop
            await asyncio.sleep(ALERT_SOUND_S)
        finally:
            if started:
                _LOGGER.info("OBLO alert: stopping %s after %ss",
                             self._attr_name, ALERT_SOUND_S)
                try:
                    # shield: a cancelled press must not skip the stop write
                    await asyncio.shield(self._async_stop(did, svc_name, svc_type))
                except asyncio.CancelledError:
                    # the shielded stop is already in flight; leave the stopper
                    # registered so the unload drain retries it
                    raise
                self.coordinator.unregister_alert_stopper(key)

    async def _async_stop(self, did, svc_name, svc_type):
        """Write the stop edge and report it. Idempotent by nature."""
        r = await self.coordinator.async_write_service_property(
            did, svc_name, "PropAlertStartStop", ALERT_STOP_TOKEN,
            service_type=svc_type,
            confirm_timeout=OHM_CONFIRM_TIMEOUT_S,
        )
        if r.get("confirmed") is False:
            _LOGGER.warning("Alert stop write unconfirmed: %s", r.get("error"))
        return r


class ObloSilenceAlertButton(CoordinatorEntity, ButtonEntity):
    """Press = stop the alert immediately (writes "false" to PropAlertStartStop)."""

    def __init__(self, coordinator, ent):
        super().__init__(coordinator)
        self._ent = ent
        self._attr_name = entity_name_multi(ent["name"], ent.get("name_suffix", "Silence"))
        self._attr_unique_id = ent["unique_id"]
        self._attr_should_poll = False

    def _service(self):
        did = self._ent["device_id"]
        for dev in (self.coordinator.data or []):
            if dev.get("id") != did:
                continue
            for svc in dev.get("services") or []:
                if svc.get("service") == self._ent["service"]:
                    return svc
        return None

    @property
    def available(self):
        return self.coordinator.ohm is not None and self._service() is not None

    async def async_press(self):
        did = self._ent["device_id"]
        svc_name = self._ent["service"]
        svc_type = self._ent.get("service_type")
        _LOGGER.info("OBLO alert: silencing %s", self._attr_name)
        r = await self.coordinator.async_write_service_property(
            did, svc_name, "PropAlertStartStop", ALERT_STOP_TOKEN,
            service_type=svc_type,
            confirm_timeout=OHM_CONFIRM_TIMEOUT_S,
        )
        if r.get("confirmed") is False:
            raise HomeAssistantError(f"Alert silence refused: {r.get('error')}")