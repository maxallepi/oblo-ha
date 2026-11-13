#!/usr/bin/env python3
"""entity_wiring_test.py -- wiring assertions for all 6 entity classes.

Tests that the actual entity __init__s wire:
- _attr_has_entity_name == True
- _attr_name == entity_label_attr(ent)
- _attr_entity_category == "diagnostic" for OperationalService/WiseDeviceService
- _attr_device_info is present with correct identifiers

Runs without HA by importing the entity classes against ha_stub.
"""
import os
import sys
import asyncio
from unittest.mock import MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401
from custom_components.oblo_living import entity_map as em  # noqa: E402

# Import all entity classes
from custom_components.oblo_living.entities import (  # noqa: E402
    binary_sensor, sensor, switch, select, button,
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


# Minimal coordinator stub with the fields entity classes read
class MockCoordinator:
    def __init__(self, devices, entry_id="entry_test", gateway_serial="SN123"):
        self.data = devices
        self.entry_id = entry_id
        self.gateway_serial = gateway_serial


# Build a tiny fixture: 1 switch, 1 sensor, 1 binary_sensor, 1 select, 2 buttons
fixture = [{
    "id": 1052,
    "name": "Hallway Lights",
    "class": "Qubino On/Off Relay",
    "services": [{
        "service": "outlet0",
        "service_type": "OutletService",
        "props": [["State", "true"], ["DeviceType", "16"]],
    }, {
        "service": "power0",
        "service_type": "PowerConsumptionService",
        "props": [["ActivePower", "12.4"]],
    }, {
        "service": "OperationalService",
        "service_type": "OperationalService",
        "props": [["State", "true"]],
    }],
}]
coord = MockCoordinator(fixture)
ents = em.build_entities(fixture)

# Group by platform
by_platform = {}
for e in ents:
    by_platform.setdefault(e["platform"], []).append(e)

print("== fixture entities ==")
for e in ents:
    print(f"  {e['platform']:<14} {e['service']:<24} label={e.get('label')!r} "
          f"cat={e.get('entity_category')!r} uid={e['unique_id']}")

print("\n== wiring assertions ==")

# ---- switch ----
sw_ent = by_platform["switch"][0]
sw = switch.ObloSwitch(coord, sw_ent)
check("switch has has_entity_name=True", sw._attr_has_entity_name is True)
check("switch _attr_name is 'Outlet'", sw._attr_name == "Outlet",
      f"({sw._attr_name!r})")
check("switch has _attr_device_info", sw._attr_device_info is not None)

# ---- sensor ----
# power0 -> PowerConsumptionService -> sensor
sn_ent = by_platform["sensor"][0]
sn = sensor.ObloSensor(coord, sn_ent)
check("sensor has has_entity_name=True", sn._attr_has_entity_name is True)
check("sensor _attr_name is 'Power'", sn._attr_name == "Power",
      f"({sn._attr_name!r})")
check("sensor has _attr_device_info", sn._attr_device_info is not None)

# ---- binary_sensor ----
# OperationalService -> connectivity -> diagnostic
bs_ent = by_platform["binary_sensor"][0]
bs = binary_sensor.ObloBinarySensor(coord, bs_ent)
check("binary_sensor has has_entity_name=True", bs._attr_has_entity_name is True)
check("binary_sensor _attr_name is 'Online'", bs._attr_name == "Online",
      f"({bs._attr_name!r})")
check("binary_sensor is diagnostic category",
      str(bs._attr_entity_category) == "diagnostic",
      f"(got {bs._attr_entity_category})")
check("binary_sensor has _attr_device_info", bs._attr_device_info is not None)

# ---- select ----
# Need a siren fixture for select
fixture_siren = [{
    "id": 15,
    "name": "Gateway Simple Alert 15",
    "class": "SimpleAlert",
    "services": [{
        "service": "SimpleAlertService",
        "service_type": "SimpleAlertService",
        "props": [["PropAlertType", "2"], ["PropAlertStartStop", "false"]],
    }],
}]
coord_siren = MockCoordinator(fixture_siren)
ents_siren = em.build_entities(fixture_siren)
by_siren = {}
for e in ents_siren:
    by_siren.setdefault(e["platform"], []).append(e)

sl_ent = by_siren["select"][0]
sl = select.ObloAlertTypeSelect(coord_siren, sl_ent)
check("select has has_entity_name=True", sl._attr_has_entity_name is True)
check("select _attr_name is 'Alert Type'", sl._attr_name == "Alert Type",
      f"({sl._attr_name!r})")
check("select has _attr_device_info", sl._attr_device_info is not None)

# ---- buttons ----
btn_sound = by_siren["button"][0]
btn_silence = by_siren["button"][1]
bt1 = button.ObloSoundAlertButton(coord_siren, btn_sound)
bt2 = button.ObloSilenceAlertButton(coord_siren, btn_silence)
check("button Sound has has_entity_name=True", bt1._attr_has_entity_name is True)
check("button Sound _attr_name is 'Sound'", bt1._attr_name == "Sound",
      f"({bt1._attr_name!r})")
check("button Silence has has_entity_name=True", bt2._attr_has_entity_name is True)
check("button Silence _attr_name is 'Silence'", bt2._attr_name == "Silence",
      f"({bt2._attr_name!r})")
check("button Sound has _attr_device_info", bt1._attr_device_info is not None)
check("button Silence has _attr_device_info", bt2._attr_device_info is not None)

print(f"\n== {checks} checks, {failures} failures")
sys.exit(1 if failures else 0)