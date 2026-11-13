"""Dynamic entity discovery: a device enrolled later must not need a restart.

Before v0.2.33 each platform built its entity list once in async_setup_entry.
A device added to the gateway landed in coordinator.data on the next poll or in
the next gateway event, but no entity was ever created for it.

This test drives the real platform modules with a fake coordinator that fires
listeners the way DataUpdateCoordinator does, then adds a device and asserts
the new entity appears -- and that duplicates never do.

Run: python3 dynamic_entities_test.py
"""
import asyncio
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401  (installs the homeassistant stubs on import)

from custom_components.oblo_living.const import DOMAIN  # noqa: E402

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + (f"  <- {detail}" if detail and not cond else ""))


class FakeCoordinator:
    """Just enough of DataUpdateCoordinator for the platform setup contract."""

    def __init__(self, devices):
        self.data = devices
        self.devices = devices
        self._listeners = []

    def async_add_listener(self, cb):
        self._listeners.append(cb)

        def _remove():
            if cb in self._listeners:
                self._listeners.remove(cb)
        return _remove

    def fire_refresh(self):
        """What HA does after a successful update: notify every listener."""
        for cb in list(self._listeners):
            cb()

    def set_devices(self, devices):
        self.data = devices
        self.devices = devices


class FakeEntry:
    entry_id = "test_entry"

    def __init__(self):
        self.unload_callbacks = []

    def async_on_unload(self, fn):
        self.unload_callbacks.append(fn)


class FakeHass:
    def __init__(self):
        self.data = {DOMAIN: {"test_entry": {}}}


MOTION = {
    "id": "dev-motion-1",
    "name": "Hall Motion",
    "class": "pir",
    "services": [{"service": "pir0", "props": [("Motion", "1")]}],
}
DOOR_DEVICE = {
    "id": "dev-contact-1",
    "name": "Front Door",
    "class": "sensor",
    "services": [{"service": "ContactService", "props": [("Contact", "0")]}],
}
TEMP_DEVICE = {
    "id": "dev-temp-1",
    "name": "Porch Thermometer",
    "class": "sensor",
    "services": [{"service": "temperature0", "props": [("Temperature", "18.0")]}],
}


def setup_platform(module_name, devices):
    """Run a platform's async_setup_entry the way HA does; return the recorder."""
    mod = importlib.import_module(f"custom_components.oblo_living.{module_name}")
    hass = FakeHass()
    coord = FakeCoordinator(list(devices))
    entry = FakeEntry()
    hass.data[DOMAIN][entry.entry_id]["coordinator"] = coord

    batches = []

    def add_entities(ents):
        batches.append(list(ents))

    asyncio.run(mod.async_setup_entry(hass, entry, add_entities))
    return batches, coord, entry


def uids(entities):
    return sorted(e._attr_unique_id for e in entities)


def main():
    # -- 1. first setup adds what exists now -------------------------------
    batches, coord, entry = setup_platform("binary_sensor", [MOTION])
    check("first pass adds one entity", len(batches) == 1 and len(batches[0]) == 1,
          str([uids(b) for b in batches]))
    check("first entity is the motion sensor",
          uids(batches[0]) == ["oblo_dev-motion-1_pir0"], str(uids(batches[0])))
    check("a coordinator listener was registered", len(coord._listeners) == 1,
          str(len(coord._listeners)))
    check("a teardown callback was registered with the entry",
          len(entry.unload_callbacks) == 1, str(len(entry.unload_callbacks)))

    # -- 2. nothing new -> no second batch, no duplicates -------------------
    coord.fire_refresh()
    check("refresh with an unchanged tree adds nothing", len(batches) == 1,
          str([uids(b) for b in batches]))

    # -- 3. a device appears later -> entity added without a restart --------
    coord.set_devices([MOTION, DOOR_DEVICE])
    coord.fire_refresh()
    check("a device appearing later is added on refresh", len(batches) == 2,
          str([uids(b) for b in batches]))
    check("the new entity is the one that appeared",
          uids(batches[1]) == ["oblo_dev-contact-1_ContactService"],
          str(uids(batches[1])))

    # -- 4. the same device seen twice is never added twice ----------------
    coord.fire_refresh()
    all_uids = [u for b in batches for u in uids(b)]
    check("no duplicate unique_ids across batches",
          len(all_uids) == len(set(all_uids)), str(all_uids))
    check("exactly two entities after the second device",
          sorted(all_uids) == ["oblo_dev-contact-1_ContactService",
                               "oblo_dev-motion-1_pir0"], str(all_uids))

    # -- 5. sensor platform does the same, and starts empty cleanly --------
    batches_s, coord_s, _ = setup_platform("sensor", [])
    check("empty first pass adds no batch", len(batches_s) == 0, str(batches_s))
    coord_s.set_devices([TEMP_DEVICE])
    coord_s.fire_refresh()
    check("sensor added from an empty start",
          len(batches_s) == 1 and uids(batches_s[0]) == ["oblo_dev-temp-1_temperature0"],
          str([uids(b) for b in batches_s]))

    # -- 6. a writable service that appears later becomes a switch --------
    outlet = {
        "id": "dev-outlet-1", "name": "Hallway Outlet", "class": "ohm",
        "services": [{"service": "outlet0", "props": [("State", "0")],
                      "service_type": "OutletService"}],
    }
    batches_w, coord_w, _ = setup_platform("switch", [])
    check("no switch for a non-outlet device", len(batches_w) == 0, str(batches_w))
    coord_w.set_devices([outlet])
    coord_w.fire_refresh()
    check("writable outlet appearing later becomes a switch",
          len(batches_w) == 1 and len(batches_w[0]) == 1,
          str([uids(b) for b in batches_w]))

    # -- 7. teardown removes the listener -> no leak across reloads -------
    for fn in entry.unload_callbacks:
        fn()
    check("unload callback removes the coordinator listener",
          len(coord._listeners) == 0, str(len(coord._listeners)))

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
