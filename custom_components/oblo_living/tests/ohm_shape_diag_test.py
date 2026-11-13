"""ohm_shape_diag_test.py -- the DIAG line that decides model labels.

Two jobs:

1. `ohm_object_shape()` reports field NAMES only. If it ever printed a value,
   it would be the v0.2.13 leak again (raw dump, `secureId`/`hash` in the log),
   so the absence of values is asserted, not assumed.

2. It documents why the line is needed: `normalise_ohm_objects()` keeps only
   id / device_name / service_list. A payload that DOES carry a class loses it
   on the way in, which is what forces every device onto the service-derived
   label. The DIAG tells us whether the real payload has `class` to lose.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401

from custom_components.oblo_living.coordinator import (  # noqa: E402
    normalise_ohm_objects, ohm_object_shape,
)

checks = 0
failures = 0


def check(label, cond, extra=""):
    global checks, failures
    checks += 1
    if cond:
        print(f"  ok   {label} {extra}")
    else:
        failures += 1
        print(f"  FAIL {label} {extra}")


# Values chosen to be unmistakable if they ever reach the log.
LEAKY = {
    "id": "1052",
    "deviceName": "Hallway Lights",
    "class": "Qubino On/Off Relay",
    "fwVersion": "6.10",
    "busId": "12",
    "secureId": "ZMUUc1rFXkt5Yztw",
    "service_list": [{
        "name": "outlet0",
        "type": "OutletService",
        "property_list": [
            {"name": "State", "value": "true"},
            {"name": "DeviceType", "value": "16"},
        ],
    }],
}

print("== shape reports names, not values")
shape = ohm_object_shape([LEAKY])
print(f"  {json.dumps(shape, sort_keys=True)}")
text = json.dumps(shape, sort_keys=True)

check("top-level keys reported", shape.get("object") ==
      sorted(["id", "deviceName", "class", "fwVersion", "busId", "secureId",
              "service_list"]), f"({shape.get('object')})")
check("service_list keys reported",
      shape.get("service_list[0]") == sorted(["name", "type", "property_list"]),
      f"({shape.get('service_list[0]')})")
check("the answer to 'does class travel?' is visible in the line",
      "class" in (shape.get("object") or []), "-> class/fwVersion/busId all listed")
check("the answer to 'does fwVersion travel?' is visible in the line",
      "fwVersion" in (shape.get("object") or []))

for leak in ("Hallway Lights", "Qubino On/Off Relay", "6.10", "true", "16",
             "ZMUUc1rFXkt5Yztw", "outlet0", "OutletService"):
    check(f"value {leak!r} absent from the DIAG line", leak not in text)

print("\n== degenerate payloads must not raise")
for bad in ([], None, [{}], ["not-a-dict"], [{"service_list": "oops"}], [{"a": [1, 2]}]):
    try:
        out = ohm_object_shape(bad)
        check(f"tolerates {bad!r}", isinstance(out, dict), f"-> {out}")
    except Exception as exc:
        check(f"tolerates {bad!r}", False, f"raised {exc!r}")

print("\n== why the line is needed: the class is dropped on the way in")
norm = normalise_ohm_objects([LEAKY])
check("normaliser keeps the device", len(norm) == 1)
check("normaliser replaces the real class with the 'ohm' marker",
      norm[0]["class"] == "ohm",
      f"(payload said {'Qubino On/Off Relay'!r}, entity path sees {norm[0]['class']!r})")
check("normaliser emits no fw_version for the device page",
      "fw_version" not in norm[0] and "FwVersion" not in norm[0],
      "-> sw_version cannot be honest until the DIAG says the payload carries it")

print(f"\n== {checks} checks, {failures} failures")
sys.exit(1 if failures else 0)
