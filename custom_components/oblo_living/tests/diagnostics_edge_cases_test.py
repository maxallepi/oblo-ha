#!/usr/bin/env python3
"""diagnostics_edge_cases_test.py -- the three device kinds, and prop shapes.

An empty `live` list must never be the only way to tell "gateway page",
"device missing from the poll" and "identifier we cannot resolve" apart.
That ambiguity is what this release keeps deleting elsewhere, so it gets a
test here rather than a comment.

Also covers the prop-shape defect: OHM-sourced services carry `props` as a
dict, REST-shaped records as list-of-pairs. Reading only the list shape made
every service on the live path report `properties: []`.
"""
import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401

from custom_components.oblo_living import diagnostics as dg  # noqa: E402
from custom_components.oblo_living.entity_map import (  # noqa: E402
    device_identifier, gateway_identifier, ohm_id_from_identifier,
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


ENTRY_ID = "aabbccdd" + "0" * 24          # 32 hex, like a real entry_id
DEVICES = [{
    "id": 1052, "name": "Hallway Lights", "class": "Qubino On/Off Relay",
    "services": [{"service": "outlet0", "service_type": "OutletService",
                  "props": {"State": "true", "MeterReport_W": "42.5"}}],
}, {
    "id": 15, "name": "Gateway Simple Alert 15", "class": "SimpleAlert",
    "services": [{"service": "SimpleAlertService", "props": [
        ["PropAlertType", "2"], ["PropAlertStartStop", "false"]]}],
}]


class FakeEntry:
    entry_id = ENTRY_ID
    unique_id = "C83DD4300D5B"
    version = 2
    domain = "oblo_living"
    data = {"host": "192.168.1.16", "port": 8883,
            "secret": "SENTINEL_SECRET", "identity": "SENTINEL_EMAIL",
            "username": "cli/SENTINEL_USER"}
    options = {}


class FakeCoordinator:
    data = DEVICES
    gateway_serial = "C83DD4300D5B"
    gateway_name = "Living Room"
    gateway_model = "doc300"
    gateway_sw_version = "5.4.0"
    gateway_hw_version = "5.4.0"
    update_interval_s = 60
    last_update_success = True
    source = "mqtt"


class FakeHass:
    data = {"oblo_living": {ENTRY_ID: {"coordinator": FakeCoordinator()}}}


class FakeDevice:
    def __init__(self, identifiers):
        self.identifiers = identifiers
        self.name = "X"
        self.manufacturer = "Qubino"
        self.model = "Smart Relay"
        self.sw_version = "6.10"
        self.hw_version = None
        self.via_device_id = None


def run(identifiers):
    dev = FakeDevice(identifiers)
    return asyncio.run(
        dg.async_get_device_diagnostics(FakeHass(), FakeEntry(), dev))


print("== identifier helpers")
check("inverse of device_identifier round-trips",
      ohm_id_from_identifier(ENTRY_ID, device_identifier(ENTRY_ID, 1052)) == "1052")
check("gateway identifier resolves to the literal 'gateway'",
      ohm_id_from_identifier(ENTRY_ID, gateway_identifier(ENTRY_ID)) == "gateway")
check("foreign prefix returns None, not the input",
      ohm_id_from_identifier(ENTRY_ID, "unknown_1052") is None,
      "-> greppable, instead of silently never matching")
check("None identifier returns None", ohm_id_from_identifier(ENTRY_ID, None) is None)

print("\n== kind: device")
r = run({("oblo_living", device_identifier(ENTRY_ID, 1052))})
check("kind == 'device'", r.get("kind") == "device", f"({r.get('kind')!r})")
check("live has exactly one device", len(r.get("live") or []) == 1)
check("resolution.matched is True", (r.get("resolution") or {}).get("matched") is True)
check("no note on a clean match", "note" not in r)

print("\n== kind: gateway")
g = run({("oblo_living", gateway_identifier(ENTRY_ID))})
check("kind == 'gateway'", g.get("kind") == "gateway", f"({g.get('kind')!r})")
check("gateway summary carried instead of an empty live list",
      (g.get("gateway") or {}).get("gateway_serial") == "C83DD4300D5B",
      f"({g.get('gateway')})")
check("gateway page reports the device count",
      (g.get("gateway") or {}).get("device_count") == len(DEVICES))
check("resolution.matched is False for the hub",
      (g.get("resolution") or {}).get("matched") is False)
check("gateway page is not mistaken for a broken device page",
      "note" not in g, "-> no 'empty means no data' question to ask")

print("\n== kind: unknown identifier")
u = run({("oblo_living", "unknown_1052")})
check("kind == 'unknown'", u.get("kind") == "unknown", f"({u.get('kind')!r})")
check("note explains why and that it cannot self-heal",
      "cannot be re-resolved" in (u.get("note") or ""), f"({u.get('note')!r})")
check("resolution.ohm_id is None", (u.get("resolution") or {}).get("ohm_id") is None)

print("\n== kind: device absent from the last poll")
n = run({("oblo_living", device_identifier(ENTRY_ID, 9999))})
check("kind == 'device-not-in-last-poll'",
      n.get("kind") == "device-not-in-last-poll", f"({n.get('kind')!r})")
check("note points at the entry-level device list",
      "absent from the" in (n.get("note") or ""), f"({n.get('note')!r})")

print("\n== property shapes")
dict_dev = [{"id": 1052, "name": "Hallway Lights", "class": "ohm",
             "services": [{"service": "outlet0",
                           "props": {"State": "true", "DeviceType": "16"}}]}]
list_dev = [{"id": 1052, "name": "Hallway Lights", "class": "ohm",
             "services": [{"service": "outlet0",
                           "props": [("State", "true"), ("DeviceType", "16")]}]}]
dprops = dg._device_summary(dict_dev)[0]["services"][0]["properties"]
lprops = dg._device_summary(list_dev)[0]["services"][0]["properties"]
check("dict-shaped props report names (live OHM path)",
      dprops == ["DeviceType", "State"], f"({dprops})")
check("list-shaped props report names (REST path)",
      lprops == ["State", "DeviceType"], f"({lprops})")
check("both shapes agree on the same set", sorted(dprops) == sorted(lprops))
check("a service with no props reports [] not None",
      dg._props_names({}) == [], f"({dg._props_names({})!r})")
check("garbage props shape reports []",
      dg._props_names({"props": "oops"}) == [])

print("\n== no values leak in any device-level payload")
for label, payload in (("device", r), ("gateway", g), ("unknown", u), ("absent", n)):
    dumped = json.dumps(payload, default=str)
    # Bare "true"/"false" would match JSON booleans (last_update_success), so
    # look for the property VALUES as serialized strings and the raw float.
    leaks = [v for v in ("SENTINEL_SECRET", "SENTINEL_EMAIL", "SENTINEL_USER",
                         "42.5", '"true"', '"2"') if v in dumped]
    check(f"{label} payload leaks no secret/property value", not leaks, f"({leaks})")

print("\n== entry-level diagnostics keep working")
e = asyncio.run(dg.async_get_config_entry_diagnostics(FakeHass(), FakeEntry()))
check("entry-level lists both devices", len(e.get("devices") or []) == 2)
check("entry-level dict props are named, not empty",
      e["devices"][0]["services"][0]["properties"] == ["MeterReport_W", "State"],
      f"({e['devices'][0]['services'][0]['properties']})")
edump = json.dumps(e, default=str)
check("entry-level leaks nothing",
      not any(v in edump for v in ("SENTINEL_SECRET", "SENTINEL_EMAIL",
                                   "SENTINEL_USER", "42.5", '"true"', '"2"')),
      "-> values absent; only property names")

print(f"\n== {checks} checks, {failures} failures")
sys.exit(1 if failures else 0)
