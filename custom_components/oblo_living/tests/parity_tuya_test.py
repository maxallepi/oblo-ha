"""Tuya Local device page parity checks for v0.2.39.

Three concrete gaps vs Tuya Local:
1. entity_category = diagnostic on OperationalService / WiseDeviceService
2. has_entity_name = True + per-entity labels (no 3x "Alert" on the siren)
3. diagnostics.py that is allow-list, not redact-list

Run as: python3 custom_components/oblo_living/tests/parity_tuya_test.py
"""
import asyncio
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401

from custom_components.oblo_living.entity_map import (  # noqa: E402
    build_entities, entity_label_attr, entity_category_attr,
    DIAGNOSTIC_SERVICES, MULTI_ENTITY_LABELS,
)

CHECKS = []


def check(label, cond, detail=""):
    detail = "" if detail is None else str(detail)
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}"
          + (f"  <- {detail}" if detail and not cond else ""))


def _load_diagnostics():
    """Load diagnostics.py as a standalone module (no HA package context).

    Returns None when the file is absent, so the negative control ("delete
    diagnostics.py -> the Download diagnostics button disappears") reports a
    clean FAIL instead of an unhandled traceback.
    """
    path = os.path.join(COMPONENT, "diagnostics.py")
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location(
        "oblo_diagnostics_under_test", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# 1. Diagnostic entity category
# --------------------------------------------------------------------------

def test_entity_category_diagnostic():
    check("OperationalService is diagnostic", "OperationalService" in DIAGNOSTIC_SERVICES)
    check("WiseDeviceService is diagnostic", "WiseDeviceService" in DIAGNOSTIC_SERVICES)

    devs = [{
        "id": 100, "name": "Test Device", "class": "sensor",
        "services": [{"service": "OperationalService", "props": [("Online", "1")]}],
    }]
    ents = build_entities(devs)
    op = next(e for e in ents if e["service"] == "OperationalService")
    check("OperationalService entity carries entity_category",
          op.get("entity_category") == "diagnostic", op.get("entity_category"))
    check("entity_category_attr returns 'diagnostic'",
          entity_category_attr(op) == "diagnostic", entity_category_attr(op))

    # A normal service must NOT be diagnostic.
    devs2 = [{
        "id": 1052, "name": "Hallway Lights", "class": "ohm",
        "services": [{"service": "OutletService", "props": [("State", "true")]}],
    }]
    outlet = next(e for e in build_entities(devs2) if e["service"] == "OutletService")
    check("OutletService is not diagnostic",
          entity_category_attr(outlet) is None, entity_category_attr(outlet))


# --------------------------------------------------------------------------
# 2. has_entity_name + per-entity labels
# --------------------------------------------------------------------------

def test_has_entity_name_labels():
    # Siren: SimpleAlertService emits three entities (Type, Sound, Silence).
    devs = [{
        "id": 15, "name": "Gateway Simple Alert 15", "class": "SimpleAlert",
        "services": [{"service": "SimpleAlertService", "props": []}],
    }]
    ents = build_entities(devs)
    labels = [e.get("label") for e in ents]
    check("siren emits 3 entities", len(ents) == 3, f"got {len(ents)}")
    check("siren labels are distinct", len(set(labels)) == 3, f"labels {labels}")
    check("labels match the per-entity table",
          set(labels) == {"Alert Type", "Sound", "Silence"}, f"labels {labels}")

    # Weather: two services must not collide on the same label.
    devs2 = [{
        "id": 1010, "name": "Weather", "class": "Weather",
        "services": [
            {"service": "WeatherService", "props": [("Temperature", "20")]},
            {"service": "WindService", "props": [("Speed", "5")]},
        ],
    }]
    labels2 = [e.get("label") for e in build_entities(devs2)]
    check("WeatherService label is 'Weather'", "Weather" in labels2, f"labels {labels2}")
    check("WindService label is 'Wind'", "Wind" in labels2, f"labels {labels2}")
    check("no label collision on Weather",
          len(set(labels2)) == len(labels2), f"labels {labels2}")

    # Every label must be human text, never a raw Python identifier.
    for label in labels:
        check(f"label '{label}' is not a raw identifier",
              label != "SimpleAlertService" and "Service" not in label, label)


def test_entity_classes_set_has_entity_name():
    """All six live entity classes must opt into has_entity_name."""
    for mod_name in ("binary_sensor", "sensor", "switch", "select", "button"):
        path = os.path.join(COMPONENT, "entities", f"{mod_name}.py")
        check(f"entities/{mod_name}.py exists", os.path.exists(path))
        if not os.path.exists(path):
            continue
        src = open(path, encoding="utf-8").read()
        check(f"{mod_name}: sets _attr_has_entity_name = True",
              "_attr_has_entity_name = True" in src)
        check(f"{mod_name}: uses entity_label_attr",
              "entity_label_attr" in src)
        check(f"{mod_name}: never uses the old full-name helper",
              "entity_name(" not in src and "entity_name_multi(" not in src)


def test_entity_label_attr():
    """entity_label_attr: None when the device name already contains the label."""
    e1 = {"name": "Hall Motion", "service": "pir0", "label": "Motion"}
    check("label contained in device name -> None", entity_label_attr(e1) is None)

    e2 = {"name": "Hallway Lights", "service": "OutletService", "label": "Outlet"}
    check("label not contained -> label", entity_label_attr(e2) == "Outlet")

    e3 = {"name": "Gateway Simple Alert 15", "service": "SimpleAlertService",
          "label": "Alert Type"}
    check("partial containment -> label", entity_label_attr(e3) == "Alert Type")


# --------------------------------------------------------------------------
# 3. Diagnostics
# --------------------------------------------------------------------------

def test_diagnostics_module_exists():
    mod = _load_diagnostics()
    check("diagnostics.py exists", mod is not None)
    if mod is None:
        return
    check("async_get_device_diagnostics exported",
          hasattr(mod, "async_get_device_diagnostics"))
    check("async_get_config_entry_diagnostics exported",
          hasattr(mod, "async_get_config_entry_diagnostics"))


def test_diagnostics_redaction_behaviour():
    """Real sentinel values must never reach the output dict."""
    mod = _load_diagnostics()
    if mod is None:
        check("diagnostics.py loadable", False)
        return

    class FakeEntry:
        entry_id = "test_entry"
        data = {
            "host": "192.168.1.16",
            "port": 8883,
            "username": "user",
            "certificate": False,
            "secureId": "SENTINEL_SECURE_ID",
            "hash": "SENTINEL_HASH",
            "secret": "SENTINEL_TOKEN",
            "session_id": "SENTINEL_SESSION",
            "password": "SENTINEL_PASSWORD",
        }
        options = {}
        unique_id = "test_entry"
        version = 1
        domain = "oblo_living"

    out = mod._entry_summary(FakeEntry())
    dumped = json.dumps(out)

    for key, sentinel in (
        ("secureId", "SENTINEL_SECURE_ID"),
        ("hash", "SENTINEL_HASH"),
        ("secret", "SENTINEL_TOKEN"),
        ("session_id", "SENTINEL_SESSION"),
        ("password", "SENTINEL_PASSWORD"),
    ):
        check(f"{key} value absent from output", sentinel not in dumped, dumped)
        check(f"{key}_present marker emitted", out.get(f"{key}_present") is True,
              out.get(f"{key}_present"))

    check("non-secret host preserved", out.get("host") == "192.168.1.16", out.get("host"))
    check("non-secret port preserved", out.get("port") == 8883, out.get("port"))
    check("no raw secret key names in output",
          not ({"secureId", "hash", "secret"} & set(out)), str(sorted(out)))

    class FakeAPI:
        session_id = "SENTINEL_API_SESSION"
        secure_id = "SENTINEL_API_SECURE"
        user_id = 12345

    api_out = mod._api_summary(FakeAPI())
    api_dumped = json.dumps(api_out)
    check("api session_id value absent", "SENTINEL_API_SESSION" not in api_dumped, api_dumped)
    check("api secure_id value absent", "SENTINEL_API_SECURE" not in api_dumped, api_dumped)
    check("api authenticated flag present", api_out.get("authenticated") is True)
    check("api session_id_present marker", api_out.get("session_id_present") is True)
    check("api secure_id_present marker", api_out.get("secure_id_present") is True)
    check("api user_id_present marker", api_out.get("user_id_present") is True)

    # The REDACTED sentinel must be imported from HA, not reinvented.
    src = open(os.path.join(COMPONENT, "diagnostics.py"), encoding="utf-8").read()
    check("imports REDACTED from homeassistant.components.diagnostics",
          "from homeassistant.components.diagnostics import REDACTED" in src)


def test_device_diagnostics_shape():
    """Device-level diagnostics must resolve the live device and keep secrets out."""
    mod = _load_diagnostics()
    if mod is None:
        check("diagnostics.py loadable", False)
        return

    # Shape taken from Andrew's real ohm.db row for device 1052.
    devices = [{
        "id": 1052, "name": "Hallway Lights", "class": "ohm",
        "services": [{"service": "OutletService",
                      "props": [("State", "true"), ("MeterReport_W", "42.5")]}],
    }]

    class FakeEntry:
        entry_id = "test_entry"
        data = {"host": "192.168.1.16", "port": 8883, "secret": "SENTINEL_TOKEN"}
        options = {}
        unique_id = "test_entry"
        version = 1
        domain = "oblo_living"

    class FakeDevice:
        identifiers = {"test_entry_1052"}
        name = "Hallway Lights"
        manufacturer = "Qubino"
        model = "Smart Relay"
        sw_version = "6.10"
        hw_version = None
        via_device_id = "test_entry_gateway"

    class FakeCoordinator:
        data = devices
        gateway_serial = "C83DD4300D5B"
        gateway_name = "Living Room"
        gateway_model = "doc300"
        gateway_sw_version = "5.4.0"
        gateway_hw_version = "5.4.0"
        update_interval_s = 30
        last_update_success = True
        source = "mqtt"

    class FakeHass:
        data = {"oblo_living": {"test_entry": {"coordinator": FakeCoordinator()}}}

    result = asyncio.run(
        mod.async_get_device_diagnostics(FakeHass(), FakeEntry(), FakeDevice()))
    dumped = json.dumps(result)

    check("device diagnostics returns dict", isinstance(result, dict), type(result).__name__)
    for key in ("entry", "device", "live"):
        check(f"device diagnostics has '{key}' key", key in result, str(sorted(result)))
    check("resolved the live device by id",
          bool(result.get("live")) and str(result["live"][0]["id"]) == "1052",
          str(result.get("live")))
    check("device metadata carried through",
          result["device"].get("manufacturer") == "Qubino", result["device"])
    check("property NAMES emitted",
          result["live"][0]["services"][0]["properties"] == ["State", "MeterReport_W"],
          result["live"][0]["services"][0]["properties"])
    check("property VALUES not emitted", "42.5" not in dumped, dumped)
    check("no secret sentinel leaked in device diag", "SENTINEL" not in dumped)

    entry_out = asyncio.run(
        mod.async_get_config_entry_diagnostics(FakeHass(), FakeEntry()))
    entry_dumped = json.dumps(entry_out)
    check("entry-level diagnostics has devices list",
          isinstance(entry_out.get("devices"), list) and len(entry_out["devices"]) == 1,
          str(entry_out.get("devices")))
    check("entry-level diagnostics gateway metadata",
          entry_out["coordinator"].get("gateway_serial") == "C83DD4300D5B")
    check("entry-level diagnostics keeps secrets out", "SENTINEL" not in entry_dumped)


if __name__ == "__main__":
    test_entity_category_diagnostic()
    test_has_entity_name_labels()
    test_entity_classes_set_has_entity_name()
    test_entity_label_attr()
    test_diagnostics_module_exists()
    test_diagnostics_redaction_behaviour()
    test_device_diagnostics_shape()

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks pass")
    sys.exit(1 if failed else 0)
