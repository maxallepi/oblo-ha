"""Tests for wire property-name aliasing (PirState -> State and friends).

Why this exists: the gateway can name a property differently from the name our
entity map declares for that service. The app itself carries an alias table
(`mappedPropertyName` in app.js) because firmware/gateway combinations disagree:

    PirService:{PirState:{mapped_values:{true:"DETECTED", false:"NOT_DETECTED"}}}

Our motion entity reads "State". An event carrying `PirState` was written to
props["PirState"], the entity kept reading "State", and the sensor stayed
Unknown even though the event arrived -- indistinguishable from "the event never
came", which is what the log said at DEBUG level.

Scope discipline (negative controls at the bottom):
- aliases apply ONLY to alternate spellings of the same value property;
- display-only aliases (ActivePower -> "Active Power", PropAlertType -> "Type")
  must NOT be applied: they would break the v0.2.45 power resolver and the
  alert-type select/button;
- a binary sensor must never invent a state from a non-state property
  (DebouncePeriod=5 is not "motion detected");
- aliasing must survive the 60 s poll (overlay filed under the canonical key).

Run: python3 custom_components/oblo_living/tests/prop_alias_test.py
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
from custom_components.oblo_living.entity_map import (  # noqa: E402
    PROP_ALIASES, build_entities, canonical_prop, resolve_binary_prop,
    resolve_power_prop,
)
from custom_components.oblo_living.entities.binary_sensor import ObloBinarySensor  # noqa: E402
from custom_components.oblo_living.entities.sensor import ObloSensor  # noqa: E402

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


def motions_device():
    """Motion sensor as Andrew's gateway names it: service instance pir0,
    and a static cache that carries only config props (no State at all)."""
    return {
        "id": "1029",
        "name": "Hallway Motion",
        "class": "MotionSensor",
        "services": [{
            "service": "pir0",
            "service_type": "PirService",
            "props": [["DebouncePeriod", "5"], ["Sensitive", "2"],
                      ["Threshold", "1"]],
        }],
    }


def smoke_device(state_key="State"):
    return {
        "id": "1044",
        "name": "Kitchen Smoke",
        "class": "SmokeDetector",
        "services": [{
            "service": "SmokeService",
            "service_type": "SmokeService",
            "props": [[state_key, "0"]],
        }],
    }


def power_device():
    return {
        "id": "1052",
        "name": "Hallway Lights",
        "class": "QubinoOnOffRelay",
        "services": [{
            "service": "power0",
            "service_type": "OutletService",
            "props": [["ActivePower", "42.5"]],
        }],
    }


def first(ents, platform):
    for e in ents:
        if e["platform"] == platform:
            return e
    return None


# ----- 1. alias table itself ------------------------------------------------

print("\n--- canonical_prop ---")
check("PirState -> State", canonical_prop("PirState") == "State")
check("SmokeState -> State", canonical_prop("SmokeState") == "State")
check("PlugState -> State", canonical_prop("PlugState") == "State")
check("ReedSwitchState -> State", canonical_prop("ReedSwitchState") == "State")
check("WaterLevel -> Detected", canonical_prop("WaterLevel") == "Detected")
check("unknown prop untouched", canonical_prop("DebouncePeriod") == "DebouncePeriod")
check("None stays None", canonical_prop(None) is None)

print("\n--- aliases that must NOT be applied (they break working code) ---")
check("ActivePower NOT aliased (power resolver owns it)",
      "ActivePower" not in PROP_ALIASES)
check("PowerConsumption NOT aliased", "PowerConsumption" not in PROP_ALIASES)
check("PropAlertType NOT aliased (alert select writes it)",
      "PropAlertType" not in PROP_ALIASES)
check("PropAlertStartStop NOT aliased",
      "PropAlertStartStop" not in PROP_ALIASES)
check("ThermostatMode NOT aliased (select writes it)",
      "ThermostatMode" not in PROP_ALIASES)
check("TextToSpeech NOT aliased (media_player reads it)",
      "TextToSpeech" not in PROP_ALIASES)
check("canonical_prop('ActivePower') identity",
      canonical_prop("ActivePower") == "ActivePower")

# ----- 2. entity_map resolves the binary prop the service carries -----------

print("\n--- resolve_binary_prop ---")
svc_state = {"service": "smoke0", "props": {"State": "1"}}
check("declared Detected, service has State -> State",
      resolve_binary_prop(svc_state, "Detected") == "State")
svc_det = {"service": "smoke0", "props": {"Detected": "1"}}
check("declared Detected, service has Detected -> Detected",
      resolve_binary_prop(svc_det, "Detected") == "Detected")
svc_cfg = {"service": "pir0",
           "props": {"DebouncePeriod": "5", "Sensitive": "2"}}
check("no state-shaped prop -> keeps declared, never picks a config key",
      resolve_binary_prop(svc_cfg, "State") == "State")
check("None declared stays None", resolve_binary_prop(svc_state, None) is None)

# ----- 3. flat PirState event lights the motion entity ----------------------

print("\n--- PirState event -> motion entity ---")
dev = motions_device()
coord = make_coordinator([dev])
ent = first(build_entities([dev]), "binary_sensor")
sensor = ObloBinarySensor(coord, ent)
check("baseline is Unknown (config props only)", sensor.is_on is None,
      f"is_on={sensor.is_on!r}")

coord.handle_event({
    "name": "propertyChanged", "device_id": "1029", "service_name": "pir0",
    "property_name": "PirState", "property_value": "1",
})
check("motion is ON after PirState=1", sensor.is_on is True,
      f"is_on={sensor.is_on!r}, props={coord.data[0]['services'][0]['props']!r}")
check("props stored under canonical State (not PirState)",
      dict(coord.data[0]["services"][0]["props"]).get("State") == "1",
      f"props={coord.data[0]['services'][0]['props']!r}")
check("no stray PirState key left in the tree",
      "PirState" not in dict(coord.data[0]["services"][0]["props"]))

# the 60 s poll returns the same service block with no State
poll = {"id": "1029", "name": "Hallway Motion", "services": [{
    "service": "pir0", "service_type": "PirService",
    "props": [["DebouncePeriod", "5"], ["Sensitive", "2"], ["Threshold", "1"]],
}]}
coord.devices = [poll]
coord.data = [poll]
coord._reapply_overlay()
check("motion survives a poll that omits State", sensor.is_on is True,
      f"is_on={sensor.is_on!r}")

# clear event, same alias family
coord.handle_event({
    "name": "propertyChanged", "device_id": "1029", "service_name": "pir0",
    "property_name": "PirState", "property_value": "0",
})
check("motion is OFF after PirState=0", sensor.is_on is False,
      f"is_on={sensor.is_on!r}")

# ----- 4. nested payload + service TYPE name still works --------------------

print("\n--- nested payload, service type name ---")
dev2 = motions_device()
dev2["id"] = "1031"
coord2 = make_coordinator([dev2])
sensor2 = ObloBinarySensor(coord2, first(build_entities([dev2]), "binary_sensor"))
coord2.handle_event({"name": "evt", "params": {
    "deviceId": "1031", "serviceName": "PirService",
    "propertyName": "PirState", "propertyValue": "1",
}})
check("nested PirState event on PirService type -> ON", sensor2.is_on is True,
      f"is_on={sensor2.is_on!r}")

# ----- 5. smoke: declared Detected, service reports State ------------------

print("\n--- smoke: declared Detected, tree carries State ---")
sdev = smoke_device("State")
scoord = make_coordinator([sdev])
sents = build_entities([sdev])
ssensor = ObloBinarySensor(scoord, first(sents, "binary_sensor"))
check("smoke descriptor prop resolved to State",
      first(sents, "binary_sensor")["value_prop"] == "State",
      f"prop={first(sents, 'binary_sensor')['value_prop']!r}")
check("smoke is OFF for State=0", ssensor.is_on is False,
      f"is_on={ssensor.is_on!r}")
scoord.handle_event({"name": "evt", "device_id": "1044",
                     "service_name": "SmokeService",
                     "property_name": "SmokeState", "property_value": "1"})
check("SmokeState=1 -> smoke ON", ssensor.is_on is True,
      f"is_on={ssensor.is_on!r}")

# ----- 6. no lie: config-only props never become a state ------------------

print("\n--- no invented state ---")
cdev = motions_device()
ccord = make_coordinator([cdev])
csensor = ObloBinarySensor(ccord, first(build_entities([cdev]), "binary_sensor"))
check("config-only motion sensor stays Unknown, not False",
      csensor.is_on is None, f"is_on={csensor.is_on!r}")

# ----- 7. power resolver untouched by aliasing ---------------------------

print("\n--- power regression (v0.2.45) ---")
pdev = power_device()
pcoord = make_coordinator([pdev])
pents = build_entities([pdev])
psensor = ObloSensor(pcoord, first(pents, "sensor"))
check("power descriptor prop resolves to ActivePower",
      first(pents, "sensor")["value_prop"] == "ActivePower",
      f"prop={first(pents, 'sensor')['value_prop']!r}")
check("power reads 42.5", psensor.native_value == 42.5,
      f"value={psensor.native_value!r}")
pcoord.handle_event({"name": "evt", "device_id": "1052",
                     "service_name": "power0", "property_name": "ActivePower",
                     "property_value": "43.0"})
check("ActivePower event still stored under ActivePower",
      dict(pcoord.data[0]["services"][0]["props"]).get("ActivePower") == "43.0",
      f"props={pcoord.data[0]['services'][0]['props']!r}")
check("power reads updated 43.0", psensor.native_value == 43.0,
      f"value={psensor.native_value!r}")
check("resolve_power_prop still handles ActivePower",
      resolve_power_prop({"props": {"ActivePower": "1"}}, "Power") == "ActivePower")

# ----- summary ------------------------------------------------------------

print()
passed = sum(1 for _, ok, _ in CHECKS if ok)
failed = [c for c in CHECKS if not c[1]]
print(f"{passed}/{len(CHECKS)} checks passed")
if failed:
    for label, _, detail in failed:
        print(f"  FAILED: {label}" + (f"  <- {detail}" if detail else ""))
    sys.exit(1)
print("ALL CHECKS PASSED")
