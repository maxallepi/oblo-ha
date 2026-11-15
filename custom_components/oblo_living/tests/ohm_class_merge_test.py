"""A real device class must survive the merge with an OHM record.

Regression guard for the v0.2.38 defect: merge_devices() keeps the record with
the most services/props -- and the OHM record always wins that contest because
it carries live property values. But normalise_ohm_objects() stamps every OHM
device with class "ohm", a source marker, while the REST record is the only one
that knows the real class ("Qubino On/Off Relay", "SimpleAlert", ...).

So the live path discarded the class before build_entities() ever saw it, and
APP_CLASS_NAMES -- the whole point of the app.js class table -- could never
fire in production. Every device fell back to its service-derived label:

    15   Internal Siren            -> Alert Siren
    1052 On/Off Relay              -> Smart Relay
    1027 Door/Window Detector MINI -> Door / Window Sensor

Labels asserted from the backup (REST-shaped) passed; labels on the live path
did not. v0.2.39 keeps the richest record AND adopts a real class from any
other record for the same device id.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401

from custom_components.oblo_living.coordinator import (  # noqa: E402
    merge_devices, normalise_ohm_objects,
)
from custom_components.oblo_living.entity_map import build_entities, device_model  # noqa: E402

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


# REST-shaped records: real class, few props (this is what the backup holds and
# what cloud_api.normalise_devices produces).
REST = [
    {"id": 1052, "class": "Qubino On/Off Relay", "name": "Hallway Lights",
     "services": [{"service": "outlet0", "props": [["DeviceType", "16"]]}]},
    {"id": 15, "class": "SimpleAlert", "name": "Gateway Simple Alert 15",
     "services": [{"service": "SimpleAlertService", "props": []}]},
    {"id": 1027, "class": "Everspring Door Window Mini", "name": "Back Door Sensor 1027",
     "services": [{"service": "ContactService", "props": []}]},
    {"id": 1029, "class": "SENSOR NOTIFICATION", "name": "Front Motion Sensor 1029",
     "services": [{"service": "pir0", "props": []}]},
]

# OHM-shaped payload: live props (so it wins on weight), no class field at all.
OHM_PAYLOAD = [
    {"id": "1052", "device_name": "Hallway Lights", "service_list": [
        {"name": "outlet0", "type": "OutletService",
         "property_list": [{"name": "State", "value": "true"},
                           {"name": "DeviceType", "value": "16"}]},
        {"name": "power0", "type": "PowerConsumptionService",
         "property_list": [{"name": "ActivePower", "value": "12.4"}]}]},
    {"id": "15", "device_name": "Gateway Simple Alert 15", "service_list": [
        {"name": "SimpleAlertService", "type": "SimpleAlertService",
         "property_list": [{"name": "PropAlertType", "value": "2"},
                           {"name": "PropAlertStartStop", "value": "false"}]}]},
    {"id": "1027", "device_name": "Back Door Sensor 1027", "service_list": [
        {"name": "ContactService", "type": "ContactService",
         "property_list": [{"name": "State", "value": "false"}]}]},
    {"id": "1029", "device_name": "Front Motion Sensor 1029", "service_list": [
        {"name": "pir0", "type": "PirService",
         "property_list": [{"name": "State", "value": "false"}]},
        {"name": "temperature0", "type": "TemperatureService",
         "property_list": [{"name": "Temperature", "value": "21.4"}]}]},
]

OHM = normalise_ohm_objects(OHM_PAYLOAD)

print("== preconditions")
check("OHM normaliser stamps class 'ohm'", {d["class"] for d in OHM} == {"ohm"})
check("OHM record is the richer one (wins on weight)",
      all(len(d["services"]) >= next(
          r["services"].__len__() for r in REST if str(r["id"]) == d["id"])
          for d in OHM))

print("\n== the defect: merge used to discard the real class")
merged = merge_devices(REST, OHM)
by_id = {int(d["id"]): d for d in merged}
check("1052 keeps its real class", by_id[1052]["class"] == "Qubino On/Off Relay",
      f"({by_id[1052]['class']!r})")
check("15 keeps its real class", by_id[15]["class"] == "SimpleAlert",
      f"({by_id[15]['class']!r})")
check("1027 keeps its real class", by_id[1027]["class"] == "Everspring Door Window Mini",
      f"({by_id[1027]['class']!r})")
check("1029 keeps its real class", by_id[1029]["class"] == "SENSOR NOTIFICATION",
      f"({by_id[1029]['class']!r})")

print("\n== the winner's data is untouched (richness still wins)")
check("1052 merged keeps OHM's 2 services", len(by_id[1052]["services"]) == 2,
      f"({[s['service'] for s in by_id[1052]['services']]})")
check("1052 merged keeps OHM's live State prop",
      dict(by_id[1052]["services"][0]["props"]).get("State") == "true")
check("merge still does not invent devices", len(merged) == len(REST),
      f"({len(merged)})")

print("\n== labels on the merged (live) path")
models = {int(e["device_id"]): e["model"] for e in build_entities(merged)}
check("1052 -> On/Off Relay", models[1052] == "On/Off Relay", f"({models[1052]!r})")
check("15 -> Internal Siren", models[15] == "Internal Siren", f"({models[15]!r})")
check("1027 -> Door/Window Detector MINI",
      models[1027] == "Door/Window Detector MINI", f"({models[1027]!r})")
check("1029 -> Motion Sensor (class has no app-table entry; primacy decides)",
      models[1029] == "Motion Sensor", f"({models[1029]!r})")

print("\n== order independence")
for first, second in ((REST, OHM), (OHM, REST)):
    m = {int(d["id"]): d["class"] for d in merge_devices(first, second)}
    check(f"same classes with args ({'REST' if first is REST else 'OHM'} first)",
          m[1052] == "Qubino On/Off Relay" and m[15] == "SimpleAlert",
          f"(1052={m[1052]!r})")

print("\n== id type must not split one device into two")
mixed = merge_devices(
    [{"id": 1060, "class": "Qubino On/Off Relay", "name": "Hallway",
      "services": [{"service": "outlet0", "props": [["DeviceType", "16"]]}]}],
    [{"id": "1060", "class": "ohm", "name": "Hallway",
      "services": [{"service": "outlet0",
                    "props": [["DeviceType", "16"], ["State", "true"]]}]}],
)
check("int 1060 and str '1060' are the same device", len(mixed) == 1,
      f"({len(mixed)} records; REST ids are numeric, OHM ids are strings)")
check("...and the richer one wins with the real class attached",
      len(mixed) == 1 and mixed[0]["class"] == "Qubino On/Off Relay"
      and dict(mixed[0]["services"][0]["props"]).get("State") == "true")
ents_mixed = build_entities(mixed)
check("no duplicate unique_ids from a mixed-id payload",
      len({e["unique_id"] for e in ents_mixed}) == len(ents_mixed),
      f"({len(ents_mixed)} entities, {len({e['unique_id'] for e in ents_mixed})} unique)")

print("\n== degenerate inputs")
check("OHM only -> class stays the marker", 
      {d["class"] for d in merge_devices(OHM)} == {"ohm"})
check("REST only -> class unchanged",
      {int(d["id"]): d["class"] for d in merge_devices(REST)}[1052]
      == "Qubino On/Off Relay")
check("empty lists are tolerated", merge_devices([], None, []) == [])
_check_input = [{"id": 7, "class": "Qubino On/Off Relay", "name": "x", "services": []}]
_res = merge_devices(_check_input)
check("merge does not mutate the caller's dict", _res[0] is not _check_input[0],
      "-> shallow copy per winning record")

print("\n== device_model still guards markers directly")
check("device_model('ohm', []) -> OBLO Device", device_model("ohm", []) == "OBLO Device")
check("device_model('hg', []) -> OBLO Device", device_model("hg", []) == "OBLO Device")

print(f"\n== {checks} checks, {failures} failures")
sys.exit(1 if failures else 0)
