"""Device registry attachment: entities declare DeviceInfo so HA groups them
onto a single device page (the integration's device list). This is what the
Tuya Local page has and what OBLO was missing before v0.2.36.

Covers:
- build_entities output carries device_id/device_name/model/via_device
- Every entity type sets _attr_device_info with identifiers {(DOMAIN, id)}
- All entities of one device share identical identifiers -> one device page
- Alert select + 2 buttons share identifiers with their device
- via_device points at gateway identifier when gateway_serial present
- Manifest has integration_type: hub
- Gateway registration happens in setup (registry created list)
- Two entries (different entry_id) never collide on same OHM object id
"""
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
    build_entities, device_info_fields, device_identifier,
    gateway_identifier, MANUFACTURER, DOMAIN,
)
from custom_components.oblo_living.entities.binary_sensor import ObloBinarySensor  # noqa: E402
from custom_components.oblo_living.entities.sensor import ObloSensor  # noqa: E402
from custom_components.oblo_living.entities.switch import ObloSwitch  # noqa: E402
from custom_components.oblo_living.entities.select import ObloAlertTypeSelect  # noqa: E402
from custom_components.oblo_living.entities.button import (  # noqa: E402
    ObloSoundAlertButton, ObloSilenceAlertButton,
)

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}"
          + (f"  <- {detail}" if detail and not cond else ""))


# ----- fixture: fake coordinator --------------------------------------------

def make_coordinator(devices=None, entry_id="test_entry", gateway_serial="SN123"):
    hass = type("H", (), {"loop": None})()
    coord = ObloDataCoordinator(hass, api=None)
    coord.devices = list(devices or [])
    coord.data = list(devices or [])
    coord.entry_id = entry_id
    coord.gateway_serial = gateway_serial
    return coord


# ----- build_entities carries device fields ---------------------------------

print("\n--- build_entities output ---")

dev = {
    "id": "1052",
    "name": "Hallway Lights",
    "class": "outlet0",
    "services": [{
        "service": "outlet0",
        "service_type": "OutletService",
        "props": [("DeviceType", "16"), ("State", "false")],
    }],
}

ents = build_entities([dev])

# all entities for this device should carry the same device_id/name/model
for e in ents:
    check("has device_id", e.get("device_id") == "1052")
    check("has device_name", e.get("name") == "Hallway Lights")
    check("has class", e.get("class") == "outlet0")
    check("has model", e.get("model") == "Smart Relay")
    # via_device is the gateway serial, not present in build_entities (added by entities)

# unique_id distinct per entity
uids = [e["unique_id"] for e in ents]
check("unique_ids distinct", len(set(uids)) == len(uids))


# ----- entity DeviceInfo basics ---------------------------------------------

print("\n--- ObloBinarySensor ---")
coord = make_coordinator()
pir_dev = {
    "id": "9",
    "name": "Back Motion",
    "class": "pir0",
    "services": [{"service": "pir0", "service_type": "PirService",
                  "props": [("Alarm", "0")]}],
}
pir_ents = build_entities([pir_dev])
sensor_ent = ObloBinarySensor(coord, [e for e in pir_ents if e["platform"] == "binary_sensor"][0])
di = sensor_ent._attr_device_info
check("has identifiers", di.identifiers == {(DOMAIN, device_identifier("test_entry", "9"))})
check("name", di.name == "Back Motion")
check("manufacturer", di.manufacturer == MANUFACTURER)
check("model", di.model == "Motion Sensor")
check("via_device", di.via_device == (DOMAIN, gateway_identifier("test_entry")))

# without gateway_serial
coord2 = make_coordinator(gateway_serial=None)
sensor_ent2 = ObloBinarySensor(coord2, [e for e in pir_ents if e["platform"] == "binary_sensor"][0])
di2 = sensor_ent2._attr_device_info
check("via_device omitted when no serial", not getattr(di2, "via_device", None))


print("\n--- ObloSensor ---")
coord = make_coordinator()
temp_dev = {
    "id": "7",
    "name": "Temp",
    "class": "temperature0",
    "services": [{"service": "temperature0", "service_type": "TemperatureService",
                  "props": [("Temperature", "21.5")]}],
}
temp_ents = build_entities([temp_dev])
sensor_ent = ObloSensor(coord, [e for e in temp_ents if e["platform"] == "sensor"][0])
di = sensor_ent._attr_device_info
check("sensor identifiers match", di.identifiers == {(DOMAIN, device_identifier("test_entry", "7"))})
check("sensor name", di.name == "Temp")
check("sensor model", di.model == "Temperature Sensor")


print("\n--- ObloSwitch ---")
coord = make_coordinator()
switch_ent = ObloSwitch(coord, [e for e in ents if e["platform"] == "switch"][0])
di = switch_ent._attr_device_info
check("switch identifiers match", di.identifiers == {(DOMAIN, device_identifier("test_entry", "1052"))})
check("switch name", di.name == "Hallway Lights")
check("switch model", di.model == "Smart Relay")


# ----- alert entities: select + 2 buttons share device_id -------------------

print("\n--- Alert entities share device ---")
alert_dev = {
    "id": "15",
    "name": "Gateway Simple Alert",
    "class": "SimpleAlert",
    "services": [{
        "service": "SimpleAlertService",
        "service_type": "SimpleAlertService",
        "props": [("DeviceType", "18")],
    }],
}
alert_ents = build_entities([alert_dev])
alert_ents = [e for e in alert_ents if e["service"] == "SimpleAlertService"]

coord = make_coordinator()
alert_ids = set()
for e in alert_ents:
    if e["platform"] == "select":
        ent_obj = ObloAlertTypeSelect(coord, e)
    elif e["name_suffix"] == "Sound":
        ent_obj = ObloSoundAlertButton(coord, e)
    elif e["name_suffix"] == "Silence":
        ent_obj = ObloSilenceAlertButton(coord, e)
    else:
        raise AssertionError("unknown alert entity")
    di = ent_obj._attr_device_info
    alert_ids.add(frozenset(di.identifiers))
    check(f"{e['name_suffix']} identifiers", di.identifiers == {(DOMAIN, device_identifier("test_entry", "15"))})
    check(f"{e['name_suffix']} name", di.name == "Gateway Simple Alert")
    # class "SimpleAlert" resolves through the app class->name table
    check(f"{e['name_suffix']} model", di.model == "Internal Siren")

check("all 3 share same identifiers", len(alert_ids) == 1)


# ----- entry-scoped identifiers: no collision across entries ----------------

print("\n--- entry-scoped identifiers ---")
id1 = device_identifier("entry_a", "1052")
id2 = device_identifier("entry_b", "1052")
check("different entry_id -> different identifiers", id1 != id2)

# gateway identifier also entry-scoped
gid1 = gateway_identifier("entry_a")
gid2 = gateway_identifier("entry_b")
check("gateway identifiers entry-scoped", gid1 != gid2)


# ----- manifest integration_type: hub ---------------------------------------

print("\n--- manifest ---")
import json
manifest_path = os.path.join(COMPONENT, "manifest.json")
with open(manifest_path) as f:
    manifest = json.load(f)
check("integration_type is hub", manifest.get("integration_type") == "hub")


# ----- gateway device registration ------------------------------------------

print("\n--- gateway registration ---")
from custom_components.oblo_living import _async_register_gateway_device  # noqa: E402
import homeassistant.helpers.device_registry as dr_mod  # noqa: E402

dr_mod._REGISTRY.created.clear()


class FakeEntry:
    entry_id = "setup_test_entry"


class FakeHass:
    loop = None
    data = {}


class FakeCoord:
    """Minimal coordinator shape used by the gateway registration."""
    gateway_serial = "GW999"
    gateway_name = None
    gateway_model = None
    gateway_sw_version = None
    gateway_hw_version = None


rec = _async_register_gateway_device(FakeHass(), FakeEntry(), FakeCoord())
created = dr_mod._REGISTRY.created

check("registry received one device", len(created) == 1)
if created:
    kw = created[0]
    check("gateway identifiers entry-scoped",
          kw.get("identifiers") == {(DOMAIN, gateway_identifier("setup_test_entry"))})
    check("gateway serial_number", kw.get("serial_number") == "GW999")
    check("gateway name carries serial", kw.get("name") == "OBLO Gateway GW999")
    check("gateway manufacturer", kw.get("manufacturer") == MANUFACTURER)
    check("gateway config_entry_id", kw.get("config_entry_id") == "setup_test_entry")

check("registration returns the record", rec is not None)

# gateway metadata from the cloud object wins over the generated name
dr_mod._REGISTRY.created.clear()


class RichCoord(FakeCoord):
    gateway_name = "Living Room"
    gateway_model = "DOC300"
    gateway_sw_version = "5.4.0"
    gateway_hw_version = "5.4.0"


rec_rich = _async_register_gateway_device(FakeHass(), FakeEntry(), RichCoord())
if dr_mod._REGISTRY.created:
    kwr = dr_mod._REGISTRY.created[0]
    check("gateway name from cloud", kwr.get("name") == "Living Room")
    check("gateway model from cloud", kwr.get("model") == "DOC300")
    check("gateway sw_version", kwr.get("sw_version") == "5.4.0")
    check("gateway hw_version", kwr.get("hw_version") == "5.4.0")
    check("gateway serial still set", kwr.get("serial_number") == "GW999")
check("rich registration returns the record", rec_rich is not None)

# no serial -> still registers, name has no trailing space, serial_number None
dr_mod._REGISTRY.created.clear()


class NoSerialCoord(FakeCoord):
    gateway_serial = None


rec2 = _async_register_gateway_device(FakeHass(), FakeEntry(), NoSerialCoord())
check("registers without serial", len(dr_mod._REGISTRY.created) == 1)
if dr_mod._REGISTRY.created:
    kw2 = dr_mod._REGISTRY.created[0]
    check("name without serial", kw2.get("name") == "OBLO Gateway")
    check("serial_number None", kw2.get("serial_number") is None)

# a registry failure must never bubble up (setup would fail for a nicety)
class BoomRegistry:
    def async_get_or_create(self, **kwargs):
        raise RuntimeError("registry exploded")


import homeassistant.helpers.device_registry as _drm  # noqa: E402
_saved = _drm.async_get
_drm.async_get = lambda hass: BoomRegistry()
try:
    rec3 = _async_register_gateway_device(FakeHass(), FakeEntry(), FakeCoord())
    check("registry failure swallowed", rec3 is None)
finally:
    _drm.async_get = _saved

# setup calls it (static wiring check: the call site passes the coordinator)
import inspect  # noqa: E402
src = inspect.getsource(__import__(
    "custom_components.oblo_living", fromlist=["x"]).async_setup_entry)
check("setup registers the gateway device",
      "_async_register_gateway_device(hass, entry" in src)
check("setup passes the coordinator (holds serial + metadata)",
      "_async_register_gateway_device(hass, entry, coordinator)" in src)
check("setup calls it after the first refresh",
      src.index("async_config_entry_first_refresh")
      < src.index("_async_register_gateway_device(hass, entry"))


# ----- model / manufacturer resolution (app class table + Z-Wave vendor) -----

print("\n--- model & manufacturer resolution ---")
from custom_components.oblo_living.entity_map import (  # noqa: E402
    APP_CLASS_NAMES, ZWAVE_VENDORS, device_manufacturer, device_model,
)

# app class->name table wins over the service fallback
check("app class name wins",
      device_model("Qubino On/Off Relay", ["outlet0"]) == "On/Off Relay")
check("SimpleAlert -> Internal Siren",
      device_model("SimpleAlert", ["SimpleAlertService"]) == "Internal Siren")
# unknown class falls back to the service-derived model
check("unknown class -> service model",
      device_model("SomeUnknownClass", ["outlet0"]) == "Smart Relay")
# unknown class with unknown services -> class itself (still a real string)
check("unknown class, unknown services -> class",
      device_model("Acme Widget", []) == "Acme Widget")
# source marker never surfaces as a model
check("ohm class never surfaces",
      device_model("ohm", []) == "OBLO Device")
check("hg class never surfaces",
      device_model("hg", []) == "OBLO Device")
check("no class no services -> OBLO Device",
      device_model(None, []) == "OBLO Device")

# Qubino relay: mfr id 345 from ManufacturerSpecific, decimal and hex forms
qub_services = [
    {"service": "ManufacturerSpecific",
     "props": [("ManufacturerID", "345"), ("ProductTypeID", "2"),
               ("ProductID", "82")]},
    {"service": "ZWaveService", "props": [("NodeID", "14")]},
    {"service": "outlet0", "props": [("State", "false")]},
]
check("Qubino manufacturer from mfr id",
      device_manufacturer(qub_services) == "Qubino")
check("hex mfr id also resolves",
      device_manufacturer([{"service": "ManufacturerSpecific",
                            "props": [("ManufacturerID", "0x0159")]}]) == "Qubino")
# no ManufacturerSpecific -> OBLO
check("no ManufacturerSpecific -> OBLO",
      device_manufacturer([{"service": "outlet0", "props": []}]) == MANUFACTURER)
check("garbage mfr id -> OBLO",
      device_manufacturer([{"service": "ManufacturerSpecific",
                            "props": [("ManufacturerID", "not-an-int")]}])
      == MANUFACTURER)
# siren (device 15) has zero properties -> still OBLO Living
check("siren has no vendor -> OBLO",
      device_manufacturer([{"service": "SimpleAlertService", "props": []},
                           {"service": "OperationalService", "props": []}])
      == MANUFACTURER)

# end-to-end through build_entities: entity dicts carry both fields
qdev = {
    "id": "1052", "name": "Hallway Lights", "class": "Qubino On/Off Relay",
    "services": qub_services,
}
qents = build_entities([qdev])
check("entity carries Qubino manufacturer",
      all(e.get("manufacturer") == "Qubino" for e in qents) and qents)
check("entity carries app class model",
      all(e.get("model") == "On/Off Relay" for e in qents) and qents)
qsw = [e for e in qents if e["platform"] == "switch"]
check("Qubino relay yields a switch entity", len(qsw) == 1)
if qsw:
    di_q = ObloSwitch(make_coordinator(), qsw[0])._attr_device_info
    check("device_info manufacturer is Qubino", di_q.manufacturer == "Qubino")
    check("device_info model is On/Off Relay", di_q.model == "On/Off Relay")

# siren device 15 keeps OBLO Living as manufacturer
sents = build_entities([alert_dev])
check("siren entity manufacturer OBLO",
      all(e.get("manufacturer") == MANUFACTURER for e in sents) and sents)
check("siren model from app class table",
      all(e.get("model") == "Internal Siren" for e in sents) and sents)


# ----- summary --------------------------------------------------------------

print(f"\n=== SUMMARY: {sum(1 for _, c, _ in CHECKS if c)}/{len(CHECKS)} checks passed ===")
if any(not c for _, c, _ in CHECKS):
    for label, cond, detail in CHECKS:
        if not cond:
            print(f"  FAIL: {label}  <- {detail}")
    sys.exit(1)