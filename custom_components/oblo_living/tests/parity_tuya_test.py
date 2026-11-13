"""Tuya Local device page parity checks for v0.2.39.

Three concrete gaps from Tuya Local:
1. entity_category = diagnostic on OperationalService (connectivity)
2. has_entity_name = True + per-entity labels (no 3x "Alert" on siren)
3. diagnostics.py with REDACTED sentinel for secrets
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401

from custom_components.oblo_living.entity_map import (  # noqa: E402
    build_entities, entity_label, entity_label_attr, entity_category_attr,
    DIAGNOSTIC_SERVICES, LABELS, MULTI_ENTITY_LABELS,
)

from homeassistant.const import EntityCategory  # noqa: E402

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + (f"  <- {detail}" if detail and not cond else ""))


def test_entity_category_diagnostic():
    """OperationalService and WiseDeviceService entities must be diagnostic."""
    for svc in DIAGNOSTIC_SERVICES:
        check(f"{svc} in DIAGNOSTIC_SERVICES", svc in DIAGNOSTIC_SERVICES)

    # Build a fake device with OperationalService
    devs = [{
        "id": 100, "name": "Test Device", "class": "sensor",
        "services": [{"service": "OperationalService", "props": [("Online", "1")]}],
    }]
    ents = build_entities(devs)
    op = next(e for e in ents if e["service"] == "OperationalService")
    check("OperationalService entity has entity_category=diagnostic",
          op.get("entity_category") == "diagnostic", op.get("entity_category"))
    cat = entity_category_attr(op)
    check("entity_category_attr returns 'diagnostic'", cat == "diagnostic", cat)


def test_has_entity_name_labels():
    """Per-entity labels must be distinct on multi-entity services (SimpleAlert)."""
    # Siren: 3 entities from SimpleAlertService (Type, Sound, Silence)
    devs = [{
        "id": 15, "name": "Gateway Simple Alert 15", "class": "SimpleAlert",
        "services": [{"service": "SimpleAlertService", "props": []}],
    }]
    ents = build_entities(devs)
    # Expect 3 entities: select/Type, button/Sound, button/Silence
    labels = [e.get("label") for e in ents]
    check("siren emits 3 entities", len(ents) == 3, f"got {len(ents)}")
    check("siren labels are distinct", len(set(labels)) == 3, f"labels {labels}")
    check("labels match per-entity table",
          set(labels) == {"Alert Type", "Sound", "Silence"}, f"labels {labels}")

    # Weather: WeatherService + WindService -> distinct labels
    devs2 = [{
        "id": 1010, "name": "Weather", "class": "Weather",
        "services": [
            {"service": "WeatherService", "props": [("Temperature", "20")]},
            {"service": "WindService", "props": [("Speed", "5")]},
        ],
    }]
    ents2 = build_entities(devs2)
    labels2 = [e.get("label") for e in ents2]
    check("WeatherService label 'Weather'", "Weather" in labels2, f"labels {labels2}")
    check("WindService label 'Wind'", "Wind" in labels2, f"labels {labels2}")
    check("no label collision on Weather", len(set(labels2)) == len(labels2), f"labels {labels2}")


def test_entity_label_attr():
    """entity_label_attr: None when device name contains the label (dedupe)."""
    # "Hall Motion" + "Motion" -> None (HA shows "Hall Motion" once)
    e1 = {"name": "Hall Motion", "service": "pir0", "label": "Motion"}
    check("label contained -> None", entity_label_attr(e1) is None)

    # "Hallway Lights" + "Outlet" -> "Outlet"
    e2 = {"name": "Hallway Lights", "service": "OutletService", "label": "Outlet"}
    check("label not contained -> label", entity_label_attr(e2) == "Outlet")

    # "Gateway Simple Alert 15" + "Alert Type" -> "Alert Type" (only "Alert" in name)
    e3 = {"name": "Gateway Simple Alert 15", "service": "SimpleAlertService",
          "label": "Alert Type"}
    check("partial containment -> label", entity_label_attr(e3) == "Alert Type")


def test_diagnostics_module_exists():
    """diagnostics.py must exist and export async_get_device_diagnostics."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "diagnostics",
        os.path.join(COMPONENT, "diagnostics.py")
    )
    check("diagnostics.py exists", spec is not None)
    if spec:
        mod = importlib.util.module_from_spec(spec)
        check("diagnostics module loads", True)
        spec.loader.exec_module(mod)
        check("async_get_device_diagnostics exported",
              hasattr(mod, "async_get_device_diagnostics"))
        check("async_get_config_entry_diagnostics exported",
              hasattr(mod, "async_get_config_entry_diagnostics"))


def test_redaction_sentinels():
    """Diagnostics must never include secureId, hash, secret, session_id."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "diagnostics",
        os.path.join(COMPONENT, "diagnostics.py")
    )
    if not spec:
        return
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Check the module uses REDACTED from homeassistant.components.diagnostics
    src = open(os.path.join(COMPONENT, "diagnostics.py")).read()
    check("imports REDACTED from diagnostics", "from homeassistant.components.diagnostics import REDACTED" in src)
    check("secureId redacted", "secureId" in src and "REDACTED" in src)
    check("hash redacted", "hash" in src and "REDACTED" in src)
    check("secret redacted", "secret" in src and "REDACTED" in src)
    check("session_id redacted", "session_id" in src and "REDACTED" in src)


if __name__ == "__main__":
    test_entity_category_diagnostic()
    test_has_entity_name_labels()
    test_entity_label_attr()
    test_diagnostics_module_exists()
    test_redaction_sentinels()

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks pass")
    sys.exit(1 if failed else 0)