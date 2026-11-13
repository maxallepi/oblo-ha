"""Diagnostics support for the OBLO Living integration.

HA shows a "Download diagnostics" button on the integration page when this
module exists. Andrew currently has to paste raw logs by hand; this gives him a
single file to attach.

REDACTION POLICY -- allow-list, not redact-list.

The config entry holds a live auth token (`secret`) and `ObloCloudApi` holds
`session_id`, which is sent as the `sessionId` header on every authenticated
call. A diagnostics dump built as "everything we have, minus a redaction list"
leaks that token the first time someone adds a field nobody remembered to
redact. That is exactly the shape of the v0.2.13 leak.

So: every key below is written out explicitly. Nothing is copied wholesale from
the config entry or from the API object. `secureId`, `hash`, `secret` and
`session_id` never enter the dict at all; the REDACTED sentinel is used only
for values that are individually known-sensitive but whose *presence* is
diagnostically useful (i.e. "we have a secret" vs "we don't").
"""
from __future__ import annotations

from homeassistant.components.diagnostics import REDACTED

from .entity_map import ohm_id_from_identifier

# Config entry keys whose existence we report but whose value we never emit.
# `identity` is the owner's account email and `username` is
# "cli/<accountId>-<64-hex session suffix>" -- half a credential. Diagnostics
# files get attached to public GitHub issues, so both are presence-only: the
# v0.2.13 leak was a raw dump, and an email in a public issue is the same
# mistake with a friendlier face.
SENSITIVE_ENTRY_KEYS = ("secureId", "hash", "secret", "session_id", "password",
                        "identity", "username")


def _entry_summary(entry):
    """Config entry fields worth seeing in a bug report, values filtered.

    Built key-by-key on purpose: adding a key to the config entry must NOT
    silently add it to diagnostics.
    """
    data = entry.data or {}
    out = {}
    # Non-secret connection details. Everything here is safe to publish.
    for key in ("host", "port", "certificate", "scan_interval", "poll_interval"):
        if key in data:
            out[key] = data[key]
    # Secret-bearing keys: presence only.
    for key in SENSITIVE_ENTRY_KEYS:
        if key in data or key in (entry.options or {}):
            out[f"{key}_present"] = True
    out["unique_id"] = entry.unique_id
    out["version"] = getattr(entry, "version", None)
    out["domain"] = getattr(entry, "domain", None)
    return out


def _api_summary(api):
    """Shape of the API object: which auth fields are populated, not their values."""
    if api is None:
        return {}
    out = {"authenticated": bool(getattr(api, "session_id", None))}
    for attr in ("session_id", "secure_id", "user_id"):
        if hasattr(api, attr):
            out[f"{attr}_present"] = bool(getattr(api, attr, None))
    if hasattr(api, "session_id"):
        out["session_id"] = REDACTED
    return out


def _device_summary(coordinator_data):
    """Per-device overview: id, class, name, model, and service names only.

    Property VALUES are omitted -- they are noise in a bug report and some of
    them (serials) are identifiers we do not need to leak. Property NAMES tell
    us what the gateway is reporting, which is the thing that actually breaks.
    """
    devices = []
    for dev in (coordinator_data or []):
        if not isinstance(dev, dict):
            continue
        devices.append({
            "id": dev.get("id"),
            "name": dev.get("name"),
            "class": dev.get("class"),
            "services": [
                {
                    "service": svc.get("service"),
                    "properties": [p[0] for p in (svc.get("props") or [])
                                   if isinstance(p, (list, tuple)) and p],
                }
                for svc in (dev.get("services") or [])
                if isinstance(svc, dict)
            ],
        })
    return devices


def _coordinator_summary(coordinator):
    if coordinator is None:
        return {}
    return {
        "gateway_serial": getattr(coordinator, "gateway_serial", None),
        "gateway_name": getattr(coordinator, "gateway_name", None),
        "gateway_model": getattr(coordinator, "gateway_model", None),
        "gateway_sw_version": getattr(coordinator, "gateway_sw_version", None),
        "gateway_hw_version": getattr(coordinator, "gateway_hw_version", None),
        "scan_interval_s": getattr(coordinator, "update_interval_s", None),
        "device_count": len(coordinator.data or []) if hasattr(coordinator, "data") else None,
        "last_update_success": getattr(coordinator, "last_update_success", None),
        "source": getattr(coordinator, "source", None),
    }


async def async_get_config_entry_diagnostics(hass, entry):
    """Entry-level diagnostics: connection shape + device inventory."""
    domain_data = (hass.data.get("oblo_living") or {}).get(entry.entry_id) or {}
    coordinator = domain_data.get("coordinator")
    api = domain_data.get("api")
    return {
        "entry": _entry_summary(entry),
        "api": _api_summary(api),
        "coordinator": _coordinator_summary(coordinator),
        "devices": _device_summary(getattr(coordinator, "data", None)),
    }


async def async_get_device_diagnostics(hass, entry, device):
    """Device-level diagnostics: one device's services and property names."""
    domain_data = (hass.data.get("oblo_living") or {}).get(entry.entry_id) or {}
    coordinator = domain_data.get("coordinator")
    # The registry stores the entry-scoped identifier ("<entry_id>_1052");
    # the coordinator's device dicts key on the bare OHM id (1052).
    raw_id = None
    for ident in (device.identifiers or set()):
        if isinstance(ident, (tuple, list)) and len(ident) == 2 and ident[0] == "oblo_living":
            raw_id = ohm_id_from_identifier(entry.entry_id, ident[1])
            break
    target = None
    for dev in (getattr(coordinator, "data", None) or []):
        if not isinstance(dev, dict):
            continue
        if str(dev.get("id")) == str(raw_id):
            target = dev
            break
    return {
        "entry": _entry_summary(entry),
        "device": {
            "id": raw_id,
            "name": getattr(device, "name", None),
            "manufacturer": getattr(device, "manufacturer", None),
            "model": getattr(device, "model", None),
            "sw_version": getattr(device, "sw_version", None),
            "hw_version": getattr(device, "hw_version", None),
            "via_device_id": getattr(device, "via_device_id", None),
        },
        "live": _device_summary([target] if target else []),
    }