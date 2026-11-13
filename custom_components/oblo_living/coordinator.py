"""Data update coordinator for the OBLO cloud mode."""
import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity, DataUpdateCoordinator, UpdateFailed,
)

from .cloud_api import (ObloAuthError, ObloCloudApi, ObloCloudError,
                           ObloRateLimitError, normalise_devices)
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


def normalise_ohm_objects(ohm_objects):
    """Convert OHM object_list from cloud MQTT to internal device shape.

    OHM object has:
      - id (str)
      - device_name (str)
      - service_list: list of {name, category, property_list: [{name, value}]}

    Internal device shape (what build_entities consumes):
      - id, name, class (optional), services: list of {service, props: dict}
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
            services.append({"service": sname, "props": props})
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

    async def _async_refresh_relogin(self):
        """Refresh after re-authentication once."""
        await self.api.async_login()
        return await self._async_fetch()

    async def _async_fetch(self):
        gateways = await self.api.async_list_gateways()
        details = await self.api.async_gateway_devices()
        rest_devices = merge_devices(normalise_devices(gateways),
                                     normalise_devices(details))

        # --- v0.2.21 machine-readable uplink gate ---
        # The cloud's own contact record is the only unambiguous uplink signal
        # we have: the local brokers are ACL-filtered so nothing can be
        # measured on the LAN, and MQTT 3.1.1 PUBACKs are unconditional.
        # If lastTimeStatusUpdated stops advancing while connectionStatus
        # still says "online", that flag is cached and the gateway's OMB
        # tunnel is dead -- no client change can fix it.
        # Greppable line: "UPLINK sn=... advanced=... connectionStatus=... lastTimeOnline=... lastTimeStatusUpdated=..."
        # Throttle: log immediately on transition, or heartbeat every 10 min
        # (avoids spamming the log with repeated advanced=False while still
        # capturing every state change: first -> False -> True -> ...)
        import time
        prev = getattr(self, "_last_status", {})
        prev_adv = getattr(self, "_last_adv", {})
        last_log = getattr(self, "_last_uplink_log", 0.0)
        now = time.time()
        cur = {}
        for gw in (gateways or []):
            if not isinstance(gw, dict):
                continue
            sn = gw.get("serialNumber")
            ts = gw.get("lastTimeStatusUpdated")
            cur[sn] = ts
            if sn not in prev:
                adv = "first"
            elif ts != prev.get(sn):
                adv = "True"
            else:
                adv = "False"
            if adv != prev_adv.get(sn) or (now - last_log) >= 600:
                _LOGGER.warning(
                    "UPLINK sn=%s advanced=%s connectionStatus=%s lastTimeOnline=%s "
                    "lastTimeStatusUpdated=%s",
                    sn,
                    adv,
                    gw.get("connectionStatus"),
                    gw.get("lastTimeOnline"),
                    ts,
                )
                prev_adv[sn] = adv
                last_log = now
        self._last_status = cur
        self._last_adv = prev_adv
        self._last_uplink_log = last_log

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
