"""Data update coordinator for the OBLO cloud mode."""
import asyncio
import logging
import time

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity, DataUpdateCoordinator, UpdateFailed,
)

from .cloud_api import (ObloAuthError, ObloCloudApi, ObloCloudError,
                           ObloRateLimitError, normalise_devices)
from .cloud_mqtt import ObloCommandError
from .const import DOMAIN, SCAN_INTERVAL_S


def diag_shape(payload):
    """Structural summary of a cloud payload: keys and type ONLY, never values.

    Exists because the v0.2.13 raw dump wrote `secureId` (the AES key seed for
    local gateway MQTT credentials) and `hash` into the HA log in plaintext.
    Any field whose value could be a credential is simply not rendered here.
    """
    items = payload if isinstance(payload, list) else [payload]
    items = [i for i in items if isinstance(i, dict)]
    if not items:
        return {"count": 0}
    first = items[0]
    return {
        "count": len(items),
        "keys": sorted(first.keys()),
        "type": first.get("type"),
        "has_devices": isinstance(first.get("devices"), list),
        "device_count": len(first.get("devices") or []),
    }


_TRUE = {"1", "true", "on", "yes"}
_FALSE = {"0", "false", "off", "no"}


def _norm_boolish(v):
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return None


def values_match(sent, readback):
    """Loose equality for gateway values.

    Gateway stores all properties as TEXT in ohm.db. A switch may come back as
    "1", "true", "on", 1, or True. Treat them as equivalent.
    """
    if str(sent).strip() == str(readback).strip():
        return True
    a, b = _norm_boolish(sent), _norm_boolish(readback)
    if a is not None and b is not None:
        return a == b
    try:
        return float(sent) == float(readback)
    except (TypeError, ValueError):
        return False


def normalise_ohm_objects(ohm_objects):
    """Convert OHM object_list from cloud MQTT to internal device shape.

    OHM object has:
      - id (str)
      - device_name (str)
      - service_list: list of {name, category, property_list: [{name, value}]}

    Internal device shape (what build_entities consumes):
      - id, name, class (optional), services: list of {service, props: dict,
        service_type: optional canonical type from OHM (category/type)}
    """
    devices = []
    for obj in ohm_objects or []:
        did = obj.get("id")
        if not did:
            continue
        services = []
        for svc in obj.get("service_list") or []:
            sname = svc.get("name")
            if not sname:
                continue
            props = {}
            for prop in svc.get("property_list") or []:
                pname = prop.get("name")
                pval = prop.get("value")
                if pname:
                    props[pname] = pval
            # Preserve the canonical type from OHM (category or type) so that
            # the write path can address a service by type as well as by alias.
            # Does not affect entity output; tests assert entity count unchanged.
            service_type = svc.get("type") or svc.get("category")
            services.append({
                "service": sname,
                "props": props,
                "service_type": service_type,
            })
        devices.append({
            "id": did,
            "name": obj.get("device_name") or did,
            "class": "ohm",  # mark as OHM-sourced
            "services": services,
        })
    return devices

_LOGGER = logging.getLogger(__name__)


def _weight(device):
    """How much detail a device record carries: (#services, #props)."""
    services = device.get("services") or []
    props = sum(len(s.get("props") or []) for s in services)
    return (len(services), props)


def merge_devices(*device_lists):
    """De-dup device dicts across payloads, keeping the richest record.

    The gateway list and the hg/info list describe the same devices; the
    second usually carries more service props, so the merge must compare
    prop counts -- not just the number of services.
    """
    merged = {}
    for devices in device_lists:
        for dev in devices or []:
            did = dev.get("id")
            if did is None:
                continue
            prev = merged.get(did)
            if prev is None or _weight(dev) > _weight(prev):
                merged[did] = dev
    return list(merged.values())


class ObloDataCoordinator(DataUpdateCoordinator):
    """Polls the cloud for gateway + device state, re-authenticating on expiry."""

    def __init__(self, hass: HomeAssistant, api: ObloCloudApi):
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}_coordinator",
            update_interval=__import__("datetime").timedelta(seconds=SCAN_INTERVAL_S))
        self.api = api
        self.devices = []
        # Optional cloud MQTT/OHM client. REST only returns the gateway object;
        # the device inventory with service_list lives behind OHM commands, so
        # when a client is attached its object_list is merged in and wins on
        # richness. Interface expected (see cloud_mqtt.py):
        #   await client.async_object_list() -> list of OHM objects
        #   await client.async_close()
        self.ohm = None

    def set_ohm_client(self, client):
        """Attach the cloud MQTT/OHM client (or None to disable)."""
        self.ohm = client

    # ── control path ────────────────────────────────────────────────────
    # PUBACK proves nothing on this broker (ACKs are unconditional and ACLs
    # filter silently), and a `code: 0` reply only means the gateway ACCEPTED
    # a command. So a write is never reported as success on the strength of
    # the reply alone -- it is confirmed by reading the property back.

    async def async_read_service_property(self, device_id, service_name,
                                          property_name, service_type=None,
                                          timeout=None):
        """Read one property straight from the gateway (bypasses the cache)."""
        if self.ohm is None:
            raise ObloCloudError("OHM client not attached")
        return await self.ohm.async_get_property(
            device_id, service_name, property_name,
            service_type=service_type, timeout=timeout)

    async def async_write_service_property(self, device_id, service_name,
                                           property_name, value,
                                           service_type=None,
                                           confirm_timeout=2.0):
        """Write one property, then confirm it by read-back.

        Returns {"sent", "readback", "confirmed", "error"} where `confirmed`
        is True only when a read-back matched the sent value, False when the
        gateway answered but the value did not change, and None when the
        read-back itself failed (unknown -- never claim success on unknown).
        """
        result = {"sent": value, "readback": None, "confirmed": None,
                  "error": None}
        if self.ohm is None:
            result["error"] = "OHM client not attached (REST-only mode)"
            _LOGGER.warning("OHM write unavailable: %s", result["error"])
            return result

        try:
            await self.ohm.async_set_property(
                device_id, service_name, property_name, value,
                service_type=service_type)
        except ObloCommandError as err:
            # The gateway considered and refused it. Not an auth problem, and
            # retrying risks double-actuation.
            result["confirmed"] = False
            result["error"] = f"refused: {err}"
            _LOGGER.warning(
                "OHM refused the write device=%s service=%s property=%s: %s",
                device_id, service_name, property_name, err)
            return result
        except Exception as err:  # noqa: BLE001 - transport noise, never auth
            result["error"] = f"send failed: {err}"
            _LOGGER.warning(
                "OHM write failed device=%s service=%s property=%s: %s",
                device_id, service_name, property_name, err)
            return result

        # Read back until it matches or the window closes. A device that
        # acknowledges instantly but reports the old value is a silent no-op,
        # which is the failure mode a bare "command accepted" log would hide.
        deadline = time.monotonic() + max(0.0, confirm_timeout)
        read_err = None
        while True:
            budget = deadline - time.monotonic()
            try:
                result["readback"] = await self.ohm.async_get_property(
                    device_id, service_name, property_name,
                    service_type=service_type,
                    timeout=max(0.5, min(2.0, budget)) if budget else 0.5)
                read_err = None
            except Exception as err:  # noqa: BLE001
                read_err = err
            if read_err is None and values_match(value, result["readback"]):
                result["confirmed"] = True
                break
            if time.monotonic() >= deadline:
                result["confirmed"] = False if read_err is None else None
                break
            await asyncio.sleep(0.25)

        if result["confirmed"]:
            _LOGGER.info(
                "OHM write confirmed device=%s service=%s property=%s "
                "value=%r readback=%r",
                device_id, service_name, property_name, value,
                result["readback"])
            self._poke_device(device_id, service_name, property_name,
                              result["readback"])
        elif result["confirmed"] is False:
            _LOGGER.warning(
                "OHM write unconfirmed device=%s service=%s property=%s "
                "sent=%r readback=%r (gateway answered, value unchanged)",
                device_id, service_name, property_name, value,
                result["readback"])
        else:
            _LOGGER.warning(
                "OHM write unconfirmed device=%s service=%s property=%s "
                "sent=%r (read-back failed: %s)",
                device_id, service_name, property_name, value, read_err)
            result["error"] = f"read-back failed: {read_err}"
        return result

    def _poke_device(self, device_id, service_name, property_name, value):
        """Update the cached device list so the UI flips without waiting a poll."""
        for dev in self.devices or []:
            if dev.get("id") != device_id:
                continue
            for svc in dev.get("services") or []:
                if svc.get("service") != service_name:
                    continue
                props = svc.get("props")
                if isinstance(props, dict):
                    props[property_name] = value
                elif isinstance(props, list):
                    for i, pair in enumerate(props):
                        if (isinstance(pair, (list, tuple)) and pair
                                and str(pair[0]).lower() == property_name.lower()):
                            props[i] = [pair[0], value]
                            break
                    else:
                        props.append([property_name, value])
                self.data = list(self.devices)
                return True
        return False

    async def _async_refresh_relogin(self):
        """Refresh after re-authentication once."""
        await self.api.async_login()
        return await self._async_fetch()

    async def _async_fetch(self):
        gateways = await self.api.async_list_gateways()
        details = await self.api.async_gateway_devices()
        rest_devices = merge_devices(normalise_devices(gateways),
                                     normalise_devices(details))

        # --- v0.2.23: honest uplink diagnostics ---
        # v0.2.21 called lastTimeStatusUpdated a heartbeat and inferred a dead
        # tunnel whenever it did not move. It is NOT a heartbeat: the cloud
        # writes it only on online<->offline transitions, so "unchanged" is the
        # normal steady state and advanced=False was a false negative. Removed
        # outright rather than relabelled -- a field that reads like a verdict
        # gets read as one, and the published release note said "advance or
        # escalate to OBLO", which was wrong.
        # Raw values stay at DEBUG for forensics. The real liveness signal is
        # ONLINE_EVENT (gtw_online_status_changed) from the evt stream, logged
        # in cloud_mqtt._on_message.
        for gw in (gateways or []):
            if isinstance(gw, dict):
                _LOGGER.debug(
                    "UPLINK sn=%s connectionStatus=%s lastTimeOnline=%s "
                    "lastTimeStatusUpdated=%s (transition timestamp, not a heartbeat)",
                    gw.get("serialNumber"),
                    gw.get("connectionStatus"),
                    gw.get("lastTimeOnline"),
                    gw.get("lastTimeStatusUpdated"),
                )

        # OHM over cloud MQTT is the only source that carries service_list.
        # Failure here must not break the poll -- fall back to REST data and
        # let the next scheduled refresh retry the MQTT round trip.
        ohm_devices = []
        if self.ohm is not None:
            try:
                objects = await self.ohm.async_object_list()
                ohm_devices = normalise_ohm_objects(objects)
                if ohm_devices:
                    _LOGGER.debug("OHM returned %d objects", len(ohm_devices))
            except ObloAuthError:
                raise
            except ObloRateLimitError:
                raise
            except ObloCommandError as err:
                # The gateway answered and refused (code, message). Distinct line
                # because this is a device-side answer, not a transport failure --
                # and it must not be mistaken for "session expired".
                _LOGGER.warning(
                    "OHM refused the request (code=%s), using REST data: %s",
                    getattr(err, "code", None), err)
            except Exception as err:
                _LOGGER.warning("OHM object list failed, using REST data: %s", err)

        self.devices = merge_devices(rest_devices, ohm_devices)

        # --- DIAGNOSTIC (v0.2.17) ---
        # The v0.2.13 raw dump printed `secureId` (the AES key seed for local
        # gateway MQTT credentials) and `hash` straight into the HA log. It had
        # done its job, so it is gone. What remains is a redacted structural
        # summary: field NAMES only, no credential material, no device values.
        if not getattr(self, "_diag_dumped", False):
            self._diag_dumped = True
            try:
                _LOGGER.warning("DIAG gateways=%s details=%s",
                                diag_shape(gateways), diag_shape(details))

                from .entity_map import build_entities
                entities = build_entities(self.devices)
                svc_names = set()
                for d in self.devices:
                    svc_names.update(s["service"] for s in d.get("services", []))
                _LOGGER.warning(
                    "DIAG SUMMARY: devices=%d total_services=%d mapped=%d entities=%d "
                    "services=%s",
                    len(self.devices),
                    sum(len(d.get("services", [])) for d in self.devices),
                    len(svc_names), len(entities), sorted(svc_names)[:30])
            except Exception as e:  # never let diagnostics break the poll
                _LOGGER.warning("DIAG summary failed: %s", e)

        return self.devices

    async def _async_update_data(self):
        try:
            return await self._async_fetch()
        except ObloRateLimitError as err:
            # Account temporarily blocked: even correct password fails.
            # Don't re-login (that would hit the same block) and don't retry
            # immediately -- return cached data and let the scheduled poll
            # pick it up when the block lifts (~15 min).
            _LOGGER.warning("cloud account blocked, keeping cached state: %s", err)
            return self.devices
        except ObloAuthError as err:
            _LOGGER.info("cloud session expired (%s), re-logging in", err)
            try:
                return await self._async_refresh_relogin()
            except ObloAuthError as err2:
                # credentials themselves rejected -> HA shows the reauth form
                _LOGGER.error("cloud re-login rejected: %s", err2)
                raise ConfigEntryAuthFailed(str(err2)) from err2
            except ObloRateLimitError as err2:
                # re-login also hit the block -- don't retry, keep cached
                _LOGGER.warning("cloud re-login blocked, keeping cached state: %s", err2)
                return self.devices
            except ObloCloudError as err2:
                raise UpdateFailed(str(err2)) from err2
        except ObloCloudError as err:
            raise UpdateFailed(str(err)) from err
