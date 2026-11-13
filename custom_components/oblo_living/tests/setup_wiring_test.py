"""Wiring test for async_setup_entry: options -> coordinator, listener registered.

The options flow and the coordinator are each tested elsewhere. What was never
proven is the joint the user actually touches: the stored option reaching the
coordinator at setup, and HA being handed an update listener so a later change
applies without a reload. This drives the real async_setup_entry with the
network layers swapped for fakes.

Run: python3 setup_wiring_test.py
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401  (installs the homeassistant stubs on import)

from custom_components.oblo_living import (  # noqa: E402
    async_setup_entry, _async_options_updated,
)
from custom_components.oblo_living.const import (  # noqa: E402
    DOMAIN, CONF_IDENTITY, CONF_SECRET, CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_S, MIN_SCAN_INTERVAL_S,
)

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + (f"  <- {detail}" if detail and not cond else ""))


class FakeCoordinator:
    """Records how it was constructed and what interval it was told to use."""

    made = []

    def __init__(self, hass, api, scan_interval=60):
        self.hass = hass
        self.api = api
        self.scan_interval = scan_interval
        self.update_interval = __import__("datetime").timedelta(seconds=scan_interval)
        self.devices = []
        self.refreshed = 0
        self.ohm = None
        FakeCoordinator.made.append(self)

    async def async_config_entry_first_refresh(self):
        self.refreshed += 1

    def set_ohm_client(self, client):
        self.ohm = client

    async def async_stop_active_alerts(self):
        return None

    def set_scan_interval(self, seconds):
        secs = max(MIN_SCAN_INTERVAL_S, int(seconds))
        self.update_interval = __import__("datetime").timedelta(seconds=secs)
        self.scan_interval = secs
        return secs


class FakeApi:
    session_id = "sess-1"
    user_id = "user-1"
    username = "user"
    groups = ["admin"]

    def __init__(self, *a, **kw):
        pass


class FakeHub:
    def load_from_normalised(self, devices):
        self.devices = devices


class FakeEntry:
    def __init__(self, options=None):
        self.entry_id = "test_entry"
        self.data = {CONF_IDENTITY: "id", CONF_SECRET: "plaintext"}
        self.options = dict(options or {})
        self.listeners = []
        self.unload = []

    def add_update_listener(self, cb):
        self.listeners.append(cb)
        return lambda: self.listeners.remove(cb)

    def async_on_unload(self, fn):
        self.unload.append(fn)


class FakeConfigEntries:
    def __init__(self):
        self.forwarded = []

    def async_update_entry(self, entry, **kw):
        pass

    async def async_forward_entry_setups(self, entry, platforms):
        self.forwarded.append(list(platforms))

    async def async_unload_platforms(self, entry, platforms):
        return True


class FakeHass:
    def __init__(self):
        self.data = {}
        self.loop = None
        self.config_entries = FakeConfigEntries()


async def _no_ohm(hass, api, coordinator):
    return None


async def _no_services(hass):
    return None


def run_setup(options):
    """Call the real async_setup_entry with the network layers faked out."""
    import custom_components.oblo_living as pkg

    FakeCoordinator.made = []
    orig = (pkg.ObloCloudApi, pkg.ObloDataCoordinator, pkg.ObloHub,
            pkg._async_start_ohm, pkg.async_register_services,
            pkg.async_get_clientsession)
    pkg.ObloCloudApi = FakeApi
    pkg.ObloDataCoordinator = FakeCoordinator
    pkg.ObloHub = FakeHub
    pkg._async_start_ohm = _no_ohm
    pkg.async_register_services = _no_services
    pkg.async_get_clientsession = lambda hass: object()

    hass = FakeHass()
    entry = FakeEntry(options)
    try:
        ok = asyncio.run(async_setup_entry(hass, entry))
    finally:
        (pkg.ObloCloudApi, pkg.ObloDataCoordinator, pkg.ObloHub,
         pkg._async_start_ohm, pkg.async_register_services,
         pkg.async_get_clientsession) = orig
    return ok, hass, entry


def main():
    # -- 1. no options stored -> documented default ------------------------
    ok, hass, entry = run_setup({})
    check("setup succeeds with no stored options", ok is True, str(ok))
    coord = FakeCoordinator.made[0]
    check("coordinator built with the 60 s default",
          coord.scan_interval == DEFAULT_SCAN_INTERVAL_S, str(coord.scan_interval))
    check("coordinator update_interval is 60 s",
          coord.update_interval.total_seconds() == 60,
          str(coord.update_interval))

    # -- 2. an update listener is registered (the Configure button path) ---
    check("HA was handed exactly one update listener", len(entry.listeners) == 1,
          str(entry.listeners))
    check("the listener is the real options handler",
          entry.listeners and entry.listeners[0] is _async_options_updated,
          str(entry.listeners))
    check("its removal is registered for teardown", len(entry.unload) == 1,
          str(len(entry.unload)))
    check("all five platforms were forwarded",
          hass.config_entries.forwarded == [["binary_sensor", "sensor",
                                             "switch", "select", "button"]],
          str(hass.config_entries.forwarded))
    check("the coordinator is stashed where platforms read it",
          hass.data.get(DOMAIN, {}).get("test_entry", {}).get("coordinator") is coord)

    # -- 3. a stored option is honoured at setup, not overridden -----------
    ok2, hass2, entry2 = run_setup({CONF_SCAN_INTERVAL: 120})
    check("setup still succeeds with a stored option", ok2 is True)
    coord2 = FakeCoordinator.made[0]
    check("coordinator built with the stored 120 s",
          coord2.scan_interval == 120, str(coord2.scan_interval))

    # -- 4. end to end: saving in the UI changes the live interval ---------
    asyncio.run(entry2.listeners[0](hass2, entry2))
    check("re-running the listener leaves 120 s in place",
          coord2.update_interval.total_seconds() == 120,
          str(coord2.update_interval))
    entry2.options[CONF_SCAN_INTERVAL] = 30
    asyncio.run(entry2.listeners[0](hass2, entry2))
    check("changing the option to 30 s reschedules the poll in place",
          coord2.update_interval.total_seconds() == 30,
          str(coord2.update_interval))
    check("no reload was needed: same coordinator object", 
          hass2.data[DOMAIN]["test_entry"]["coordinator"] is coord2)
    check("entities were forwarded only once (no re-forward on option change)",
          len(hass2.config_entries.forwarded) == 1,
          str(hass2.config_entries.forwarded))

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())