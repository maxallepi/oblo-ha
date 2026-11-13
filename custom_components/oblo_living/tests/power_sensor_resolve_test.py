"""Power sensor must read the property the gateway actually reports.

Andrew's report: "Hallway Lights Power shows as Unknown and NOT reporting power
usage" on the Qubino relay (device 1052, `outlet0` / `OutletService`).

What the app bundle says (oblo_service_property_catalog.json): `OutletService`
defines exactly ONE property, `State`. There is no `PowerConsumptionService`,
no `power0`, and no power/metering vocabulary anywhere in the catalog. So a
Power sensor is OUR addition (Tuya parity), not an app feature -- and the
gateway only exposes it if the device really meters.

Two failure shapes this test pins down:

1. The service IS there but the value lives under another key
   (`ActivePower`, `Watt`, `Value`, lowercase `power`, ...). The old code read
   only the declared key `Power` -> permanent Unknown while the number sat in
   the tree under a different name.
2. The service is there but carries no numeric power property at all. The
   entity must stay Unknown (never invent a value) and warn once with the
   property names it did see, so the log answers the question.

Registry stability matters: the resolved key must NOT change `unique_id` --
an entity that changes identity on a key rename loses its history.

Run: python3 power_sensor_resolve_test.py
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
    POWER_SERVICES,
    build_entities,
    props_as_map,
    resolve_power_prop,
)
from custom_components.oblo_living.entities.sensor import ObloSensor  # noqa: E402

CHECKS = []

import logging  # noqa: E402
records = []


class Grab(logging.Handler):
    def emit(self, record):
        records.append(record.getMessage())


lg = logging.getLogger("custom_components.oblo_living.entities.sensor")
lg.addHandler(Grab())
lg.setLevel(logging.WARNING)


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}"
          + (f"  <- {detail}" if detail and not cond else ""))


class FakeCoordinator:
    """Minimal coordinator surface ObloSensor touches."""

    def __init__(self, devices, entry_id="entry1", serial="SN1"):
        self.data = devices
        self.entry_id = entry_id
        self.gateway_serial = serial
        self.updates = 0

    def async_set_updated_data(self, _data):
        self.updates += 1

    def async_add_listener(self, *_a, **_k):
        return lambda: None


def dev_with(services, did="1052", name="Hallway Lights"):
    return [{
        "id": did,
        "class": "ohm",
        "name": name,
        "services": services,
    }]


def power_entity(services, did="1052"):
    ents = build_entities(dev_with(services, did))
    for e in ents:
        if e["platform"] == "sensor" and e.get("device_class") == "power":
            return e
    return None


# ── 1. Declared key present: unchanged behaviour ────────────────────────
svc = {"service": "power0", "props": [["Power", "42.5"]]}
ent = power_entity([svc])
check("power0/Power -> entity created", ent is not None)
check("declared Power kept as value_prop", ent and ent["value_prop"] == "Power",
      ent and ent["value_prop"])
check("initial state read from props", ent and ent["state"] == "42.5",
      ent and ent["state"])

# ── 2. Value lives under ActivePower (the likely Qubino meter key) ──────
svc = {"service": "power0", "props": [["ActivePower", 108.4]]}
ent = power_entity([svc])
check("ActivePower only -> value_prop resolves to ActivePower",
      ent and ent["value_prop"] == "ActivePower", ent and ent["value_prop"])
check("ActivePower only -> state populated (was None before fix)",
      ent and ent["state"] == 108.4, ent and ent["state"])

# ── 3. Other real-world spellings ───────────────────────────────────────
for prop in ("Watt", "PowerConsumption", "InstantaneousPower", "Energy"):
    svc = {"service": "power0", "props": [[prop, 7.25]]}
    e = power_entity([svc])
    check(f"{prop} only -> resolved", e and e["value_prop"] == prop,
          e and e["value_prop"])

# ── 4. Case-insensitive declared match ──────────────────────────────────
svc = {"service": "power0", "props": [["power", "13.0"]]}
ent = power_entity([svc])
check("lowercase 'power' -> resolves to lowercase key",
      ent and ent["value_prop"] == "power", ent and ent["value_prop"])
check("lowercase 'power' -> state populated", ent and ent["state"] == "13.0",
      ent and ent["state"])

# ── 5. Dict-shaped props (gateway/backup variant) ───────────────────────
svc = {"service": "power0", "props": {"ActivePower": "55.5"}}
ent = power_entity([svc])
check("dict props -> ActivePower resolved", ent and ent["value_prop"] == "ActivePower",
      ent and ent["value_prop"])
check("dict props -> state populated", ent and ent["state"] == "55.5",
      ent and ent["state"])

# ── 6. Single numeric unknown key: use it rather than stay blind ────────
svc = {"service": "power0", "props": [["SomeMeterValue", "3.5"], ["Flag", "on"]]}
ent = power_entity([svc])
check("single numeric unknown key -> adopted",
      ent and ent["value_prop"] == "SomeMeterValue", ent and ent["value_prop"])

# ── 7. No numeric property: keep declared, never invent ─────────────────
svc = {"service": "power0", "props": [["Flag", "on"], ["Mode", "auto"]]}
ent = power_entity([svc])
check("no numeric prop -> declared prop kept (no guessing)",
      ent and ent["value_prop"] == "Power", ent and ent["value_prop"])
check("no numeric prop -> state is None, not fabricated",
      ent is not None and ent["state"] is None, ent and ent["state"])

# ── 8. Empty props (gateway names the service, reports nothing) ─────────
svc = {"service": "power0", "props": []}
ent = power_entity([svc])
check("empty props -> entity still built (registry stability)",
      ent is not None)
check("empty props -> state None", ent is not None and ent["state"] is None)

# ── 9. unique_id must not move when the key changes ─────────────────────
a = power_entity([{"service": "power0", "props": [["Power", 1]]}])
b = power_entity([{"service": "power0", "props": [["ActivePower", 1]]}])
check("unique_id stable across key rename",
      a and b and a["unique_id"] == b["unique_id"],
      (a and a["unique_id"], b and b["unique_id"]))

# ── 10. Runtime re-resolution in native_value ───────────────────────────
# Entity created while the service was empty (value_prop "Power"); the next
# poll starts reporting ActivePower. native_value must pick it up without a
# reload.
coord = FakeCoordinator(dev_with([{"service": "power0", "props": []}]))
ent_desc = power_entity([{"service": "power0", "props": []}])
sensor = ObloSensor(coord, ent_desc)
check("native_value Unknown when nothing reported", sensor.native_value is None,
      sensor.native_value)
coord.data = dev_with([{"service": "power0", "props": [["ActivePower", "61.2"]]}])
check("native_value picks up ActivePower on the next poll",
      sensor.native_value == 61.2, sensor.native_value)

# Declared key present: still read directly.
coord2 = FakeCoordinator(dev_with([{"service": "power0", "props": [["Power", "10"]]}]))
sensor2 = ObloSensor(coord2, power_entity([{"service": "power0", "props": [["Power", "10"]]}]))
check("native_value reads declared Power", sensor2.native_value == 10.0,
      sensor2.native_value)

# ── 11. Warning is emitted once and names the props ─────────────────────
coord3 = FakeCoordinator(dev_with([{"service": "power0", "props": [["Flag", "on"]]}]))
s3 = ObloSensor(coord3, power_entity([{"service": "power0", "props": [["Flag", "on"]]}]))
_mark = len(records)
s3.native_value
s3.native_value
s3.native_value
warns = [r for r in records[_mark:] if "Power sensor" in r]
check("warns exactly once while unreadable", len(warns) == 1, len(warns))
check("warning lists the props actually present",
      warns and "flag" in warns[0], warns[0] if warns else "no warning")

# ── 12. Non-power sensors untouched ─────────────────────────────────────
# A temperature service keeps its declared key even when another numeric
# property exists; the resolver is scoped to power services only.
tem = {"service": "temperature0", "props": [["Value", "21.5"], ["Offset", "0.5"]]}
ents = build_entities(dev_with([tem], did="900", name="Temp"))
tent = [e for e in ents if e["platform"] == "sensor"]
check("temperature sensor still reads Value",
      tent and tent[0]["value_prop"] == "Value", tent and tent[0]["value_prop"])

# ── Summary ─────────────────────────────────────────────────────────────
fails = [c for c in CHECKS if not c[1]]
print(f"\n{len(CHECKS) - len(fails)}/{len(CHECKS)} checks passed")
sys.exit(1 if fails else 0)
