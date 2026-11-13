"""Data update coordinator for the OBLO cloud mode."""
import asyncio
import logging
import time
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity, DataUpdateCoordinator, UpdateFailed,
)

from .cloud_api import (ObloAuthError, ObloCloudApi, ObloCloudError,
                           ObloRateLimitError, normalise_devices)
from .cloud_mqtt import ObloCommandError
from .const import DOMAIN, SCAN_INTERVAL_S
from .entity_map import APP_CLASS_NAMES


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


def _stamp():
    """Millisecond wall clock, so our writes and inbound evt lines can be ordered."""
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _first(params, keys):
    """First present, non-None value among `keys` in a dict. None if absent.

    Event payload key names are not documented; the SPA and the gateway are not
    guaranteed to agree on camelCase. Rather than pick one and silently match
    nothing, accept the plausible spellings and report which ones we tried.
    """
    for key in keys:
        if key in params and params[key] is not None:
            return params[key]
    return None


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


# Two boolean dialects are in play on this gateway: numeric ("1"/"0") and word
# ("true"/"false"). The app's own persisted writes (ScenarioDeviceSetting
# PropValue) use the word form, while ohm.db text can be either. Sending the
# wrong dialect is refused or silently ignored, and there is exactly one
# actuator on this hardware to settle it with -- so don't guess: mirror the
# dialect the device is already reporting.
_DIALECTS = (
    ({"1"}, {"0"}),
    ({"true"}, {"false"}),
    ({"on"}, {"off"}),
    ({"yes"}, {"no"}),
)
_DEFAULT_ON, _DEFAULT_OFF = "true", "false"

# OutletService/BasicService are the canonical OHM service names that map to
# switches. The OHM service_id used by the gateway for targeted reads is
# "outlet0"/"basic0" (see entity_map.py SERVICE_TO_OHM and entity_map).
SWITCH_SERVICE_IDS = {
    "OutletService": "outlet0",
    "BasicService": "basic0",
}

# Z-Wave bus id per device, when known. Absent -> the app's "OnvifIpBus"
# fallback, which is what it sends for a gateway-attached device.
SWITCH_BUS_IDS: dict = {}


def _prop_value(props, key):
    """Read a property from dict or list-of-pairs, case-insensitive."""
    if not props:
        return None
    if isinstance(props, dict):
        if key in props:
            return props[key]
        for k, v in props.items():
            if str(k).lower() == str(key).lower():
                return v
        return None
    # list of [k, v] pairs
    for pair in props:
        if isinstance(pair, (list, tuple)) and pair and str(pair[0]).lower() == str(key).lower():
            return pair[1]
    return None


def _poke_props(props, key, value):
    """Write a property into dict or list-of-pairs, case-insensitive."""
    if isinstance(props, dict):
        props[key] = value
        return
    # list of [k, v] pairs
    for i, pair in enumerate(props):
        if isinstance(pair, (list, tuple)) and pair and str(pair[0]).lower() == str(key).lower():
            props[i] = [pair[0], value]
            return
    props.append([key, value])


def write_token_for(current, want_on):
    """Return the on/off token in the same dialect as `current`.

    `current` is the device's present value for the property being written.
    Unknown/absent -> the word form, which is what the OBLO app itself writes.

    This is not cosmetic: a `switch.turn_on` that sends "1" to a device that
    speaks "true"/"false" produces `confirmed=False` and raises, so a perfectly
    healthy relay looks broken. Mirroring the observed dialect removes that
    coin flip without inventing a value the gateway may reject.
    """
    cur = "" if current is None else str(current).strip().lower()
    for on_set, off_set in _DIALECTS:
        if cur in on_set or cur in off_set:
            tok_on = next(iter(on_set))
            tok_off = next(iter(off_set))
            return tok_on if want_on else tok_off
    return _DEFAULT_ON if want_on else _DEFAULT_OFF


def ohm_object_shape(objects):
    """Names-only snapshot of the raw OHM object_list structure.

    Returns a dict of top-level keys from the first object and, if present,
    the keys of the first service_list entry. No values are ever included.
    """
    if not objects:
        return {}
    first = objects[0]
    if not isinstance(first, dict):
        return {"type": type(first).__name__}
    top = sorted(first.keys())
    svc = []
    for v in first.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            svc = sorted(v[0].keys())
            break
    return {"object": top, "service_list[0]": svc}


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
            "class": (obj.get("class") if str(obj.get("class") or "")
                      in APP_CLASS_NAMES else "ohm"),
            "services": services,
        })
    return devices

_LOGGER = logging.getLogger(__name__)


def _weight(device):
    """How much detail a device record carries: (#services, #props)."""
    services = device.get("services") or []
    props = sum(len(s.get("props") or []) for s in services)
    return (len(services), props)


def _real_class(device):
    """The device's real class string, or "" when the source only sent a marker.

    normalise_ohm_objects() stamps every OHM record with class "ohm" because
    the live object_list carries no class field. That marker must never be
    allowed to overwrite a real class that another payload supplied: the class
    is what APP_CLASS_NAMES keys on, so losing it silently costs every device
    its marketing name on the device page.
    """
    cls = str(device.get("class") or "").strip()
    if not cls or cls.lower() in ("ohm", "hg"):
        return ""
    return cls


def merge_devices(*device_lists):
    """De-dup device dicts across payloads, keeping the richest record.

    The gateway list and the hg/info list describe the same devices; the
    second usually carries more service props, so the merge must compare
    prop counts -- not just the number of services.

    Richness cannot decide everything: the OHM record wins on props but only
    ever says class "ohm", while the REST record knows the real class. The
    winner keeps its services/props, and adopts a real class from any other
    record for the same id. Without this the app's class->name table can never
    fire on the live path, and every device silently loses its real name.
    """
    merged = {}
    classes = {}
    for devices in device_lists:
        for dev in devices or []:
            did = dev.get("id")
            if did is None:
                continue
            # Key on the string form: the REST payload carries numeric ids and
            # the OHM object_list carries strings, so keying on the raw value
            # would keep both and hand build_entities every device twice --
            # two records with the same unique_id, differing only in id type.
            key = str(did)
            real = _real_class(dev)
            if real and key not in classes:
                classes[key] = real
            prev = merged.get(key)
            if prev is None or _weight(dev) > _weight(prev):
                merged[key] = dict(dev)
    for key, dev in merged.items():
        if not _real_class(dev) and key in classes:
            dev["class"] = classes[key]
    return list(merged.values())


class ObloDataCoordinator(DataUpdateCoordinator):
    """Polls the cloud for gateway + device state, re-authenticating on expiry."""

    def __init__(self, hass: HomeAssistant, api: ObloCloudApi,
                 scan_interval: int = SCAN_INTERVAL_S,
                 entry_id: str | None = None):
        """scan_interval is the poll period in seconds (options flow, min 15).

        The poll is a safety net: gateway events patch the cache as they
        arrive. Each poll is three round trips against a cloud API that has
        already rate-limited this account once, so the caller passes the
        configured interval rather than a hard constant.

        entry_id is passed by async_setup_entry so entities can build
        entry-scoped device identifiers (prevents two gateways from
        colliding on the same OHM object id like "1052").
        """
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}_coordinator",
            update_interval=__import__("datetime").timedelta(
                seconds=int(scan_interval)))
        self.api = api
        self.devices = []
        self.entry_id = entry_id
        self.gateway_serial = None
        # Gateway device-page metadata, filled from the gateway object in the
        # poll. None until the first successful poll.
        self.gateway_name = None
        self.gateway_model = None
        self.gateway_sw_version = None
        self.gateway_hw_version = None
        # Optional cloud MQTT/OHM client. REST only returns the gateway object;
        # the device inventory with service_list lives behind OHM commands, so
        # when a client is attached its object_list is merged in and wins on
        # richness. Interface expected (see cloud_mqtt.py):
        #   await client.async_object_list() -> list of OHM objects
        #   await client.async_close()
        self.ohm = None
        # One-shot: have we spent the targeted State reads for this session?
        self._states_resolved = False
        # key -> coroutine function that undoes an in-flight actuation
        self._alert_stoppers = {}
        # key -> monotonic press generation. A Silence press bumps the
        # generation so an in-flight Sound press can tell it has been
        # superseded and must not go on to write "true".
        self._alert_generation = {}
        # (device_id, service_name) -> {property_name: value}. Holds the last
        # value the GATEWAY itself reported for a property (live evt, or the
        # read-back of a confirmed write). The poll rebuilds the device tree
        # from OHM/REST payloads, and the gateway does not persist live state
        # (outlet0/State, both SimpleAlert props have no rows in ohm.db), so a
        # property present after an evt can be absent one poll later. Without
        # this overlay the entity falls back to unknown mid-flight; with it the
        # freshest gateway-sourced value survives until the gateway says
        # otherwise. Cleared on reconnect: state from before a gap in the
        # connection is not evidence about now.
        self._evt_overlay = {}

    def _overlay(self):
        """Return the gateway-sourced value overlay, creating it on demand.

        Created lazily rather than only in __init__ so a coordinator built
        without __init__ (test harnesses do this to skip HA's base-class
        setup) cannot crash the evt handler with an AttributeError.
        """
        overlay = getattr(self, "_evt_overlay", None)
        if overlay is None:
            overlay = self._evt_overlay = {}
        return overlay

    # ── locally remembered writes (v0.2.40) ─────────────────────────────
    # PropAlertType is write-only on the gateway: writing it produces no evt
    # and no poll row (object 15 has zero property rows in ohm.db), so any
    # read-back returns the gateway default "'0'" and every idle type write
    # looks unconfirmed. That is a property of the read, not of the write.
    # We therefore remember what WE last wrote, per (device, service, prop),
    # and the Sound button uses that instead of a read-back it cannot win.
    #
    # Kept separate from _evt_overlay on purpose: the evt overlay means "the
    # gateway told us this". This dict means "we asked for this and the
    # gateway did not object". Conflating them would make a local write look
    # like gateway truth in _reapply_overlay, which would then keep a stale
    # local value even after the poll started carrying the real one.
    def remember_local_write(self, device_id, service_name, prop, value):
        """Record a value we wrote that the gateway will not read back."""
        if getattr(self, "_local_writes", None) is None:
            self._local_writes = {}
        self._local_writes[(str(device_id), str(service_name), str(prop))] = value
        return value

    def local_write(self, device_id, service_name, prop):
        """Last value we wrote for (device, service, prop), or None."""
        store = getattr(self, "_local_writes", None) or {}
        return store.get((str(device_id), str(service_name), str(prop)))

    def forget_local_writes(self):
        """Drop remembered writes. Called on reconnect: new session, new mood."""
        self._local_writes = {}
        self._prop_disagreements = set()

    # ── local authority over write-only properties (v0.2.39) ────────────
    # PropAlertType is write-only: the gateway never reports it back. If a
    # contradicting value ever reaches us anyway (a snapshot evt on connect,
    # or a default read), letting it win would pin "0" into _evt_overlay and
    # _reapply_overlay would restore "0" after every poll -- the user's
    # remembered Alarm would be unreachable forever, and the select would
    # silently show Notification while the button sounded it. So for these
    # properties the remembered write wins for display and press; a differing
    # gateway value is reported ONCE at INFO instead of being applied.
    LOCAL_AUTHORITY_PROPS = ("PropAlertType",)

    def _local_authority(self, device_id, service_name, prop):
        """Our remembered write for a local-authority prop, else None."""
        if str(prop) not in self.LOCAL_AUTHORITY_PROPS:
            return None
        return self.local_write(device_id, service_name, prop)

    def _note_gateway_disagreement(self, device_id, service_name, prop,
                                   gateway_value, local_value):
        """Log a contradicting gateway report once per distinct value pair."""
        seen = getattr(self, "_prop_disagreements", None)
        if seen is None:
            seen = self._prop_disagreements = set()
        key = (str(device_id), str(service_name), str(prop),
               str(gateway_value), str(local_value))
        if key in seen:
            return False
        seen.add(key)
        _LOGGER.info(
            "OBLO: gateway reports %s=%r after our write of %r "
            "(device=%s service=%s) -- write may not have latched",
            prop, gateway_value, local_value, device_id, service_name)
        return True

    def push_local_update(self):
        """Push cached data to entities after a local, non-poll change.

        DataUpdateCoordinator always provides async_set_updated_data, but the
        offline test harness builds this class without HA's base __init__, so
        the attribute can be absent. Returning False keeps that a no-op rather
        than an AttributeError in the middle of a write.
        """
        notify = getattr(self, "async_set_updated_data", None)
        if notify is None:
            return False
        notify(list(self.data or []))
        return True

    def set_ohm_client(self, client):
        """Attach the cloud MQTT/OHM client (or None to disable)."""
        # A new client means a new session. Anything we learned across the gap
        # may have changed while we were unable to hear the gateway, so the
        # overlay must not outlive the connection that populated it.
        if client is None or client is not getattr(self, "ohm", None):
            if self._overlay():
                _LOGGER.debug("OBLO evt overlay cleared on reconnect (%d entries)",
                              len(self._overlay()))
            self._evt_overlay = {}
            # A remembered write belongs to the session that issued it. A new
            # session may be a restarted gateway with a different stored type,
            # so not trusting it is the only honest option.
            self.forget_local_writes()
            # A new session may have new state to discover.
            self._states_resolved = False
        self.ohm = client

    def set_scan_interval(self, seconds):
        """Change the poll period in place (options flow, no reload needed).

        DataUpdateCoordinator re-schedules its next refresh when
        update_interval is assigned, so writing this attribute is the whole
        mechanism. Clamped to the same bounds the options schema enforces so a
        hand-edited options dict cannot pin the poller to a hostile rate.
        """
        from datetime import timedelta
        secs = max(15, int(seconds))
        self.update_interval = timedelta(seconds=secs)
        _LOGGER.info("OBLO poll interval set to %ss", secs)
        return secs

    # ── in-flight actuation bookkeeping ─────────────────────────────────
    # A sound-alert press writes "true", sleeps, then writes "false". If HA
    # stops or reloads during that sleep the coroutine is cancelled and the
    # "false" is never sent -- the siren keeps sounding with nobody left to
    # silence it. Anything that must be undone on teardown registers a stopper
    # here; async_unload_entry drains them while the OHM client is still open.
    def register_alert_stopper(self, key, stopper):
        self._alert_stoppers[key] = stopper

    def unregister_alert_stopper(self, key):
        self._alert_stoppers.pop(key, None)

    # ── press generation ────────────────────────────────────────────────
    # Silence is a stop, but a Sound press is a sequence of writes (set type,
    # start, wait, stop). If Andrew presses Silence while that sequence is
    # still preparing, the stop lands first and the start lands after it --
    # the siren re-sounds *after* being silenced. Each press increments the
    # generation; a Sound press compares its own generation before it writes
    # "true" and abandons the press if Silence has overtaken it.
    def alert_generation(self, key):
        return self._alert_generation.get(key, 0)

    def bump_alert_generation(self, key):
        gen = self._alert_generation.get(key, 0) + 1
        self._alert_generation[key] = gen
        return gen

    # ── gateway events ──────────────────────────────────────────────────
    # The gateway publishes live changes on oblo/<uid>/gtw/<SN>/+/evt. Until
    # v0.2.32 nothing consumed them: _async_start_ohm subscribed with a
    # no-op lambda, so every event was received and dropped. That also made
    # one question unanswerable -- whether the gateway ends a siren on its
    # own. Every event is now logged with a millisecond stamp, so our own
    # write lines and an incoming PropAlertStartStop=false can be ordered.
    async def async_attach_events(self):
        """Subscribe the coordinator to gateway events (idempotent, best-effort)."""
        if self.ohm is None:
            return False
        subscribe = getattr(self.ohm, "async_subscribe_events", None)
        if subscribe is None:
            return False
        try:
            await subscribe(self.handle_event)
            return True
        except Exception as err:  # noqa: BLE001 - events are a bonus, not setup
            _LOGGER.warning("OBLO evt subscribe failed: %s", err)
            return False

    def handle_event(self, payload):
        """Apply one gateway event. Runs on the HA loop via call_soon_threadsafe."""
        if not isinstance(payload, dict):
            _LOGGER.debug("OBLO evt (non-dict): %r", payload)
            return
        evt_name = payload.get("name") or payload.get("type") or "?"
        # Broker may deliver flat {device_id, service_name, property_name, property_value}
        # OR wrapped {params: {...}}. Accept both; prefer nested if it's a non-empty dict.
        nested = payload.get("params")
        params = nested if isinstance(nested, dict) and nested else payload
        prop = _first(params, ("propertyName", "property", "property_name", "prop"))
        if prop is None:
            # Not a property change (online status, config, ...). Keep it quiet
            # unless someone is debugging.
            _LOGGER.debug("OBLO evt %s at %s keys=%s",
                          evt_name, _stamp(),
                          sorted(params) if params else sorted(payload))
            return
        did = _first(params, ("deviceId", "device_id", "objId", "objectId", "id"))
        svc = _first(params, ("serviceName", "service", "service_name"))
        val = _first(params, ("value", "newValue", "new_value", "val", "property_value"))
        # Alert properties are logged at INFO: they are the only live proof we
        # can get that a siren started or stopped on the gateway's own terms.
        # Motion sensors: momentary, evt-driven, log at INFO so they're visible
        # without DEBUG config.
        is_motion = str(prop) in ("State", "Motion", "Occupancy") and \
                    str(svc) in ("PirService", "pir0", "MotionDetectorService")
        if str(prop).startswith("PropAlert") or is_motion:
            _LOGGER.info("OBLO evt %s at %s device=%s service=%s %s=%r",
                         evt_name, _stamp(), did, svc, prop, val)
        else:
            _LOGGER.debug("OBLO evt %s at %s device=%s service=%s %s=%r",
                          evt_name, _stamp(), did, svc, prop, val)
        # Write-only props: our remembered write outranks a contradicting
        # gateway report. Applying it would pin the default into the overlay,
        # and _reapply_overlay would then restore it after every poll, making
        # the user's choice permanently unreachable. Log once, change nothing.
        if val is None:
            # An event that names a property but carries no value is not
            # evidence of anything. Applying it would blank a good value back
            # to Unknown, which is the exact symptom we are fixing.
            _LOGGER.debug("OBLO evt %s device=%s service=%s %s has no value; ignored",
                          evt_name, did, svc, prop)
            return
        local_val = self._local_authority(did, svc, prop)
        if local_val is not None and str(val) != str(local_val):
            self._note_gateway_disagreement(did, svc, prop, val, local_val)
            return
        canon = self._apply_property_change(did, svc, prop, val)
        if canon:
            # Remember it under the CANONICAL service instance name, which is
            # what `_reapply_overlay` looks up (the tree's `service` field).
            self._overlay().setdefault((str(did), str(canon)), {})[str(prop)] = val
            # Push the patched device list to entities without waiting for the
            # 60 s poll. No MQTT round-trip, no confirmation semantics.
            self.async_set_updated_data(list(self.data or []))

    def _apply_property_change(self, did, svc_name, prop, value):
        """Write one property change into the cached device tree.

        Returns the CANONICAL service instance name (`svc["service"]`, e.g.
        `pir0`) when something actually changed, so the caller can (a) notify
        entities only on a real update and (b) file its overlay entry under the
        same key `_reapply_overlay` looks up. Returns False when nothing
        changed or nothing matched.

        Returning the canonical name rather than a bare True is the fix for the
        "ON for one push, then Unknown at the next poll" gap: the event names
        the TYPE (`PirService`) while the tree and the overlay are keyed by the
        INSTANCE (`pir0`). Filing the overlay under the raw event name left the
        memory unreachable by `_reapply_overlay`.

        Service identity is matched in two passes. Pass 1 matches the instance
        name exactly. Pass 2 accepts the type, but only when exactly one
        service on the device carries it: a device with two outlets would
        otherwise have the event applied to the wrong relay, and a wrong relay
        is worse than a dropped event.
        """
        if did is None or prop is None:
            return False
        devices = self.data or []
        for dev in devices:
            if str(dev.get("id")) != str(did):
                continue
            services = [s for s in (dev.get("services") or []) if isinstance(s, dict)]
            if svc_name is None:
                targets = services
            else:
                targets = [s for s in services
                           if str(s.get("service")) == str(svc_name)]
                if not targets:
                    targets = [s for s in services
                               if str(s.get("service_type")) == str(svc_name)]
                    if len(targets) > 1:
                        _LOGGER.warning(
                            "OBLO evt: service type %s is ambiguous on device %s "
                            "(%d instances) -- event for %s ignored",
                            svc_name, did, len(targets), prop)
                        return False
            for svc in targets:
                props = svc.get("props")
                if props is None:
                    continue
                if str(_prop_value(props, prop)) == str(value):
                    return False
                _poke_props(props, prop, value)
                # Canonical key: what the tree calls this service instance.
                return svc.get("service") or svc_name
        return False

    async def async_stop_active_alerts(self):
        """Best-effort teardown: undo every in-flight actuation."""
        stoppers = list(self._alert_stoppers.items())
        self._alert_stoppers.clear()
        for key, stopper in stoppers:
            try:
                await stopper()
                _LOGGER.info("OBLO alert: stopped in-flight actuation %s on unload", key)
            except Exception as err:  # noqa: BLE001 - teardown must not raise
                _LOGGER.warning("OBLO alert: could not stop %s on unload: %s", key, err)

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
                                           confirm_timeout=2.0,
                                           matcher=None,
                                           verify=True):
        """Write one property, then confirm it by read-back.

        Returns {"sent", "ok", "observed", "readback", "confirmed", "error"}.
        `ok` is True only if the command reached the gateway (a refusal or a
        transport error leaves it False). `observed` is True if any read-back
        returned an actual value -- it is what separates "the gateway does not
        publish this property" from "the gateway published a value we do not
        like". `confirmed`
        is True only when a read-back matched the sent value, False when the
        gateway answered but the value did not change, and None when the
        read-back itself failed (unknown -- never claim success on unknown).

        `matcher(sent, readback)` overrides the default loose comparison for
        properties whose wire form and reported form differ (an enum written
        as "2" can be reported back as "WARNING").

        `verify=False` skips the read-back entirely and returns confirmed=None.
        Use it for properties the gateway does not persist or report back
        (PropAlertType on SimpleAlertService has no poll row at all): reading
        them can only return a default, so the "confirmation" would be a lie
        about the write rather than a measurement of it.
        """
        result = {"sent": value, "ok": False, "observed": False,
                  "readback": None, "confirmed": None, "error": None}
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

        # Reached only when the command was actually handed to the gateway.
        # `ok` distinguishes "the write went out but we cannot see the result"
        # (remember it locally) from "the write never happened" (raise).
        result["ok"] = True

        # Verify path: if caller explicitly opts out, the write went out and
        # we have no business pretending to measure something we were told not
        # to read. Return None for both confirmed and readback.
        if not verify:
            _LOGGER.info(
                "OHM write sent (not verified): device=%s service=%s property=%s value=%r",
                device_id, service_name, property_name, value)
            return result

        # Read back until it matches or the window closes. A device that
        # acknowledges instantly but reports the old value is a silent no-op,
        # which is the failure mode a bare "command accepted" log would hide.
        match = matcher or values_match
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
            if read_err is None and result["readback"] is not None:
                result["observed"] = True
            if read_err is None and match(value, result["readback"]):
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

    def poke_property(self, device_id, service_name, property_name, value):
        """Public alias for _poke_device: the gateway just told us this value."""
        return self._poke_device(device_id, service_name, property_name, value)

    def _poke_device(self, device_id, service_name, property_name, value):
        """Update the cached device list so the UI flips without waiting a poll.

        Only ever called with a value the gateway itself just returned as the
        read-back of a confirmed write, which is why it is safe to remember in
        the overlay exactly like a live evt: both are the gateway speaking.
        """
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
                self._overlay().setdefault(
                    (str(device_id), str(service_name)), {})[str(property_name)] = value
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
                # Remember the gateway serial: entities use it as via_device so
                # the device page shows them as "connected via" the gateway.
                sn = (gw.get("serialNumber") or gw.get("serial")
                      or gw.get("sn") or gw.get("deviceSN"))
                if sn and not self.gateway_serial:
                    self.gateway_serial = str(sn)
                # Gateway hardware metadata for the gateway device record. All
                # optional: a missing field must not break the poll, so each is
                # read tolerantly and only stored when it is a plain string.
                for attr, keys in (
                    ("gateway_name", ("deviceName", "name")),
                    ("gateway_model", ("model", "modelNumber", "deviceModel")),
                    ("gateway_sw_version", ("swVersion", "firmwareVersion",
                                            "softwareVersion", "version")),
                    ("gateway_hw_version", ("hwVersion", "hardwareVersion")),
                ):
                    if getattr(self, attr, None):
                        continue
                    for key in keys:
                        val = gw.get(key)
                        if isinstance(val, (str, int, float)) and str(val).strip():
                            setattr(self, attr, str(val).strip())
                            break
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
                # --- DIAGNOSTIC: what the live object_list actually carries ---
                # normalise_ohm_objects() reads exactly id, device_name and
                # service_list. Everything else in the payload is dropped --
                # including a device class, if one is there, which is why the
                # app's class->name table cannot fire on the live path. Field
                # NAMES only, never a value: this answers whether class /
                # fwVersion / busId travel at all without printing the device
                # inventory into the log.
                if not getattr(self, "_ohm_shape_dumped", False) and objects:
                    self._ohm_shape_dumped = True
                    try:
                        _LOGGER.warning("DIAG ohm object shape: %s",
                                        ohm_object_shape(objects))
                    except Exception as _shape_err:
                        _LOGGER.debug("DIAG ohm shape failed: %s", _shape_err)
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

        # A poll that returns nothing is not evidence that the house is empty.
        # Publishing [] makes every entity lose its state and flip to
        # `unknown` -- the exact false negative that hides a failed fetch behind
        # a plausible-looking value. Only rate-limit and auth failures kept the
        # cache before this guard; a *successful* poll whose OHM round-trip came
        # back empty would assign [] as truth. Keep the last good list instead
        # and say why, so the next failure is diagnosable from the log alone.
        fresh = merge_devices(rest_devices, ohm_devices)

        # One-shot targeted read for switch services whose State the poll omits.
        # This runs once per OHM session, before the cache is published, so the
        # entities' first read sees gateway truth where available. A value that
        # returns is real; nothing back -> nothing invented (Unknown is honest).
        if self.ohm is not None and fresh:
            await self._resolve_missing_states(fresh)

        if fresh:
            self.devices = fresh
        elif self.devices:
            _LOGGER.warning(
                "OBLO poll returned no devices (rest=%d, ohm=%d); keeping the "
                "%d cached device(s) rather than publishing an empty house",
                len(rest_devices or []), len(ohm_devices or []),
                len(self.devices))
        else:
            # First ever poll and nothing came back: there is no cache to
            # protect, so record it loudly and publish the (empty) truth.
            _LOGGER.warning(
                "OBLO poll returned no devices and no cache exists "
                "(rest=%d, ohm=%d); entities will be created empty",
                len(rest_devices or []), len(ohm_devices or []))
            self.devices = fresh

        # Re-apply what the gateway last told us for properties this payload
        # omits. The poll is a snapshot of what the gateway persists, not of
        # what is true right now, so an absent property is "no information",
        # not "value cleared" -- and a present one always wins, because it is
        # fresher than anything we remembered.
        if self._overlay():
            applied = self._reapply_overlay()
            if applied:
                _LOGGER.debug("OBLO poll kept %d evt-confirmed value(s)", applied)

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
                # Which class each device ended up with after the merge. This
                # is the difference between the device page reading "On/Off
                # Relay" (class survived from the REST list) and "Smart Relay"
                # (only the OHM marker arrived, so the label falls back to
                # service primacy). Model names are not secrets.
                _LOGGER.warning(
                    "DIAG merged classes: %s",
                    {str(d.get("id")): d.get("class") for d in self.devices})

                # Property NAMES (never values) for every service that has a
                # writable entity behind it. Answers, once per install and
                # without waiting for a live event, whether the poll payload
                # actually carries the property each entity reads:
                #   outlet0.State            -> is the relay overlay needed at
                #                               all, or is the poll sufficient?
                #   SimpleAlertService.*     -> can the alert entities ever
                #                               show a state before an evt?
                #   everything else outlet0  -> is DeviceType=16 the only decoy
                #                               in that service, or one of many?
                interesting = ("outlet", "OutletService", "SimpleAlertService",
                               "PowerConsumptionService")
                diag_props = {}
                for d in self.devices:
                    for s in d.get("services", []):
                        name = str(s.get("service") or "")
                        stype = str(s.get("service_type") or "")
                        if not any(m in name or m in stype for m in interesting):
                            continue
                        props = s.get("props")
                        if isinstance(props, dict):
                            names = sorted(str(k) for k in props)
                        elif isinstance(props, list):
                            names = sorted(str(p[0]) for p in props
                                           if isinstance(p, (list, tuple)) and p)
                        else:
                            names = []
                        diag_props[f"{d.get('id')}/{name}"] = names
                _LOGGER.warning("DIAG writable props: %s", diag_props)
            except Exception as e:  # never let diagnostics break the poll
                _LOGGER.warning("DIAG summary failed: %s", e)

        return self.devices

    async def async_refresh_state(self, device_id, service_name, property_name,
                                  settle=0.6):
        """Ask the gateway to re-poll one device, then read the property once.

        The app's device page has a "check status" button that publishes
        `check_device` (type=service_command) and then re-reads the property.
        That is the only way to obtain gateway truth for a property the poll
        omits without waiting for the next scheduled refresh -- and it is a
        refresh, not a guess: nothing is returned unless the gateway answers.

        Best-effort by design. A gateway that ignores `check_device` must not
        turn a completed write into an error, so every failure here returns
        None and the caller falls back to its remembered value.
        """
        if self.ohm is None:
            return None
        check = getattr(self.ohm, "async_check_device", None)
        bus_id = SWITCH_BUS_IDS.get(str(device_id))
        if check is not None:
            try:
                await check(bus_id, device_id)
            except Exception as err:  # noqa: BLE001 - optional refresh lever
                _LOGGER.debug(
                    "OHM check_device failed device=%s destination=%s (continuing): %s",
                    device_id, bus_id, err)
        if settle:
            await asyncio.sleep(settle)
        ohm_svc = SWITCH_SERVICE_IDS.get(service_name, service_name)
        dest = bus_id
        try:
            val = await self.ohm.async_get_property(
                device_id, ohm_svc, property_name, timeout=5.0)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "OHM refresh read failed device=%s destination=%s service=%s: %s",
                device_id, dest, ohm_svc, err)
            return None
        if val is not None:
            _LOGGER.info(
                "OHM refresh read device=%s destination=%s service=%s property=%s -> %r",
                device_id, dest, ohm_svc, property_name, val)
        return val

    async def _resolve_missing_states(self, devices):
        """One targeted read for switch services whose State the poll omits.

        The poll is a snapshot of what the gateway persists. For a relay it may
        carry no `State` row at all, which leaves the card with no truth source
        after a restart: nothing remembered, nothing polled. A targeted read is
        the only primitive that can recover it, and it is already proven -- the
        same `get_service_property_value` call the write path uses to confirm.

        Runs once per OHM session, for at most SWITCH_STATE_SERVICES x devices
        round trips, because this account has already been rate-limited once.
        A value that comes back is real and is written into the poll result, so
        entities see gateway truth. Nothing back -> nothing invented.
        """
        if self._states_resolved:
            return 0
        if self.ohm is None or not devices:
            return 0
        self._states_resolved = True

        resolved = 0
        for dev in devices:
            did = dev.get("id")
            if did is None:
                continue
            for svc in dev.get("services") or []:
                ohm_svc = SWITCH_SERVICE_IDS.get(svc.get("service"))
                if ohm_svc is None:
                    continue
                props = svc.get("props")
                if _prop_value(props, "State") is not None:
                    continue  # poll already answers; do not spend a round trip
                try:
                    val = await self.ohm.async_get_property(
                        did, ohm_svc, "State", timeout=5.0)
                except Exception as err:  # noqa: BLE001 - one read must not break the poll
                    _LOGGER.debug(
                        "OHM targeted State read failed device=%s service=%s: %s",
                        did, ohm_svc, err)
                    continue
                if val is None:
                    _LOGGER.info(
                        "OHM targeted State read returned nothing device=%s "
                        "service=%s -- entity stays unknown (honest)", did, ohm_svc)
                    continue
                _poke_props(props, "State", val)
                resolved += 1
                _LOGGER.info(
                    "OHM targeted State read device=%s service=%s -> %r",
                    did, ohm_svc, val)
        if resolved:
            _LOGGER.info("OBLO resolved %d switch state(s) by targeted read",
                         resolved)
        return resolved

    def _reapply_overlay(self) -> int:
        """Re-apply overlay for missing props; refresh remembered for present ones.

        For each remembered (device, service, property):
          - if the poll payload DOES carry the property: update remembered to
            the payload value (gateway's freshest word) and do NOT count as
            an overlay application.
          - if the payload OMITS the property: write the remembered value back
            and count it.

        This keeps the overlay invariant: "last value the gateway itself reported"
        — whether via live evt or a poll that included it.

        Exception: LOCAL_AUTHORITY_PROPS (PropAlertType). The gateway never
        reports those back, so a value that appears in the payload is a
        snapshot default, not a measurement. Our remembered write wins: the
        tree is forced to it, the differing payload value is logged once, and
        the overlay is NOT refreshed from the payload -- refreshing it is what
        made a single "0" permanent, restoring it after every poll.
        """
        applied = 0
        for dev in self.devices or []:
            for svc in dev.get("services") or []:
                key = (str(dev.get("id")), str(svc.get("service")))
                remembered = self._overlay().get(key) or {}
                props = svc.get("props")
                if isinstance(props, dict):
                    # Local-authority props first: force our value into the
                    # tree even when the payload carries a different one.
                    for name in self.LOCAL_AUTHORITY_PROPS:
                        local_val = self._local_authority(
                            dev.get("id"), svc.get("service"), name)
                        if local_val is None:
                            continue
                        k = next((pk for pk in props
                                  if str(pk).lower() == name.lower()), None)
                        if k is None:
                            props[name] = local_val
                            applied += 1
                        elif str(props[k]) != str(local_val):
                            self._note_gateway_disagreement(
                                dev.get("id"), svc.get("service"), name,
                                props[k], local_val)
                            props[k] = local_val
                            applied += 1
                        remembered.pop(name, None)
                    # No `if not props: continue` here. An empty props dict is
                    # still a service the overlay may have something to say
                    # about -- skipping it meant a property the gateway omits
                    # entirely could never be restored on a payload that
                    # carried no props at all. `if not remembered` below is the
                    # real gate: no memory, no work.
                    if not remembered:
                        continue
                    # Build case-insensitive map of payload props
                    current = {str(k).lower(): k for k in props}
                    for name, value in list(remembered.items()):
                        k = current.get(name.lower())
                        if k is None:
                            props[name] = value
                            applied += 1
                        else:
                            remembered[name] = props[k]
                elif isinstance(props, list):
                    for name in self.LOCAL_AUTHORITY_PROPS:
                        local_val = self._local_authority(
                            dev.get("id"), svc.get("service"), name)
                        if local_val is None:
                            continue
                        found = None
                        for pair in props:
                            if (isinstance(pair, (list, tuple)) and len(pair) >= 2
                                    and str(pair[0]).lower() == name.lower()):
                                found = pair
                                break
                        if found is None:
                            props.append([name, local_val])
                            applied += 1
                        elif str(found[1]) != str(local_val):
                            self._note_gateway_disagreement(
                                dev.get("id"), svc.get("service"), name,
                                found[1], local_val)
                            # Rebuild rather than mutate: a payload pair may be
                            # a tuple, which has no __setitem__.
                            props[props.index(found)] = (name, local_val)
                            applied += 1
                        remembered.pop(name, None)
                    for name, value in list(remembered.items()):
                        found = None
                        for pair in props:
                            if (isinstance(pair, (list, tuple)) and len(pair) >= 2
                                    and str(pair[0]).lower() == name.lower()):
                                found = pair
                                break
                        if found is None:
                            props.append([name, value])
                            applied += 1
                        else:
                            remembered[name] = found[1]
        return applied

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
