"""Tests for the evt handler payload shape, service identity and value keys.

Motion sensors read Unknown because the gateway's property-changed event never
reached the cached device tree. Three independent reasons, one test each:

1. Shape  -- the broker sends a FLAT payload ({device_id, service_name,
   property_name, property_value}); we required a `params` sub-dict and
   returned early on every real event.
2. Key    -- the app reads `property_value`; our value tuple did not.
3. Identity -- the tree names a service instance `pir0` while the event may
   name its TYPE `PirService`; exact string compare dropped it.

Plus the regressions this must not introduce: an ambiguous type must not patch
a sibling service, a valueless event must not blank a good value, and a
list-of-pairs props list must still be patched.

Run: python3 custom_components/oblo_living/tests/evt_payload_shape_test.py
"""
import asyncio
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401

from custom_components.oblo_living.coordinator import ObloDataCoordinator  # noqa: E402

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}"
          + (f"  <- {detail}" if detail and not cond else ""))


def make_coordinator(devices):
    hass = type("H", (), {"loop": asyncio.get_event_loop()})()
    coord = ObloDataCoordinator(hass, api=None, entry_id="test_entry")
    coord.devices = devices
    coord.data = devices
    coord.ohm = None
    coord.async_set_updated_data = lambda data: None  # test harness
    return coord


def pir_device(did="1029", props=None):
    """Motion sensor as the OHM tree names it: instance name pir0."""
    return {
        "id": did,
        "name": "Hallway Motion",
        "services": [{
            "service": "pir0",
            "service_type": "PirService",
            "props": props if props is not None else [["DebouncePeriod", "5"]],
        }],
    }


def props_of(dev, service="pir0"):
    for svc in dev["services"]:
        if svc["service"] == service:
            return svc["props"]
    return None


def get_prop(dev, key, service="pir0"):
    props = props_of(dev, service)
    if isinstance(props, dict):
        return props.get(key)
    for pair in props or []:
        if pair[0] == key:
            return pair[1]
    return None


def two_outlet_device():
    return {
        "id": "2000",
        "name": "Double Relay",
        "services": [
            {"service": "outlet0", "service_type": "OutletService",
             "props": {"State": "0"}},
            {"service": "outlet1", "service_type": "OutletService",
             "props": {"State": "0"}},
        ],
    }


def main():
    # 1) THE BUG: flat payload, no params envelope
    dev = pir_device()
    coord = make_coordinator([dev])
    coord.handle_event({
        "name": "device_1029_property_changed",
        "device_id": "1029",
        "service_name": "pir0",
        "property_name": "State",
        "property_value": "true",
    })
    check("flat payload is applied (the v0.2.32 symptom)",
          get_prop(dev, "State") == "true", repr(get_prop(dev, "State")))

    # 2) flat payload + property_value key only (the second bug)
    dev = pir_device()
    coord = make_coordinator([dev])
    coord.handle_event({
        "device_id": "1029", "service_name": "pir0",
        "property_name": "State", "property_value": "false",
    })
    check("property_value key is read",
          get_prop(dev, "State") == "false", repr(get_prop(dev, "State")))

    # 3) nested payload still works (covers the other broker shape)
    dev = pir_device()
    coord = make_coordinator([dev])
    coord.handle_event({
        "name": "device_1029_property_changed",
        "params": {"device_id": "1029", "service_name": "pir0",
                   "property_name": "State", "property_value": "true"},
    })
    check("nested params payload still applied",
          get_prop(dev, "State") == "true", repr(get_prop(dev, "State")))

    # 4) empty params {} + flat top-level fields
    dev = pir_device()
    coord = make_coordinator([dev])
    coord.handle_event({
        "name": "x", "params": {},
        "device_id": "1029", "service_name": "pir0",
        "property_name": "State", "property_value": "true",
    })
    check("empty params falls back to top level",
          get_prop(dev, "State") == "true", repr(get_prop(dev, "State")))

    # 5) service TYPE instead of instance name
    dev = pir_device()
    coord = make_coordinator([dev])
    coord.handle_event({
        "device_id": "1029", "service_name": "PirService",
        "property_name": "State", "property_value": "true",
    })
    check("service type matches the single instance",
          get_prop(dev, "State") == "true", repr(get_prop(dev, "State")))

    # 6) ambiguous type must not patch a sibling
    dev = two_outlet_device()
    coord = make_coordinator([dev])
    coord.handle_event({
        "device_id": "2000", "service_name": "OutletService",
        "property_name": "State", "property_value": "1",
    })
    check("ambiguous service type -> outlet0 untouched",
          get_prop(dev, "State", "outlet0") == "0", repr(get_prop(dev, "State", "outlet0")))
    check("ambiguous service type -> outlet1 untouched",
          get_prop(dev, "State", "outlet1") == "0", repr(get_prop(dev, "State", "outlet1")))

    # 7) exact instance name on a two-outlet device still resolves
    dev = two_outlet_device()
    coord = make_coordinator([dev])
    coord.handle_event({
        "device_id": "2000", "service_name": "outlet1",
        "property_name": "State", "property_value": "1",
    })
    check("exact instance name patches only that service",
          get_prop(dev, "State", "outlet1") == "1"
          and get_prop(dev, "State", "outlet0") == "0",
          f"o0={get_prop(dev, 'State', 'outlet0')} o1={get_prop(dev, 'State', 'outlet1')}")

    # 8) list-of-pairs props get patched (previously skipped entirely)
    dev = pir_device(props=[["DebouncePeriod", "5"]])
    coord = make_coordinator([dev])
    coord.handle_event({
        "device_id": "1029", "service_name": "pir0",
        "property_name": "State", "property_value": "true",
    })
    check("list-of-pairs props patched",
          get_prop(dev, "State") == "true", repr(props_of(dev)))
    check("existing list-of-pairs entry preserved",
          get_prop(dev, "DebouncePeriod") == "5", repr(props_of(dev)))

    # 9) valueless event must not blank a good value
    dev = pir_device(props={"State": "true"})
    coord = make_coordinator([dev])
    coord.handle_event({
        "device_id": "1029", "service_name": "pir0",
        "property_name": "State",
    })
    check("valueless event leaves the good value alone",
          get_prop(dev, "State") == "true", repr(get_prop(dev, "State")))

    # 10) non-property event is ignored, no crash
    dev = pir_device()
    coord = make_coordinator([dev])
    coord.handle_event({"name": "online_status", "device_id": "1029",
                        "status": "online"})
    check("non-property event ignored", get_prop(dev, "State") is None)

    # 11) unknown device is ignored
    dev = pir_device()
    coord = make_coordinator([dev])
    coord.handle_event({"device_id": "9999", "service_name": "pir0",
                        "property_name": "State", "property_value": "true"})
    check("unknown device ignored", get_prop(dev, "State") is None)

    # 12) motion State logs at INFO, not DEBUG
    dev = pir_device()
    coord = make_coordinator([dev])
    logger = logging.getLogger("custom_components.oblo_living.coordinator")
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Capture()
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        coord.handle_event({
            "device_id": "1029", "service_name": "pir0",
            "property_name": "State", "property_value": "true",
        })
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    motion_info = [r for r in records
                   if r.levelno == logging.INFO and "evt" in r.getMessage()]
    check("motion State logs at INFO (visible at WARNING default)",
          len(motion_info) == 1,
          [f"{r.levelname}:{r.getMessage()}" for r in records])

    # 13) a non-motion State still stays at DEBUG (no log spam)
    dev = {"id": "5", "services": [{"service": "outlet0",
                                    "service_type": "OutletService",
                                    "props": {"State": "0"}}]}
    coord = make_coordinator([dev])
    records.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        coord.handle_event({"device_id": "5", "service_name": "outlet0",
                            "property_name": "State", "property_value": "1"})
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    outlet_info = [r for r in records if r.levelno == logging.INFO]
    check("relay State stays at DEBUG", len(outlet_info) == 0,
          [f"{r.levelname}:{r.getMessage()}" for r in records])

    print(f"\n=== SUMMARY: {sum(c[1] for c in CHECKS)}/{len(CHECKS)} checks passed ===")
    for label, ok, detail in CHECKS:
        if not ok:
            print(f"  FAIL: {label}  <- {detail}")
    if not all(c[1] for c in CHECKS):
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()