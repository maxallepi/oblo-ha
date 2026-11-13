"""Negative control: an evt-patched property must survive the next poll.

The gateway does not persist live state to disk (outlet0/State and both
SimpleAlert props have no rows in ohm.db), so OHM's property_list may legitimately
omit them. If a poll rebuilds the device tree from that payload, an evt-patched
value is discarded and the entity falls back to unknown mid-flight.

This test fails on any version whose _async_fetch replaces the tree without
re-applying the evt overlay. It is the regression guard for that defect.
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
from custom_components.oblo_living.entity_map import read_prop_strict  # noqa: E402

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}"
          + (f"  <- {detail}" if detail and not cond else ""))


OHM_PAYLOAD = [{
    "id": "1052",
    "device_name": "Hallway Lights",
    "service_list": [{"name": "outlet0", "type": "OutletService",
                      "property_list": [{"name": "DeviceType", "value": "16"}]}],
}]


class FakeApi:
    def __init__(self):
        self.calls = 0

    async def async_list_gateways(self):
        return [{"serialNumber": "C83DD4300D5B"}]

    async def async_gateway_devices(self):
        return []


class FakeOhm:
    def __init__(self, payload=None):
        self.payload = payload if payload is not None else OHM_PAYLOAD
        self.calls = 0

    async def async_object_list(self):
        self.calls += 1
        return self.payload


def mk(payload=None):
    hass = type("H", (), {"loop": None})()
    c = ObloDataCoordinator(hass, api=FakeApi())
    c.ohm = FakeOhm(payload)
    c.notified = []
    c.async_set_updated_data = lambda d: c.notified.append(1)
    return c


def state_of(c):
    for d in c.devices or []:
        if str(d.get("id")) != "1052":
            continue
        for s in d.get("services") or []:
            if s.get("service") == "outlet0":
                return read_prop_strict(s, "State")
    return "device-missing"


async def scenario_poll_keeps_evt():
    """evt says ON, poll payload omits State -> ON must survive."""
    c = mk()
    await c._async_fetch()                       # baseline: unknown (no State)
    c.data = list(c.devices)                     # what _async_update_data does
    base = state_of(c)

    c.handle_event({"name": "device_property_changed",
                    "params": {"deviceId": "1052", "serviceName": "outlet0",
                               "propertyName": "State", "value": "true"}})
    after_evt = state_of(c)

    await c._async_fetch()                       # the 60 s poll
    c.data = list(c.devices)
    after_poll = state_of(c)
    return base, after_evt, after_poll


async def scenario_poll_wins_when_present():
    """If the poll DOES carry the property, its fresh value must win."""
    c = mk()
    await c._async_fetch()
    c.data = list(c.devices)
    c.handle_event({"name": "device_property_changed",
                    "params": {"deviceId": "1052", "serviceName": "outlet0",
                               "propertyName": "State", "value": "true"}})
    # gateway now reports false in the poll payload
    payload = [{
        "id": "1052", "device_name": "Hallway Lights",
        "service_list": [{"name": "outlet0", "type": "OutletService",
                          "property_list": [{"name": "DeviceType", "value": "16"},
                                            {"name": "State", "value": "false"}]}],
    }]
    c.ohm = FakeOhm(payload)
    await c._async_fetch()
    c.data = list(c.devices)
    return state_of(c)


async def scenario_stale_after_reconnect():
    """After an OHM reconnect the evt overlay must be dropped: state we last
    saw before the gap is not evidence of state now."""
    c = mk()
    await c._async_fetch()
    c.data = list(c.devices)
    c.handle_event({"name": "device_property_changed",
                    "params": {"deviceId": "1052", "serviceName": "outlet0",
                               "propertyName": "State", "value": "true"}})
    # disconnect + reconnect: fresh client attached
    c.set_ohm_client(FakeOhm())
    await c._async_fetch()
    c.data = list(c.devices)
    return state_of(c)


async def scenario_confirmed_write_survives_poll():
    """A confirmed write uses _poke_device (gateway read-back) to flip UI
    instantly. That value must also survive the next poll, because the poll
    may omit the property just like it omits evt-sourced ones."""
    c = mk()
    await c._async_fetch()
    c.data = list(c.devices)
    # simulate the confirmed write path: _poke_device with read-back value
    c._poke_device("1052", "outlet0", "State", "true")
    after_poke = state_of(c)
    # next poll omits State
    await c._async_fetch()
    c.data = list(c.devices)
    after_poll = state_of(c)
    return after_poke, after_poll


async def scenario_fresher_poll_replaces_overlay():
    """A poll value must REPLACE the remembered one, not just win for a moment.

    evt true -> poll says false (gateway changed) -> next poll omits the
    property. The remembered value must be the poll's false, otherwise the
    overlay resurrects a stale true -- worse than unknown, because it is wrong.
    """
    c = mk()
    await c._async_fetch()
    c.data = list(c.devices)
    c.handle_event({"name": "device_property_changed",
                    "params": {"deviceId": "1052", "serviceName": "outlet0",
                               "propertyName": "State", "value": "true"}})
    with_false = [{
        "id": "1052", "device_name": "Hallway Lights",
        "service_list": [{"name": "outlet0", "type": "OutletService",
                          "property_list": [{"name": "DeviceType", "value": "16"},
                                            {"name": "State", "value": "false"}]}],
    }]
    c.ohm = FakeOhm(with_false)
    await c._async_fetch()
    c.data = list(c.devices)
    seen_false = state_of(c)
    c.ohm = FakeOhm()          # back to a payload that omits State
    await c._async_fetch()
    c.data = list(c.devices)
    return seen_false, state_of(c)


base, after_evt, after_poll = asyncio.run(scenario_poll_keeps_evt())
check("baseline before evt is unknown", base is None, f"base={base!r}")
check("evt sets state to 'true'", after_evt == "true", f"after_evt={after_evt!r}")
check("state survives the next poll", after_poll == "true",
      f"after_poll={after_poll!r} (poll discarded the evt value)")

poll_wins = asyncio.run(scenario_poll_wins_when_present())
check("a property present in the poll overrides the evt value",
      poll_wins == "false", f"got {poll_wins!r} - stale evt beat fresh poll")

stale = asyncio.run(scenario_stale_after_reconnect())
check("reconnect drops stale evt state", stale is None,
      f"got {stale!r} - pre-gap value presented as current")

after_poke, after_poll2 = asyncio.run(scenario_confirmed_write_survives_poll())
check("confirmed write flips state", after_poke == "true", f"after_poke={after_poke!r}")
check("confirmed write survives next poll", after_poll2 == "true",
      f"after_poll={after_poll2!r} (poll discarded the confirmed read-back)")

seen_false, after_omit = asyncio.run(scenario_fresher_poll_replaces_overlay())
check("a fresher poll value is presented", seen_false == "false",
      f"got {seen_false!r}")
check("a fresher poll value replaces the remembered one (no resurrection)",
      after_omit == "false",
      f"got {after_omit!r} - a poll that omitted State resurrected the old evt value")

print("\n--- SUMMARY ---")
total = len(CHECKS)
passed = sum(1 for _, ok, _ in CHECKS if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    for label, ok, _ in CHECKS:
        if not ok:
            print(f"  FAIL: {label}")
    sys.exit(1)
print("All checks passed.")
