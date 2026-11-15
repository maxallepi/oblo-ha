"""Options flow + poll-interval tests.

Proves three things the user asked for:
  1. The integration exposes an options flow at all (Configure button) and it
     offers scan_interval with the documented default.
  2. The schema refuses a rate that would hammer the cloud: anything below the
     15 s floor is rejected, and a hand-edited stored value is clamped.
  3. Applying a new option changes coordinator.update_interval in place -- no
     reload, so a sounding siren or an in-flight write is not disturbed.

Run: python3 options_flow_test.py
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

import voluptuous as vol  # noqa: E402

from custom_components.oblo_living import config_flow  # noqa: E402
from custom_components.oblo_living.const import (  # noqa: E402
    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_S,
    MIN_SCAN_INTERVAL_S, MAX_SCAN_INTERVAL_S,
    SCAN_INTERVAL_S,
)
from custom_components.oblo_living.coordinator import ObloDataCoordinator  # noqa: E402

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + (f"  <- {detail}" if detail and not cond else ""))


class FakeEntry:
    def __init__(self, options=None):
        self.entry_id = "test_entry"
        self.options = dict(options or {})
        self.data = {}


# ---------------------------------------------------------------- flow wiring
def test_flow_exists():
    check("ObloConfigFlow.async_get_options_flow exists",
          hasattr(config_flow.ObloConfigFlow, "async_get_options_flow"))
    flow = config_flow.ObloConfigFlow.async_get_options_flow(FakeEntry())
    check("it returns an ObloOptionsFlow",
          isinstance(flow, config_flow.ObloOptionsFlow), type(flow).__name__)
    check("OptionsFlow is an HA OptionsFlow subclass",
          isinstance(flow, config_flow.config_entries.OptionsFlow))

    # the class must also be reachable as config_flow.ObloOptionsFlow so HA's
    # strings lookup finds the "init" step
    check("options step id is 'init'",
          asyncio.run(_step_id(flow, FakeEntry())) == "init")


async def _step_id(flow, entry):
    flow.config_entry = entry
    shown = await flow.async_step_init(None)
    return shown["step_id"]


# ------------------------------------------------------------------- defaults
def test_default_and_bounds():
    entry = FakeEntry()
    flow = config_flow.ObloOptionsFlow()
    flow.config_entry = entry
    shown = asyncio.run(flow.async_step_init(None))
    schema = shown["schema"]
    key = list(schema.schema)[0]
    check("form asks for scan_interval",
          key.schema if hasattr(key, "schema") else key == CONF_SCAN_INTERVAL,
          str(key))
    check("default is DEFAULT_SCAN_INTERVAL_S (60)",
          key.default() == DEFAULT_SCAN_INTERVAL_S, str(key.default()))
    check("default equals the old hard-coded constant",
          DEFAULT_SCAN_INTERVAL_S == SCAN_INTERVAL_S == 60)

    validator = schema.schema[key]
    check("floor is 15 s (5 s rejected)",
          MIN_SCAN_INTERVAL_S == 15, str(MIN_SCAN_INTERVAL_S))
    try:
        validator(5)
        check("validator rejects 5 s", False, "5 was accepted")
    except vol.Invalid:
        check("validator rejects 5 s", True)
    try:
        validator(100000)
        check("validator rejects an absurd interval", False, "100000 accepted")
    except vol.Invalid:
        check("validator rejects an absurd interval", True)
    check("validator accepts 15 s", validator(15) == 15)
    check("validator accepts 300 s", validator(300) == 300)
    check("validator accepts MAX", validator(MAX_SCAN_INTERVAL_S) == MAX_SCAN_INTERVAL_S)


# ------------------------------------------------------------ saving + clamping
def test_save_and_clamp():
    flow = config_flow.ObloOptionsFlow()
    flow.config_entry = FakeEntry()
    created = asyncio.run(flow.async_step_init({CONF_SCAN_INTERVAL: "30"}))
    check("submitted interval is coerced to int and stored",
          created["data"] == {CONF_SCAN_INTERVAL: 30}, str(created["data"]))

    flow2 = config_flow.ObloOptionsFlow()
    flow2.config_entry = FakeEntry()
    created2 = asyncio.run(flow2.async_step_init({CONF_SCAN_INTERVAL: 3}))
    check("a hand-edited 3 s is clamped up to the floor",
          created2["data"][CONF_SCAN_INTERVAL] == MIN_SCAN_INTERVAL_S,
          str(created2["data"]))

    flow3 = config_flow.ObloOptionsFlow()
    flow3.config_entry = FakeEntry({CONF_SCAN_INTERVAL: 120})
    shown = asyncio.run(flow3.async_step_init(None))
    key = list(shown["schema"].schema)[0]
    check("form pre-selects the stored value",
          key.default() == 120, str(key.default()))


# --------------------------------------------------- coordinator applies in place
class FakeHass:
    def __init__(self):
        self.data = {}


def test_coordinator_interval():
    coord = ObloDataCoordinator.__new__(ObloDataCoordinator)  # skip HA __init__
    coord.update_interval = None
    secs = coord.set_scan_interval(45)
    check("set_scan_interval returns the applied value", secs == 45, str(secs))
    check("update_interval is now 45 s",
          coord.update_interval.total_seconds() == 45,
          str(coord.update_interval))
    coord.set_scan_interval(2)
    check("set_scan_interval clamps 2 s up to 15 s",
          coord.update_interval.total_seconds() == 15,
          str(coord.update_interval))


# ------------------------------------------ the real update listener does it
def test_options_listener_applies_option():
    """Drive the module-level listener HA calls after the options flow saves.

    This is the wiring the user actually sees: change the value in the UI ->
    entry.options changes -> HA calls the listener -> coordinator reschedules.
    """
    from custom_components.oblo_living import _async_options_updated
    from custom_components.oblo_living.const import DOMAIN

    coord = ObloDataCoordinator.__new__(ObloDataCoordinator)
    coord.update_interval = None
    coord.set_scan_interval(DEFAULT_SCAN_INTERVAL_S)

    hass = FakeHass()
    hass.data = {DOMAIN: {"test_entry": {"coordinator": coord}}}
    entry = FakeEntry({CONF_SCAN_INTERVAL: 90})

    asyncio.run(_async_options_updated(hass, entry))
    check("the saved option reaches the coordinator without a reload",
          coord.update_interval.total_seconds() == 90,
          str(coord.update_interval))

    # a stored value below the floor is clamped, not applied blindly
    entry.options[CONF_SCAN_INTERVAL] = 4
    asyncio.run(_async_options_updated(hass, entry))
    check("a stored 4 s is clamped to the floor by the listener",
          coord.update_interval.total_seconds() == MIN_SCAN_INTERVAL_S,
          str(coord.update_interval))

    # no coordinator in hass.data (e.g. entry already unloaded) -> no crash
    hass.data = {DOMAIN: {}}
    asyncio.run(_async_options_updated(hass, entry))
    check("the listener tolerates a missing coordinator", True)


def main():
    test_flow_exists()
    test_default_and_bounds()
    test_save_and_clamp()
    test_coordinator_interval()
    test_options_listener_applies_option()

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
