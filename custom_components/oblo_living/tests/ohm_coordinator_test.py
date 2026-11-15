"""Coordinator must merge OHM (MQTT) devices and prefer them over REST stubs.

REST only ever returns the gateway object with no services; OHM object_list is
the real inventory. This test locks the wiring: attaching an OHM client makes
entities appear, and an OHM failure must not break the REST poll.

Run: python3 ohm_coordinator_test.py
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
    ObloDataCoordinator, normalise_ohm_objects,
)
from custom_components.oblo_living.cloud_api import ObloRateLimitError  # noqa: E402

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + (f"  <- {detail}" if detail and not cond else ""))


class FakeApi:
    """REST side: returns one empty gateway object, like the live cloud."""

    def __init__(self):
        self.rate_limited = False

    async def async_login(self):
        return "sid"

    async def async_list_gateways(self):
        if self.rate_limited:
            raise ObloRateLimitError("Too many failed requests! Blocked.")
        return [{"id": "gw1", "cached": 1}]

    async def async_gateway_devices(self):
        return [{"id": "gw1", "cached": 1}]


OHM_OBJECTS = [{
    "id": "abc123",
    "device_name": "Living Room Motion",
    "service_list": [
        {"name": "PirService", "property_list": [{"name": "State", "value": "1"}]},
        {"name": "TemperatureService", "property_list": [{"name": "Value", "value": "21.5"}]},
        {"name": "BatteryService", "property_list": [{"name": "Level", "value": "85"}]},
    ],
}, {
    "id": "def456",
    "device_name": "Kitchen Outlet",
    "service_list": [
        {"name": "OutletService", "property_list": [{"name": "State", "value": "0"}]},
    ],
}]


class FakeOhm:
    def __init__(self, objects=None, error=None):
        self.objects = objects if objects is not None else OHM_OBJECTS
        self.error = error
        self.calls = 0

    async def async_object_list(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.objects


async def main():
    # --- no OHM client: REST-only behaviour unchanged -------------------
    api = FakeApi()
    coord = ObloDataCoordinator(None, api)  # type: ignore[arg-type]
    devices = await coord._async_fetch()
    check("REST only -> 1 device (gateway phantom)", len(devices) == 1, f"got {len(devices)}")

    # --- OHM attached: real devices merged in ---------------------------
    api2 = FakeApi()
    coord2 = ObloDataCoordinator(None, api2)  # type: ignore[arg-type]
    ohm = FakeOhm()
    coord2.set_ohm_client(ohm)
    devices2 = await coord2._async_fetch()
    check("OHM attached -> ohm client called", ohm.calls == 1, f"calls={ohm.calls}")
    ids = {d["id"] for d in devices2}
    check("OHM devices present", {"abc123", "def456"} <= ids, f"ids={ids}")
    check("gateway kept (no services, lowest weight)", "gw1" in ids, f"ids={ids}")

    from custom_components.oblo_living.entity_map import build_entities
    ents = build_entities(devices2)
    uids = {e["unique_id"] for e in ents}
    check("entities built from OHM data", len(ents) >= 4, f"n={len(ents)}")
    for svc in ("PirService", "TemperatureService", "BatteryService", "OutletService"):
        check(f"entity for {svc}", any(svc in u for u in uids), f"uids={uids}")

    # --- OHM failure must not break the poll ----------------------------
    api3 = FakeApi()
    coord3 = ObloDataCoordinator(None, api3)  # type: ignore[arg-type]
    coord3.set_ohm_client(FakeOhm(error=RuntimeError("mqtt timeout")))
    devices3 = await coord3._async_fetch()
    check("OHM failure -> REST data still returned", len(devices3) == 1, f"got {len(devices3)}")

    # --- OHM rate limit must propagate (no silent retry loop) -----------
    api4 = FakeApi()
    coord4 = ObloDataCoordinator(None, api4)  # type: ignore[arg-type]
    coord4.set_ohm_client(FakeOhm(error=ObloRateLimitError("blocked")))
    try:
        await coord4._async_fetch()
        check("OHM rate limit propagates", False, "no exception raised")
    except ObloRateLimitError:
        check("OHM rate limit propagates", True)

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
