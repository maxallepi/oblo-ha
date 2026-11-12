"""Platform wiring test: does async_setup_entry actually create entities?

Regression test for the v0.2.3 defect where PLATFORMS forwarded to
custom_components/oblo_living/{sensor,binary_sensor}.py that did not exist
and no platform had an async_setup_entry -- setup died with ModuleNotFoundError
and Andrew saw nothing in HA.

Run: python3 platform_setup_test.py
"""
import asyncio
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))  # repo root (for package import)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401  (installs the homeassistant stubs on import)

from custom_components.oblo_living.const import DOMAIN  # noqa: E402

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + (f"  <- {detail}" if detail and not cond else ""))


# --- fake coordinator + hass -------------------------------------------------
class FakeCoordinator:
    def __init__(self, devices):
        self.data = devices
        self.devices = devices


class FakeEntry:
    entry_id = "test_entry"


class FakeHass:
    def __init__(self):
        self.data = {DOMAIN: {"test_entry": {}}}


# Motion sensor with temp + battery; contact sensor with contact
DEVICES = [
    {   # Z-Wave motion sensor with temperature + battery
        "id": "dev-motion-1",
        "name": "Hall Motion",
        "class": "pir",
        "services": [
            {"service": "pir0", "props": [("Motion", "1")]},
            {"service": "temperature0", "props": [("Temperature", "21.5")]},
            {"service": "battery0", "props": [("Level", "84")]},
        ],
    },
    {   # contact sensor
        "id": "dev-contact-1",
        "name": "Front Door",
        "class": "sensor",
        "services": [
            {"service": "ContactService", "props": [("Contact", "0")]},
        ],
    },
]


def run_platform(module_name, devices):
    """Import the platform module the way HA does, run its setup, return entities."""
    mod = importlib.import_module(f"custom_components.oblo_living.{module_name}")
    hass = FakeHass()
    coord = FakeCoordinator(list(devices))
    hass.data[DOMAIN]["test_entry"]["coordinator"] = coord

    added = []

    def add_entities(ents):
        # HA's async_add_entities is a plain callback, not a coroutine
        added.extend(ents)

    asyncio.run(mod.async_setup_entry(hass, FakeEntry(), add_entities))
    return added, coord


def main():
    # 1. every platform named in __init__.PLATFORMS must be an importable module
    init = importlib.import_module("custom_components.oblo_living")
    check("PLATFORMS is non-empty", len(init.PLATFORMS) > 0, str(init.PLATFORMS))
    for p in init.PLATFORMS:
        try:
            importlib.import_module(f"custom_components.oblo_living.{p}")
            check(f"platform module '{p}' imports", True)
        except Exception as e:
            check(f"platform module '{p}' imports", False, repr(e))
        mod = sys.modules.get(f"custom_components.oblo_living.{p}")
        check(f"platform '{p}' has async_setup_entry", hasattr(mod, "async_setup_entry"))

    # 2. sensor platform builds the right entities (temp + battery from motion device)
    sensors, coord = run_platform("sensor", DEVICES)
    uids = sorted(s._attr_unique_id for s in sensors)
    check("sensor count == 2 (temperature + battery)", len(sensors) == 2, str(uids))
    check("sensor uids exact", uids == [
        "oblo_dev-motion-1_battery0",
        "oblo_dev-motion-1_temperature0",
    ])

    temp = [s for s in sensors if s._attr_unique_id == "oblo_dev-motion-1_temperature0"][0]
    check("temperature device_class", temp._attr_device_class == "temperature", str(temp._attr_device_class))
    check("temperature unit", temp._attr_native_unit_of_measurement == "°C", str(temp._attr_native_unit_of_measurement))
    check("temperature value 21.5", temp.native_value == 21.5, str(temp.native_value))

    batt = [s for s in sensors if s._attr_unique_id == "oblo_dev-motion-1_battery0"][0]
    check("battery value 84", batt.native_value == 84.0, str(batt.native_value))

    # 3. values follow coordinator.data (no restart needed)
    coord.data = [{
        "id": "dev-motion-1", "name": "Hall Motion", "class": "pir",
        "services": [{"service": "temperature0", "props": [("Temperature", "19.0")]}],
    }]
    check("temperature refreshes with coordinator.data", temp.native_value == 19.0, str(temp.native_value))

    # device missing from new payload -> None, not a crash
    coord.data = []
    check("missing device yields None", temp.native_value is None, str(temp.native_value))

    # 4. binary sensor platform (pir0 + ContactService)
    bins, _ = run_platform("binary_sensor", DEVICES)
    buids = sorted(b._attr_unique_id for b in bins)
    check("binary_sensor count == 2", len(bins) == 2, str(buids))
    check("binary_sensor uids exact", buids == [
        "oblo_dev-contact-1_ContactService",
        "oblo_dev-motion-1_pir0",
    ])
    motion = [b for b in bins if b._attr_unique_id == "oblo_dev-motion-1_pir0"][0]
    door = [b for b in bins if b._attr_unique_id == "oblo_dev-contact-1_ContactService"][0]
    check("motion device_class", motion._attr_device_class == "motion", str(motion._attr_device_class))
    check("motion is_on True", motion.is_on is True, str(motion.is_on))
    check("door device_class", door._attr_device_class == "door", str(door._attr_device_class))
    check("door is_on False", door.is_on is False, str(door.is_on))

    # 5. no device_info-less name collisions: names are prefixed with device name
    check("entity names device-prefixed",
          motion._attr_name == "Hall Motion", str(motion._attr_name))
    check("entity name does not duplicate the device word",
          "Motion Motion" not in motion._attr_name, str(motion._attr_name))

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())