"""Uplink diagnostics must not claim a verdict they cannot support.

v0.2.21 emitted `UPLINK ... advanced=False` and inferred "tunnel dead" from an
unchanged lastTimeStatusUpdated. That field is a *transition* timestamp, only
written on online<->offline, so "unchanged" is the normal steady state -- the
flag was a false negative and the published release note told Andrew to
escalate to OBLO support on the strength of it.

v0.2.23 deletes the gate. These checks fail if a verdict-shaped field ever
comes back, and prove the raw values are still available at DEBUG.
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

TS = "2026-11-06T08:06:01.000Z"

BUF = io.StringIO()
HANDLER = logging.StreamHandler(BUF)
HANDLER.setLevel(logging.DEBUG)
_LOG = logging.getLogger("custom_components.oblo_living.coordinator")
_LOG.addHandler(HANDLER)
_LOG.setLevel(logging.DEBUG)


class Api:
    def __init__(self):
        self.calls = 0

    async def async_login(self):
        pass

    async def async_list_gateways(self):
        self.calls += 1
        return [{
            "_id": "645cd2167740a3ddc4c010a5",
            "serialNumber": "C83DD4300D5B",
            "type": "hg",
            "connectionStatus": "online",
            "lastTimeOnline": TS,
            "lastTimeStatusUpdated": TS,
        }]

    async def async_gateway_devices(self):
        return []

    async def async_close(self):
        pass


def check(desc, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + desc + ("" if ok else f"  <- {detail}"))
    return bool(ok)


async def main():
    checks = []

    # --- the false verdict must not exist in the shipped source ---
    # The comment may name the retired field; no log line may render it.
    src = open(os.path.join(COMP, "coordinator.py")).read()
    checks.append(check("no log line renders an 'advanced' verdict",
                        "advanced=%s" not in src, "advanced=%s still in a format string"))
    checks.append(check("no _last_adv state carried", "_last_adv" not in src))

    # --- raw values still logged, at DEBUG, labelled honestly ---
    co = ObloDataCoordinator(None, Api())
    await co._async_fetch()
    await co._async_fetch()
    lines = [l for l in BUF.getvalue().splitlines() if "UPLINK" in l]
    checks.append(check("two polls log two DEBUG lines", len(lines) == 2, len(lines)))
    checks.append(check("serial present", all("sn=C83DD4300D5B" in l for l in lines), lines[:1]))
    checks.append(check("both timestamps present",
                        all(TS in l for l in lines), lines[:1]))
    checks.append(check("labelled as a transition timestamp, not a heartbeat",
                        all("not a heartbeat" in l for l in lines), lines[:1]))

    # --- and nothing noise-level: steady state must not warn ---
    warned = [l for l in BUF.getvalue().splitlines() if "UPLINK" in l and "WARNING" in l]
    checks.append(check("steady state emits no UPLINK warning", not warned, warned))

    ok = all(checks)
    print(f"\n{sum(checks)}/{len(checks)} checks")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
