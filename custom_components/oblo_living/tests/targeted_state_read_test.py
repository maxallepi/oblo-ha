"""Tests for the one-shot targeted State read on relays.

The poll may omit `State` for a relay. After a restart nothing is remembered and
nothing is polled, so the card has no truth source until someone toggles it.
These tests cover the recovery path: one `get_service_property_value` per
switch service per OHM session, and nothing invented when the read is empty.

Covers:
- missing State + read returns a value -> value lands in the poll result
- missing State + read returns None -> nothing invented (stays unknown)
- read raises -> poll is not broken
- runs once per session, not on every poll
- reconnect resets the flag so a new session re-resolves
- service that already has State spends no round trip
- the switch entity then reports the resolved value
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

from custom_components.oblo_living.coordinator import (  # noqa: E402
    ObloDataCoordinator, _prop_value,
)
from custom_components.oblo_living.entities.switch import ObloSwitch  # noqa: E402

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}"
          + (f"  <- {detail}" if detail and not cond else ""))


class FakeOhm:
    """Only implements the targeted read -- enough for the resolver."""

    def __init__(self, value="1", raises=False):
        self.value = value
        self.raises = raises
        self.reads = []

    async def async_get_property(self, service_id, service_name, property_name,
                                 service_type=None, timeout=None):
        self.reads.append((service_id, service_name, property_name))
        if self.raises:
            raise RuntimeError("broker said no")
        return self.value


def relay_without_state(did="1052", svc="OutletService"):
    return {
        "id": did,
        "name": "Hallway Lights Outlet",
        "services": [{"service": svc, "service_type": svc,
                      "props": [("DeviceType", "16")]}],
    }


def make_coordinator(devices, ohm):
    hass = type("H", (), {"loop": asyncio.get_event_loop()})()
    coord = ObloDataCoordinator(hass, api=None, entry_id="test_entry")
    coord.devices = devices
    coord.data = devices
    coord.ohm = ohm
    return coord


async def main():
    # 1) read returns a value -> it lands in the poll result
    dev = relay_without_state()
    ohm = FakeOhm(value="1")
    coord = make_coordinator([dev], ohm)
    n = await coord._resolve_missing_states([dev])
    check("value returned -> resolved count 1", n == 1, f"n={n}")
    check("State written into the poll result",
          _prop_value(dev["services"][0]["props"], "State") == "1",
          str(dev["services"][0]["props"]))
    check("read used the OHM service id 'outlet0'",
          ohm.reads == [("1052", "outlet0", "State")], str(ohm.reads))

    # 2) read returns None -> nothing invented
    dev = relay_without_state()
    coord = make_coordinator([dev], FakeOhm(value=None))
    n = await coord._resolve_missing_states([dev])
    check("nothing returned -> resolved 0", n == 0)
    check("nothing returned -> State still absent",
          _prop_value(dev["services"][0]["props"], "State") is None)

    # 3) read raises -> poll not broken, nothing invented
    dev = relay_without_state()
    coord = make_coordinator([dev], FakeOhm(raises=True))
    n = await coord._resolve_missing_states([dev])
    check("read raises -> resolved 0, no exception", n == 0)
    check("read raises -> State still absent",
          _prop_value(dev["services"][0]["props"], "State") is None)

    # 4) runs once per session
    dev1 = relay_without_state()
    ohm = FakeOhm(value="1")
    coord = make_coordinator([dev1], ohm)
    await coord._resolve_missing_states([dev1])
    dev2 = relay_without_state(did="2001")
    await coord._resolve_missing_states([dev2])
    check("second poll spends no round trip", len(ohm.reads) == 1, str(ohm.reads))
    check("second device not resolved", _prop_value(
        dev2["services"][0]["props"], "State") is None)

    # 5) reconnect resets the flag
    ohm2 = FakeOhm(value="0")
    coord.set_ohm_client(ohm2)   # different object -> new session
    dev3 = relay_without_state(did="3001")
    await coord._resolve_missing_states([dev3])
    check("reconnect -> resolves again", len(ohm2.reads) == 1, str(ohm2.reads))

    # 6) service that already has State spends no round trip
    dev = relay_without_state()
    dev["services"][0]["props"] = [("DeviceType", "16"), ("State", "0")]
    ohm = FakeOhm(value="1")
    coord = make_coordinator([dev], ohm)
    n = await coord._resolve_missing_states([dev])
    check("poll already has State -> no read", len(ohm.reads) == 0 and n == 0)
    check("poll value not overwritten",
          _prop_value(dev["services"][0]["props"], "State") == "0")

    # 7) the switch entity reports the resolved value
    dev = relay_without_state()
    ohm = FakeOhm(value="1")
    coord = make_coordinator([dev], ohm)
    await coord._resolve_missing_states([dev])
    ent = {"device_id": "1052", "service": "OutletService",
           "service_type": "OutletService", "value_prop": "State",
           "unique_id": "oblo_1052_OutletService_switch_State"}
    sw = ObloSwitch(coord, ent)
    check("switch is_on True from the targeted read", sw.is_on is True)

    # 8) BasicService uses basic0
    dev = relay_without_state(did="1053", svc="BasicService")
    ohm = FakeOhm(value="1")
    coord = make_coordinator([dev], ohm)
    await coord._resolve_missing_states([dev])
    check("BasicService read uses 'basic0'",
          ohm.reads == [("1053", "basic0", "State")], str(ohm.reads))

    # 9) async_refresh_state: check_device then one read
    class FakeRefresh:
        def __init__(self, value="1", check_raises=False, read_raises=False):
            self.value = value
            self.check_raises = check_raises
            self.read_raises = read_raises
            self.checks = []
            self.reads = []

        async def async_check_device(self, bus_id, device_id, timeout=None):
            if self.check_raises:
                raise RuntimeError("no such verb")
            self.checks.append((bus_id, device_id))
            return {"code": 0}

        async def async_get_property(self, service_id, service_name, property_name,
                                     service_type=None, timeout=None):
            if self.read_raises:
                raise RuntimeError("read boom")
            self.reads.append((service_id, service_name, property_name))
            return self.value

    ohm = FakeRefresh(value="1")
    coord = make_coordinator([relay_without_state()], ohm)
    val = await coord.async_refresh_state("1052", "OutletService", "State", settle=0)
    check("refresh -> returns the read value", val == "1", repr(val))
    check("refresh -> check_device sent first", ohm.checks == [(None, "1052")], str(ohm.checks))
    check("refresh -> read used outlet0",
          ohm.reads == [("1052", "outlet0", "State")], str(ohm.reads))

    # check_device unsupported -> read still attempted, no raise
    ohm = FakeRefresh(value="0", check_raises=True)
    coord = make_coordinator([relay_without_state()], ohm)
    val = await coord.async_refresh_state("1052", "OutletService", "State", settle=0)
    check("check_device fails -> read still attempted, value returned", val == "0", repr(val))

    # read returns nothing -> None, nothing invented
    ohm = FakeRefresh(value=None)
    coord = make_coordinator([relay_without_state()], ohm)
    val = await coord.async_refresh_state("1052", "OutletService", "State", settle=0)
    check("refresh read empty -> None", val is None, repr(val))

    # read raises -> None, no propagation
    ohm = FakeRefresh(read_raises=True)
    coord = make_coordinator([relay_without_state()], ohm)
    val = await coord.async_refresh_state("1052", "OutletService", "State", settle=0)
    check("refresh read raises -> None, no exception", val is None)

    # no ohm client -> None
    coord = make_coordinator([relay_without_state()], None)
    coord.ohm = None
    val = await coord.async_refresh_state("1052", "OutletService", "State", settle=0)
    check("no ohm client -> None", val is None)

    print(f"\n=== SUMMARY: {sum(c[1] for c in CHECKS)}/{len(CHECKS)} checks passed ===")
    for label, ok, detail in CHECKS:
        if not ok:
            print(f"  FAIL: {label}  <- {detail}")
    if all(c[1] for c in CHECKS):
        print("All checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
