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
from datetime import datetime

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..entity_map import (
    alert_type_label, alert_type_wire, ALERT_START_TOKEN, ALERT_STOP_TOKEN,
    ALERT_SOUND_S, device_info_fields, entity_category_attr,
    entity_label_attr, read_prop_strict
)
from ..const import OHM_CONFIRM_TIMEOUT_S

_LOGGER = logging.getLogger(__name__)


def _stamp():
    """Millisecond wall clock, for ordering our writes against evt lines."""
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


class ObloSoundAlertButton(CoordinatorEntity, ButtonEntity):
    """Press = sound the alert with the currently selected type."""

    def __init__(self, coordinator, ent):
        super().__init__(coordinator)
        self._ent = ent
        # has_entity_name: row reads "Sound" under the device header, not the
        # full "Gateway Simple Alert 15 Sound". Per-entity label table keeps
        # this distinct from the sibling Sound/Silence rows.
        self._attr_has_entity_name = True
        self._attr_name = entity_label_attr(ent)
        _cat = entity_category_attr(ent)
        if _cat:
            self._attr_entity_category = EntityCategory(_cat)
        self._attr_unique_id = ent["unique_id"]
        self._attr_should_poll = False
        # Shared with the alert-type select and Silence button: one device page.
        self._attr_device_info = DeviceInfo(**device_info_fields(
            ent, getattr(coordinator, "entry_id", None),
            getattr(coordinator, "gateway_serial", None)))

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
        key = f"{did}:{svc_name}"
        # Remember which press we are. Silence bumps this; if it changed while
        # we were setting the type, the stop already went out and writing
        # "true" now would re-sound a siren Andrew just silenced.
        gen = self.coordinator.alert_generation(key)
        svc = self._service()

        # 1) Ensure the type is set (write order matters for the siren).
        #    PropAlertType has no read-back contract: the poll omits it and a
        #    targeted read returns the gateway default "0". Reading it before
        #    every press is therefore what silently downgraded every alert to
        #    NOTIFICATION. Order of trust: what WE last wrote from the select,
        #    else the gateway's value if one ever appears. The remembered write
        #    outranks a snapshot default on purpose -- see LOCAL_AUTHORITY_PROPS.
        type_wire = self.coordinator.local_write(did, svc_name, "PropAlertType")
        if type_wire is not None:
            try:
                # Normalise whatever we stored into a wire ordinal.
                type_wire = alert_type_wire(alert_type_label(type_wire))
            except ValueError:
                _LOGGER.warning(
                    "OBLO alert: unrecognised remembered PropAlertType %r on "
                    "%s, falling back to the gateway value", type_wire,
                    self._attr_name)
                type_wire = None
        if type_wire is None:
            cur_type = read_prop_strict(svc, "PropAlertType") if svc else None
            if cur_type is not None:
                try:
                    type_wire = alert_type_wire(alert_type_label(cur_type))
                except ValueError:
                    _LOGGER.warning(
                        "OBLO alert: unrecognised PropAlertType %r on %s, "
                        "sounding with the stored type", cur_type,
                        self._attr_name)
                    type_wire = None
        if type_wire is not None:
            _LOGGER.info("OBLO alert: setting type to %s before sounding", type_wire)
            r = await self.coordinator.async_write_service_property(
                did, svc_name, "PropAlertType", type_wire,
                service_type=svc_type,
                confirm_timeout=OHM_CONFIRM_TIMEOUT_S,
                matcher=lambda s, r: alert_type_label(s) == alert_type_label(r),
                verify=False,
            )
            # An unconfirmed read-back is expected here and must never block
            # the siren; only an actual send/refusal failure does.
            if r.get("error"):
                raise HomeAssistantError(f"Alert type write failed: {r['error']}")
            self.coordinator.remember_local_write(did, svc_name, "PropAlertType", type_wire)

        # 2) Start the siren, and make the stop unmissable. If HA reloads or
        # the job is cancelled during the wait, the coroutine is cancelled --
        # a bare `await sleep(); write(false)` never runs, and the siren keeps
        # sounding with nobody left to silence it. The `finally` below sends
        # the stop anyway, and the registered stopper lets async_unload_entry
        # retry it if this final await is itself cut short.
        key = f"{did}:{svc_name}"
        started = False
        # Superseded while preparing? Then a Silence has already been sent and
        # this press must not start the siren.
        if self.coordinator.alert_generation(key) != gen:
            _LOGGER.info("OBLO alert: %s press superseded by Silence before "
                         "start; not sounding", self._attr_name)
            return
        try:
            # Wall-clock stamp on every alert write so a later evt line can be
            # ordered against it. @research's one-press test reads these two
            # lines: a PropAlertStartStop=false evt BEFORE this stop's stamp
            # proves the gateway self-times-out; no evt proves it does not.
            _LOGGER.info("OBLO alert: sounding %s (start write at %s)",
                         self._attr_name, _stamp())
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
        _LOGGER.info("OBLO alert: stop write at %s (%s)",
                     _stamp(), self._attr_name)
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
        self._attr_has_entity_name = True
        self._attr_name = entity_label_attr(ent)
        _cat = entity_category_attr(ent)
        if _cat:
            self._attr_entity_category = EntityCategory(_cat)
        self._attr_unique_id = ent["unique_id"]
        self._attr_should_poll = False
        self._attr_device_info = DeviceInfo(**device_info_fields(
            ent, getattr(coordinator, "entry_id", None),
            getattr(coordinator, "gateway_serial", None)))

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
        # Bump first: any Sound press still preparing its type must abandon
        # rather than write "true" after this stop.
        self.coordinator.bump_alert_generation(f"{did}:{svc_name}")
        _LOGGER.info("OBLO alert: silencing %s", self._attr_name)
        r = await self.coordinator.async_write_service_property(
            did, svc_name, "PropAlertStartStop", ALERT_STOP_TOKEN,
            service_type=svc_type,
            confirm_timeout=OHM_CONFIRM_TIMEOUT_S,
        )
        if r.get("confirmed") is False:
            raise HomeAssistantError(f"Alert silence refused: {r.get('error')}")