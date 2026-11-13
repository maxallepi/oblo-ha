"""OBLO Living integration setup."""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (DOMAIN, CONF_IDENTITY, CONF_SECRET, CONF_SESSION_ID,
                    CONF_USER_ID, CONF_USERNAME, CONF_GROUPS,
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_S,
                    SCAN_INTERVAL_S)
from .cloud_api import ObloCloudApi, ObloCloudError
from .cloud_mqtt import ObloCloudMqtt
from .coordinator import ObloDataCoordinator
from .hub import ObloHub
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["binary_sensor", "sensor", "switch", "select", "button"]


def _pick_gateway_identity(raw_gateways, api):
    """Resolve (gateway_owner_user_id, serial) from the raw /udm/device/list payload.

    The published topic uses the GATEWAY OWNER's userId, not the logged-in
    user's, so a wrong/absent value silently publishes to `None` and the broker
    drops it (QoS 0 -> bare 15 s timeout, no error). Prefer the object that
    actually carries a serial; fall back to the login user id only for userId.
    Never invent a serial.
    """
    candidates = [g for g in (raw_gateways or []) if isinstance(g, dict)]
    # prefer containers that look like gateways (carry a serial)
    with_serial = [g for g in candidates
                   if g.get("serialNumber") or g.get("serial") or g.get("sn")]
    gw = (with_serial or candidates or [{}])[0]

    user_id = str(gw.get("userId") or gw.get("userID") or api.user_id or "")
    serial = str(gw.get("serialNumber") or gw.get("serial")
                 or gw.get("sn") or gw.get("deviceSN") or "")
    return user_id, serial, sorted(gw.keys())


async def _async_start_ohm(hass, api, coordinator):
    """Connect the cloud MQTT/OHM client and attach it to the coordinator.

    Returns the client, or None when OHM is unavailable. Never raises: a broken
    MQTT path degrades the entry to REST-only instead of failing setup.
    """
    # Cached session restore sets session_id but not username/groups.
    # Force a login if we don't have what OHM needs.
    if not api.username or not api.groups:
        try:
            await api.async_login()
        except ObloCloudError as err:
            _LOGGER.warning("OHM skipped: login failed: %s", err)
            return None
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("OHM skipped: login error: %s", err)
            return None

    if not api.session_id or not api.username:
        _LOGGER.warning("OHM skipped: no cloud session (session_id=%r username=%r)",
                        api.session_id, api.username)
        return None

    try:
        raw = await api.async_list_gateways()
    except ObloCloudError as err:
        _LOGGER.warning("OHM skipped: gateway list failed: %s", err)
        return None

    user_id, serial, keys = _pick_gateway_identity(raw, api)
    # Guard the silent-None-publish trap: both values are mandatory for the topic.
    if not user_id or not serial:
        _LOGGER.warning(
            "OHM skipped: gateway payload lacks userId/serial "
            "(user_id=%r serial=%r keys=%s)",
            user_id, serial, keys)
        return None

    client = ObloCloudMqtt(
        session_id=api.session_id,
        user_id=user_id,
        username=api.username,
        gateway_serial=serial,
        hass_loop=hass.loop,
        groups=api.groups or [],
    )
    # One INFO line so a wrong topic is visible in the log, not a bare timeout.
    # The MQTT username is a credential under the shared literal password, so it
    # is masked here -- only its shape and source are printed.
    _uname = api.username or ""
    _masked = (f"{_uname[:4]}...{_uname[-4:]} (len={len(_uname)})"
               if len(_uname) > 8 else f"{_uname!r} (len={len(_uname)})")
    _is_admin = "admin" in (api.groups or [])
    _LOGGER.info(
        "OHM topic oblo/%s/gtw/%s/ohm/req (admin=%s, sender=cli/%s, mqtt_user cli/%s)",
        user_id, serial, _is_admin,
        ("<sessionId>" if _is_admin else user_id), _masked)

    try:
        await client.async_connect()
        coordinator.set_ohm_client(client)
        # Subscribe BEFORE the first object_list so a property change that
        # lands during the initial poll is not lost. Until v0.2.32 this was a
        # no-op lambda: every event arrived and was thrown away, which is why
        # nothing in the log ever mentioned a device changing.
        if await coordinator.async_attach_events():
            _LOGGER.info("OHM evt subscribed (live property changes will log)")
        else:
            _LOGGER.warning("OHM evt not subscribed -- state updates will "
                            "only arrive on the %ss poll",
                            int(coordinator.update_interval.total_seconds())
                            if coordinator.update_interval else SCAN_INTERVAL_S)
        await coordinator.async_request_refresh()
        _LOGGER.info("OHM connected: devices=%d", len(coordinator.devices))
        return client
    except ObloCloudError as err:
        _LOGGER.warning("OHM unavailable, REST-only mode: %s", err)
    except Exception as err:  # noqa: BLE001 - never fail setup on MQTT problems
        _LOGGER.warning("OHM setup failed, REST-only mode: %s", err)
    try:
        await client.async_close()
    except Exception:  # noqa: BLE001
        pass
    coordinator.set_ohm_client(None)
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up OBLO Living from a config entry (cloud mode)."""
    # build cloud API from stored credentials. CONF_SECRET holds the PLAINTEXT
    # password (v2 entries). v1 entries (stored md5) are migrated via
    # async_migrate_entry and forced to reauth, so only plaintext reaches here.
    session = async_get_clientsession(hass)
    secret = entry.data[CONF_SECRET]
    api = ObloCloudApi(session, entry.data[CONF_IDENTITY], secret)

    # restore cached session if present (avoids one login round-trip);
    # the coordinator re-authenticates itself on the first auth failure
    if entry.data.get(CONF_SESSION_ID):
        api.session_id = entry.data[CONF_SESSION_ID]
        api.user_id = entry.data.get(CONF_USER_ID)
        api.username = entry.data.get(CONF_USERNAME)
        api.groups = entry.data.get(CONF_GROUPS) or []

    # coordinator for periodic polling (re-login + fetch happens inside).
    # Interval comes from the options flow (default 60 s, floor 15 s): the poll
    # is a safety net for missed gateway events, but each cycle is three cloud
    # round trips and this API has already rate-limited the account once.
    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_S)
    coordinator = ObloDataCoordinator(hass, api, scan_interval=scan_interval)
    await coordinator.async_config_entry_first_refresh()
    _LOGGER.info("cloud session=%s devices=%d", api.session_id, len(coordinator.devices))

    # persist what we learned so a restart does not need a fresh login
    # (OHM needs username+groups; a cached session alone does not carry them)
    saved = {}
    if api.session_id and entry.data.get(CONF_SESSION_ID) != api.session_id:
        saved[CONF_SESSION_ID] = api.session_id
    if api.user_id and entry.data.get(CONF_USER_ID) != api.user_id:
        saved[CONF_USER_ID] = api.user_id
    if api.username and entry.data.get(CONF_USERNAME) != api.username:
        saved[CONF_USERNAME] = api.username
    if api.groups and entry.data.get(CONF_GROUPS) != api.groups:
        saved[CONF_GROUPS] = list(api.groups)
    if saved:
        hass.config_entries.async_update_entry(entry, data={**entry.data, **saved})
        _LOGGER.debug("cached cloud session fields: %s", sorted(saved))

    # ── Cloud MQTT / OHM ────────────────────────────────────────────────
    # REST only returns the gateway container; the device inventory with
    # service_list lives behind OHM commands over MQTT. Attaching the client
    # makes the coordinator merge real devices in. A failure here must NOT
    # fail the config entry -- we degrade to REST-only and retry next restart.
    ohm = await _async_start_ohm(hass, api, coordinator)

    # build hub from normalised devices
    hub = ObloHub()
    hub.load_from_normalised(coordinator.devices)

    # stash for platforms
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "hub": hub,
        "ohm": ohm,
    }

    # Options changed (poll interval) -> apply in place, no reload.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_register_services(hass)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry):
    """Apply option changes in place (registered as an entry update listener).

    Kept at module level, not nested, so it can be driven by a test without a
    full HA runtime. HA calls this after the options flow writes the entry, so
    a new poll interval takes effect without a reload: assigning
    coordinator.update_interval reschedules the next refresh, and no entity is
    rebuilt -- an in-flight write or a sounding siren is not disturbed.
    """
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id) or {}
    coord = data.get("coordinator")
    if coord is None:
        return
    coord.set_scan_interval(
        entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_S))


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id) or {}
    ohm = data.get("ohm")
    coord = data.get("coordinator")
    if coord is not None:
        # Drain any in-flight actuations (e.g. a sounding siren) while the
        # OHM client is still attached. A cancelled press coroutine may have
        # left the "stop" write pending; this retries it.
        await coord.async_stop_active_alerts()
        coord.set_ohm_client(None)
    if ohm is not None:
        try:
            await ohm.async_close()
        except Exception:  # noqa: BLE001 - unload must never raise on MQTT
            pass
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate a v1 entry (stored md5(password)) to v2 (plaintext password).

    The md5 hash cannot be turned back into the password, and the cloud would
    hash it a second time, so the only honest migration is to mark the entry
    v2 and send the user through reauth once to re-enter the password.

    NOTE: HA looks for async_migrate_entry on the integration __init__ module
    (config_entries.py: `hasattr(component, "async_migrate_entry")`), NOT on
    config_flow. This must live here or version migration silently fails.
    """
    if config_entry.version > 2:
        return False           # written by a newer version -- leave it alone
    if config_entry.version == 1:
        _LOGGER.info("migrating OBLO entry %s from v1 to v2 (reauth required)",
                     config_entry.entry_id)
        hass.config_entries.async_update_entry(config_entry, version=2,
                                               minor_version=1)
        entry = hass.config_entries.async_get_entry(config_entry.entry_id)
        if entry is not None and hasattr(entry, "async_start_reauth"):
            entry.async_start_reauth(hass)
        else:
            _LOGGER.warning(
                "could not start reauth for %s; remove and re-add the "
                "integration", config_entry.entry_id)
    return True
