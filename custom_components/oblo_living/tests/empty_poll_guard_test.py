"""An empty poll must never be published as an empty house.

Andrew's log showed:

    DIAG SUMMARY: devices=0 total_services=0 mapped=0 entities=0 services=[]

on the first poll after a restart. Every entity flipped to `unknown` -- including
the relay, which has nothing to do with SimpleAlert. That is the false negative
v0.2.23 banned: a failed fetch is indistinguishable from a device that reports
nothing.

The guard: if the freshly merged device list is empty but a cached list exists,
keep the cache and log why. Never publish emptiness as truth. The first-ever
poll has no cache to protect, so it logs loudly and publishes the empty truth.

Run: python3 empty_poll_guard_test.py
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
    ObloDataCoordinator,
)

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}"
          + (f"  <- {detail}" if detail and not cond else ""))


class FakeApi:
    """REST side. `devices` lets a test decide what the REST poll returns.

    `gateways` matters: merge_devices() keeps a phantom object for the gateway
    itself, so a poll only yields zero devices when the gateway list is empty
    too. Andrew's `devices=0` means both sides came back empty.
    """

    def __init__(self, devices=None, gateways=None):
        self.devices = devices if devices is not None else []
        self.gateways = gateways if gateways is not None else [{"id": "gw1"}]

    async def async_login(self):
        return "sid"

    async def async_list_gateways(self):
        return self.gateways

    async def async_gateway_devices(self):
        return self.devices


class FakeOhm:
    """OHM side. `objects` empty models an OHM round-trip that returns nothing."""

    def __init__(self, objects=None):
        self.objects = objects if objects is not None else []

    async def async_object_list(self):
        return self.objects


# Andrew's exact poll shape: both endpoints return the gateway record
# (no `devices` key inside it).
GATEWAY_ONLY = {"id": "gw1", "type": "hg", "device_count": 0, "has_devices": False}


GOOD_DEVICE = {
    "id": "1052",
    "device_name": "Hallway Lights",
    "service_list": [{
        "name": "OutletService",
        "property_list": [{"name": "State", "value": "1"}],
    }],
}


async def main():
    # --- first ever poll, nothing anywhere: publish empty, say so loudly ----
    coord = ObloDataCoordinator(None, FakeApi(gateways=[]),  # type: ignore[arg-type]
                        entry_id="e" * 32)
    coord.set_ohm_client(FakeOhm([]))
    devices = await coord._async_fetch()
    check("first empty poll -> empty list published",
          devices == [], f"got {len(devices)}")
    check("first empty poll -> no phantom cache",
          coord.devices == [], f"got {len(coord.devices)}")

    # --- seed a good poll -------------------------------------------------
    coord2 = ObloDataCoordinator(None, FakeApi([GOOD_DEVICE]),  # type: ignore[arg-type]
                        entry_id="e" * 32)
    coord2.set_ohm_client(FakeOhm([GOOD_DEVICE]))
    seeded = await coord2._async_fetch()
    check("good poll -> device present", len(seeded) >= 1, f"got {len(seeded)}")
    kept_ids = {d["id"] for d in seeded}
    check("good poll -> 1052 in list", "1052" in kept_ids, f"ids={kept_ids}")

    # --- now the OHM round-trip returns nothing and REST carries no devices -
    # This is Andrew's exact failure: a *successful* poll that yields nothing.
    coord2.set_ohm_client(FakeOhm([]))
    coord2.api = FakeApi([], gateways=[])  # type: ignore[assignment]
    after_empty = await coord2._async_fetch()
    after_ids = {d["id"] for d in after_empty}
    check("empty poll after good poll -> cache kept, not []",
          len(after_empty) == len(seeded),
          f"was {len(seeded)}, now {len(after_empty)}")
    check("empty poll after good poll -> 1052 still there",
          "1052" in after_ids, f"ids={after_ids}")
    check("empty poll after good poll -> coordinator.data not blanked",
          len(coord2.devices) == len(seeded),
          f"coordinator.devices={len(coord2.devices)}")

    # --- recovered poll must replace the cache, not merge into it ----------
    coord2.set_ohm_client(FakeOhm([GOOD_DEVICE]))
    coord2.api = FakeApi([GOOD_DEVICE])  # type: ignore[assignment]
    recovered = await coord2._async_fetch()
    recovered_ids = {d["id"] for d in recovered}
    seeded_ids = {d["id"] for d in seeded}
    check("recovered poll -> still one device, no duplication",
          recovered_ids == seeded_ids, f"ids={recovered_ids}")

    # --- a real empty house (user removed everything) is a different case --
    # Nothing can distinguish it from a failed fetch, so the guard keeps the
    # cache and warns. That is the deliberate trade: never blank the UI on a
    # poll we cannot prove. Documented here so nobody "fixes" it later.
    coord3 = ObloDataCoordinator(None, FakeApi([GOOD_DEVICE]),  # type: ignore[arg-type]
                        entry_id="e" * 32)
    coord3.set_ohm_client(FakeOhm([GOOD_DEVICE]))
    await coord3._async_fetch()
    coord3.set_ohm_client(FakeOhm([]))
    coord3.api = FakeApi([], gateways=[])  # type: ignore[assignment]
    held = await coord3._async_fetch()
    check("indistinguishable empty house -> cache held (deliberate)",
          len(held) >= 1, f"got {len(held)}")

    # --- Andrew's exact shape: gateways + details return the gateway record ---
    # /hg/info returns the same gateway object (no device list) on this firmware.
    # diag_shape() reads .get("devices") -> missing -> has_devices=False, device_count=0
    # This is diagnostic noise, NOT a functional signal. The cache must still hold.
    coord4 = ObloDataCoordinator(None, FakeApi([GOOD_DEVICE]),  # type: ignore[arg-type]
                        entry_id="e" * 32)
    coord4.set_ohm_client(FakeOhm([GOOD_DEVICE]))
    await coord4._async_fetch()
    # Now the poll returns gateway-only on both endpoints + OHM empty
    coord4.set_ohm_client(FakeOhm([]))
    coord4.api = FakeApi([], gateways=[GATEWAY_ONLY])  # type: ignore[assignment]
    # Need to monkeypatch async_gateway_devices to return GATEWAY_ONLY too
    async def fake_gw_devices():
        return GATEWAY_ONLY
    coord4.api.async_gateway_devices = fake_gw_devices  # type: ignore[assignment]
    held2 = await coord4._async_fetch()
    check("gateway-only details + OHM empty -> cache kept",
          len(held2) >= 1, f"got {len(held2)}")
    # The diag_shape on this gateway record must report has_devices=False, device_count=0
    from custom_components.oblo_living.coordinator import diag_shape
    shape = diag_shape(GATEWAY_ONLY)
    check("diag_shape reports has_devices=False for gateway record",
          shape["has_devices"] is False)
    check("diag_shape reports device_count=0 for gateway record",
          shape["device_count"] == 0)

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
