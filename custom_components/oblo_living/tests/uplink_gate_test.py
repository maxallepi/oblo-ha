
"""UPLINK gate: log must expose advanced=first -> False -> False, then True."""
import asyncio, os, sys, logging, io

HERE = os.path.dirname(os.path.abspath(__file__))
COMP = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMP))
sys.path.insert(0, HERE); sys.path.insert(0, ROOT)
import ha_stub  # noqa

from custom_components.oblo_living.coordinator import ObloDataCoordinator

buf = io.StringIO()
h = logging.StreamHandler(buf)
h.setLevel(logging.WARNING)
logging.getLogger("custom_components.oblo_living.coordinator").addHandler(h)
logging.getLogger("custom_components.oblo_living.coordinator").setLevel(logging.WARNING)

TS1 = "2026-11-06T08:06:01.000Z"
TS2 = "2026-11-13T09:12:44.000Z"

class Api:
    def __init__(self, stamps):
        self.stamps = list(stamps)
        self.i = 0
    async def async_login(self): pass
    async def async_list_gateways(self):
        ts = self.stamps[min(self.i, len(self.stamps)-1)]
        return [{"_id": "645cd2167740a3ddc4c010a5", "serialNumber": "C83DD4300D5B",
                 "type": "hg", "connectionStatus": "online",
                 "lastTimeOnline": ts, "lastTimeStatusUpdated": ts}]
    async def async_gateway_devices(self): return []
    async def async_close(self): pass

def uplink_lines(s):
    return [l for l in s.splitlines() if "UPLINK" in l]

async def main():
    co = ObloDataCoordinator(None, Api([TS1, TS1, TS2]))
    checks = []
    for n in (1, 2, 3):
        co.api.i = n - 1
        await co._async_fetch()
    lines = uplink_lines(buf.getvalue())
    for l in lines: print(l)
    print("---")
    checks.append(("3 polls logged 3 UPLINK lines", len(lines) == 3, len(lines)))
    checks.append(("poll1 advanced=first", "advanced=first" in lines[0], lines[0] if lines else ""))
    checks.append(("poll2 advanced=False (stalled tunnel)", "advanced=False" in lines[1], lines[1] if len(lines)>1 else ""))
    checks.append(("poll3 advanced=True (timestamp moved)", "advanced=True" in lines[2], lines[2] if len(lines)>2 else ""))
    checks.append(("serial present", all("sn=C83DD4300D5B" in l for l in lines), ""))
    checks.append(("connectionStatus present", all("connectionStatus=online" in l for l in lines), ""))
    ok = True
    for label, cond, d in checks:
        print(("PASS  " if cond else "FAIL  ") + label + ("" if cond else f"  <- {d}"))
        ok = ok and cond
    print(f"\n{sum(1 for _,c,_ in checks if c)}/{len(checks)} checks")
    sys.exit(0 if ok else 1)

asyncio.run(main())
