"""Tests for overlay canonicalisation and empty-props restore.

1. Canonical key fix: an evt that names the TYPE (PirService) must file its
   overlay under the INSTANCE (pir0) so _reapply_overlay finds it and
   the value survives the next poll.

2. Empty-props guard: a service whose poll returns {} props must still
   restore its overlay (the guard was skipping it).

3. _apply_property_change returns the canonical service name on change,
   False when unchanged.

4. Ambiguous type (two services sharing a type) drops the evt.

5. Valueless event (via handle_event) does NOT blank a good value.

Run: python3 custom_components/oblo_living/tests/overlay_canonical_test.py
"""
import asyncio
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


def outlet_device(did="1052", props=None):
    """Relay outlet as the OHM tree names it: instance name outlet0."""
    return {
        "id": did,
        "name": "Hallway Lights",
        "services": [{
            "service": "outlet0",
            "service_type": "OutletService",
            "props": props if props is not None else [["State", "0"]],
        }],
    }


def get_prop(dev, key, service="pir0"):
    for svc in dev["services"]:
        if svc["service"] == service:
            props = svc.get("props")
            if isinstance(props, dict):
                return props.get(key)
            for pair in props or []:
                if pair[0] == key:
                    return pair[1]
    return None


# ===================================================================
# 1. Canonical key: evt names TYPE → overlay keys by INSTANCE
# ===================================================================

print("\n=== 1. Canonical key: evt names TYPE → overlay keys by INSTANCE ===")

# 1a: evt names instance (pir0) → works
devices = [pir_device()]
coord = make_coordinator(devices)
coord.handle_event({
    "name": "property_changed",
    "device_id": "1029",
    "service_name": "pir0",
    "property_name": "State",
    "property_value": "1",
})
# Overlay should be keyed by canonical 'pir0'
check("overlay key is ('1029','pir0')", ("1029", "pir0") in coord._overlay())
check("overlay key NOT ('1029','PirService')", ("1029", "PirService") not in coord._overlay())

# Now swap to a fresh poll payload that OMITS State
fresh_poll = [pir_device(props=[["DebouncePeriod", "5"]])]
coord.devices = fresh_poll
coord.data = fresh_poll
applied = coord._reapply_overlay()
check("reapply applies 1 when poll omits State", applied == 1)
check("State restored after poll", get_prop(fresh_poll[0], "State") == "1")

# 1b: evt names type (PirService) → overlay keyed by canonical 'pir0'
devices = [pir_device()]
coord = make_coordinator(devices)
coord.handle_event({
    "name": "property_changed",
    "device_id": "1029",
    "service_name": "PirService",  # TYPE, not instance
    "property_name": "State",
    "property_value": "1",
})
check("overlay key is ('1029','pir0')", ("1029", "pir0") in coord._overlay())
check("overlay key NOT ('1029','PirService')", ("1029", "PirService") not in coord._overlay())

# Swap to fresh poll without State
fresh_poll = [pir_device(props=[["DebouncePeriod", "5"]])]
coord.devices = fresh_poll
coord.data = fresh_poll
applied = coord._reapply_overlay()
check("reapply applies 1 for type-named evt", applied == 1)
check("State restored for type-named evt", get_prop(fresh_poll[0], "State") == "1")


# ===================================================================
# 2. Empty-props guard removed: {} props still restores overlay
# ===================================================================

print("\n=== 2. Empty-props guard removed ===")

devices = [outlet_device(props={})]  # poll returns empty dict
coord = make_coordinator(devices)
# Pre-seed overlay
coord._overlay()[("1052", "outlet0")] = {"State": "1"}
applied = coord._reapply_overlay()
check("reapply applies when props is {} and overlay exists", applied == 1)
check("State restored into {} props", get_prop(devices[0], "State", "outlet0") == "1")


# ===================================================================
# 3. _apply_property_change return values (unit-level)
# ===================================================================

print("\n=== 3. _apply_property_change return values ===")

devices = [pir_device(props=[["State", "0"]])]
coord = make_coordinator(devices)

# Change: returns canonical
canon = coord._apply_property_change("1029", "pir0", "State", "1")
check("returns 'pir0' when value changes", canon == "pir0")

# Unchanged: returns False (no re-push needed)
canon = coord._apply_property_change("1029", "pir0", "State", "1")
check("returns False when value unchanged", canon is False)

# No matching service: returns False
canon = coord._apply_property_change("1029", "UnknownService", "State", "1")
check("returns False when no match", canon is False)


# ===================================================================
# 4. Ambiguous type: two outlets with same type → evt ignored
# ===================================================================

print("\n=== 4. Ambiguous type drops evt ===")

devices = [{
    "id": "1052",
    "name": "Dual Relay",
    "services": [
        {"service": "outlet0", "service_type": "OutletService", "props": [["State", "0"]]},
        {"service": "outlet1", "service_type": "OutletService", "props": [["State", "0"]]},
    ],
}]
coord = make_coordinator(devices)
coord.handle_event({
    "name": "property_changed",
    "device_id": "1052",
    "service_name": "OutletService",
    "property_name": "State",
    "property_value": "1",
})
check("neither outlet patched",
      get_prop(devices[0], "State", "outlet0") == "0" and
      get_prop(devices[0], "State", "outlet1") == "0")
check("no overlay created for ambiguous type",
      ("1052", "outlet0") not in coord._overlay() and
      ("1052", "outlet1") not in coord._overlay() and
      ("1052", "OutletService") not in coord._overlay())


# ===================================================================
# 5. List-of-pairs props still patched (regression guard)
# ===================================================================

print("\n=== 5. List-of-pairs props still patched ===")

devices = [pir_device(props=[["State", "0"], ["DebouncePeriod", "5"]])]
coord = make_coordinator(devices)
coord.handle_event({
    "name": "property_changed",
    "device_id": "1029",
    "service_name": "PirService",
    "property_name": "State",
    "property_value": "1",
})
check("list-of-pairs patched", get_prop(devices[0], "State") == "1")


# ===================================================================
# 6. Valueless event (via handle_event) does NOT blank a good value
# ===================================================================

print("\n=== 6. Valueless event does not blank good value ===")

devices = [pir_device(props=[["State", "1"]])]
coord = make_coordinator(devices)
coord.handle_event({
    "name": "property_changed",
    "device_id": "1029",
    "service_name": "pir0",
    "property_name": "State",
    # property_value omitted entirely
})
check("State stays 1 (valueless ignored)", get_prop(devices[0], "State") == "1")


# ===================================================================
# 7. Flat payload (no params envelope) works end-to-end
# ===================================================================

print("\n=== 7. Flat payload (no params envelope) end-to-end ===")

devices = [pir_device(props=[["DebouncePeriod", "5"]])]
coord = make_coordinator(devices)
# Flat payload — no "params" key
coord.handle_event({
    "name": "property_changed",
    "device_id": "1029",
    "service_name": "PirService",
    "property_name": "State",
    "property_value": "1",
})
# Swap to fresh poll without State
fresh_poll = [pir_device(props=[["DebouncePeriod", "5"]])]
coord.devices = fresh_poll
coord.data = fresh_poll
applied = coord._reapply_overlay()
check("flat evt survives poll", applied == 1 and get_prop(fresh_poll[0], "State") == "1")


# ===================================================================
# Summary
# ===================================================================
print(f"\n=== SUMMARY ===")
passed = sum(1 for _, ok, _ in CHECKS if ok)
failed = sum(1 for _, ok, _ in CHECKS if not ok)
print(f"PASS={passed} FAIL={failed}")
for label, ok, detail in CHECKS:
    if not ok:
        print(f"  FAIL: {label} <- {detail}")

if failed:
    sys.exit(1)