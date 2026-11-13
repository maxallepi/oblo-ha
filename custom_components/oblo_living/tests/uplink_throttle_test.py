"""UPLINK gate must not flood the log, but must never miss a transition.

@devops' gate logs one line per poll. At SCAN_INTERVAL_S=60 that is 1440
WARNING lines/day while the gateway stays dead, which buries real warnings.
The throttle keeps every *state change* (first -> False -> True) and adds a
10-minute heartbeat, so the grep gate still works and the log stays readable.
"""
import asyncio
import io
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COMP = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMP))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import ha_stub  # noqa: F401

from custom_components.oblo_living.coordinator import ObloDataCoordinator

BUF = io.StringIO()
HANDLER = logging.StreamHandler(BUF)
HANDLER.setLevel(logging.WARNING)
_LOG = logging.getLogger("custom_components.oblo_living.coordinator")
_LOG.addHandler(HANDLER)
_LOG.setLevel(logging.WARNING)

TS1 = "2026-11-06T08:06:01.000Z"
TS2 = "2026-11-13T09:12:44.000Z"


class Api:
    """Serves one gateway whose lastTimeStatusUpdated follows a script."""

    def __init__(self, stamps):
        self.stamps = list(stamps)

    async def async_login(self):
        pass

    async def async_list_gateways(self):
        ts = self.stamps.pop(0) if len(self.stamps) > 1 else self.stamps[0]
        return [{
            "_id": "645cd2167740a3ddc4c010a5",
            "serialNumber": "C83DD4300D5B",
            "type": "hg",
            "connectionStatus": "online",
            "lastTimeOnline": ts,
            "lastTimeStatusUpdated": ts,
        }]

    async def async_gateway_devices(self):
        return []

    async def async_close(self):
        pass


def lines():
    return [l for l in BUF.getvalue().splitlines() if "UPLINK" in l]


def check(desc, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + desc + ("" if ok else f"  <- {detail}"))
    return bool(ok)


async def main():
    checks = []

    # --- same timestamp 6 polls: only the first should log (state change) ---
    BUF.truncate(0)
    BUF.seek(0)
    co = ObloDataCoordinator(None, Api([TS1] * 6))
    for _ in range(6):
        await co._async_fetch()
    got = lines()
    checks.append(check("6 identical polls log fewer lines than polls", len(got) < 6, len(got)))
    checks.append(check("first poll still logged", len(got) >= 1, len(got)))
    checks.append(check("first line is advanced=first", "advanced=first" in got[0], got[0] if got else ""))
    checks.append(check("baseline timestamp printed verbatim",
                        TS1 in got[0], got[0] if got else ""))

    # --- transition to a fresh timestamp must log immediately (the gate) ---
    co.api.stamps = [TS2]
    await co._async_fetch()
    got = lines()
    checks.append(check("advance logged immediately on the next poll",
                        got[-1].startswith("UPLINK") and "advanced=True" in got[-1],
                        got[-1] if got else ""))
    checks.append(check("advance line carries the new timestamp",
                        TS2 in got[-1], got[-1] if got else ""))

    # --- and the greppable count is non-zero, which is what Andrew runs ---
    count = sum(1 for l in got if "advanced=True" in l)
    checks.append(check("grep -c 'UPLINK.*advanced=True' is non-zero", count >= 1, count))

    ok = all(checks)
    print(f"\n{sum(checks)}/{len(checks)} checks")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
