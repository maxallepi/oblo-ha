"""Tests that the POLL path canonicalises wire property names, like the evt path.

v0.2.46 fixed `handle_event` (live MQTT evt) so a gateway emitting "PirState"
still lands on the "State" key our entity map reads. It did NOT touch the two
other paths that write the same cached device tree:

  * `normalise_ohm_objects`  -- the 60 s OHM object_list (poll)
  * `_poke_device`           -- the read-back of a confirmed write (ACK)
  * `normalise_devices`      -- the REST /hg/info payload

so a plug/smoke/contact/reed/alarm whose value only ever arrives on the poll
kept a "PlugState" key while the entity read "State" -> permanent Unknown.

Covers:
- OHM poll: PlugState/PirState/ReedSwitchState -> canonical "State"
- OHM poll: non-aliased names (DebouncePeriod, ActivePower, Threshold) untouched
- OHM poll: State+PirState in one payload collapse to one key (last wins)
- OHM poll: service_type preserved
- ACK: _poke_device writes the canonical key in props AND in the overlay
- ACK: an echoed wire spelling does not create a second key
- REST: pair-shaped props are canonicalised too
- end-to-end: poll payload -> resolve_binary_prop -> read_prop_strict value
- no invented state: absent property still resolves to None

Run: python3 custom_components/oblo_living/tests/poll_canonical_test.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401

from custom_components.oblo_living.cloud_api import normalise_devices  # noqa: E402
from custom_components.oblo_living.coordinator import (  # noqa: E402
    ObloDataCoordinator, normalise_ohm_objects,
)
from custom_components.oblo_living.entity_map import (  # noqa: E402
    read_prop_strict, resolve_binary_prop,
)

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}"
          + (f"  <- {detail}" if detail and not cond else ""))


def ohm_obj(did, name, sname, props):
    """props: list of (name, value) tuples -> OHM object_list entry."""
    return {
        "id": did,
        "device_name": name,
        "service_list": [{
            "name": sname,
            "type": sname.replace("0", "") + "Service",
            "property_list": [{"name": n, "value": v} for n, v in props],
        }],
    }


def svc_of(devices, did, sname):
    for d in devices:
        if d["id"] != did:
            continue
        for s in d["services"]:
            if s["service"] == sname:
                return s
    return {}


# ----- 1. OHM poll canonicalises -----------------------------------------

print("\n--- normalise_ohm_objects canonicalises wire names ---")
objects = [
    ohm_obj("1052", "Hallway Lights", "plug0", [("PlugState", "1"),
                                               ("DeviceType", 16)]),
    ohm_obj("1101", "Hall Pir", "pir0", [("PirState", "true"),
                                         ("DebouncePeriod", 5)]),
    ohm_obj("1102", "Front Door", "reed0", [("ReedSwitchState", "0")]),
    ohm_obj("1103", "Kitchen Smoke", "smoke0", [("SmokeState", "false")]),
    ohm_obj("1104", "Garage Alarm", "alarm0", [("AlarmState", "2")]),
]
devices = normalise_ohm_objects(objects)

plug = svc_of(devices, "1052", "plug0")
check("poll: plug0 has canonical 'State'", "State" in plug["props"],
      f"props={plug['props']}")
check("poll: plug0 'State' == '1'", plug["props"].get("State") == "1",
      f"props={plug['props']}")
check("poll: plug0 has no wire 'PlugState' key",
      "PlugState" not in plug["props"], f"props={plug['props']}")
check("poll: DeviceType still under its own name",
      plug["props"].get("DeviceType") == 16, f"props={plug['props']}")

pir = svc_of(devices, "1101", "pir0")
check("poll: pir0 'State' == 'true'", pir["props"].get("State") == "true",
      f"props={pir['props']}")
check("poll: pir0 no 'PirState' key", "PirState" not in pir["props"],
      f"props={pir['props']}")
check("poll: DebouncePeriod not aliased",
      pir["props"].get("DebouncePeriod") == 5, f"props={pir['props']}")

reed = svc_of(devices, "1102", "reed0")
check("poll: reed0 'State' == '0'", reed["props"].get("State") == "0",
      f"props={reed['props']}")

smoke = svc_of(devices, "1103", "smoke0")
check("poll: smoke0 'State' == 'false'", smoke["props"].get("State") == "false",
      f"props={smoke['props']}")

alarm = svc_of(devices, "1104", "alarm0")
check("poll: alarm0 'State' == '2'", alarm["props"].get("State") == "2",
      f"props={alarm['props']}")

check("poll: service_type preserved through canonicalisation",
      plug.get("service_type") == "plugService", f"svc={plug}")

# ----- 2. collision -------------------------------------------------------

print("\n--- collision: State + PirState in one payload ---")
coll = normalise_ohm_objects([
    ohm_obj("1200", "Collide", "pir0", [("State", "true"), ("PirState", "0")]),
])
cp = svc_of(coll, "1200", "pir0")["props"]
check("collision: exactly one state key", len(cp) == 1, f"props={cp}")
check("collision: last entry wins (PirState=0)",
      cp.get("State") in ("0", 0), f"props={cp}")

# ----- 3. non-aliased props are untouched ---------------------------------

print("\n--- non-aliased names untouched ---")
keep = normalise_ohm_objects([
    ohm_obj("1300", "Meter", "power0", [("ActivePower", "41.2"),
                                        ("PowerConsumption", "120.5"),
                                        ("Threshold", 3)]),
])
kp = svc_of(keep, "1300", "power0")["props"]
check("ActivePower NOT aliased (power resolver depends on it)",
      kp.get("ActivePower") == "41.2", f"props={kp}")
check("PowerConsumption NOT aliased",
      kp.get("PowerConsumption") == "120.5", f"props={kp}")
check("no phantom 'Active Power' key", "Active Power" not in kp,
      f"props={kp}")

# ----- 4. end-to-end entity read -----------------------------------------

print("\n--- poll payload -> entity read ---")
resolved = resolve_binary_prop(plug, "State")
check("resolve_binary_prop finds 'State' on polled plug", resolved == "State",
      f"resolved={resolved!r}")
check("strict read returns the polled value",
      read_prop_strict(plug, resolved) == "1",
      f"value={read_prop_strict(plug, resolved)!r}")

pir_resolved = resolve_binary_prop(pir, "State")
check("resolve_binary_prop finds 'State' on polled pir", pir_resolved == "State",
      f"resolved={pir_resolved!r}")
check("pir strict read is the wire truth",
      read_prop_strict(pir, pir_resolved) == "true",
      f"value={read_prop_strict(pir, pir_resolved)!r}")

# ----- 5. no invented state ----------------------------------------------

print("\n--- absent property stays Unknown ---")
empty = normalise_ohm_objects([ohm_obj("1400", "Bare", "reed0", [])])
empty_svc = svc_of(empty, "1400", "reed0")
check("empty service still resolves the declared key",
      resolve_binary_prop(empty_svc, "State") == "State",
      f"resolved={resolve_binary_prop(empty_svc, 'State')!r}")
check("empty service strict read is None (Unknown, not a lie)",
      read_prop_strict(empty_svc, "State") is None,
      f"value={read_prop_strict(empty_svc, 'State')!r}")

# ----- 6. ACK path canonicalises -----------------------------------------

print("\n--- _poke_device (write ACK) ---")


def bare_coordinator(devices=None):
    """Coordinator without HA's base class init -- only the fields we touch."""
    c = ObloDataCoordinator.__new__(ObloDataCoordinator)
    c.devices = devices if devices is not None else []
    c.data = list(c.devices)
    c._evt_overlay = {}
    return c


c = bare_coordinator(normalise_ohm_objects([
    ohm_obj("1052", "Hallway Lights", "plug0", [("State", "0")]),
]))
ok = c._poke_device("1052", "plug0", "PlugState", "1")
svc = c.devices[0]["services"][0]
check("ACK returned True", ok is True, f"ok={ok!r}")
check("ACK wrote canonical 'State'", svc["props"].get("State") == "1",
      f"props={svc['props']}")
check("ACK did not create a 'PlugState' duplicate",
      "PlugState" not in svc["props"], f"props={svc['props']}")
check("ACK overlay key is canonical",
      c._evt_overlay.get(("1052", "plug0"), {}).get("State") == "1",
      f"overlay={c._evt_overlay}")

c2 = bare_coordinator(normalise_ohm_objects([
    ohm_obj("1102", "Front Door", "reed0", [("State", "0")]),
]))
c2._poke_device("1102", "reed0", "ReedSwitchState", "1")
svc2 = c2.devices[0]["services"][0]
check("ACK echo of ReedSwitchState updates 'State'",
      svc2["props"].get("State") == "1", f"props={svc2['props']}")
check("ACK echo leaves exactly one state key",
      sum(1 for k in svc2["props"] if k.lower().endswith("state")) == 1,
      f"props={svc2['props']}")

c3 = bare_coordinator(normalise_ohm_objects([
    ohm_obj("1052", "Hallway Lights", "plug0", [("State", "0")]),
]))
check("ACK with None property returns False", c3._poke_device(
    "1052", "plug0", None, "1") is False, "expected False")

# ----- 7. REST path canonicalises ----------------------------------------

print("\n--- normalise_devices (REST) ---")
rest = normalise_devices([{
    "deviceId": "1052",
    "deviceName": "Hallway Lights",
    "services": [{"service": "plug0", "props": [["PlugState", "1"],
                                                ["DeviceType", 16]]}],
}])
rsvc = rest[0]["services"][0]
check("REST: props canonicalised", ["State", "1"] in rsvc["props"],
      f"props={rsvc['props']}")
check("REST: no wire name left", all(p[0] != "PlugState" for p in rsvc["props"]),
      f"props={rsvc['props']}")
check("REST: other props intact", ["DeviceType", 16] in rsvc["props"],
      f"props={rsvc['props']}")

# ----- summary -----------------------------------------------------------

print()
passed = sum(1 for _, ok_, _ in CHECKS if ok_)
failed = [x for x in CHECKS if not x[1]]
print(f"{passed}/{len(CHECKS)} checks passed")
if failed:
    for label, _, detail in failed:
        print(f"  FAILED: {label}" + (f"  <- {detail}" if detail else ""))
    sys.exit(1)
print("ALL CHECKS PASSED")
