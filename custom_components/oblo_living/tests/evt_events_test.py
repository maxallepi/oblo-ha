"""Gateway events must actually reach something.

Until v0.2.32 `_async_start_ohm` subscribed to the evt stream with
`lambda p: None` -- every event was received by the MQTT client and thrown
away. Two consequences:

  1. entity state only ever moved on the 60 s poll, never on a live change;
  2. @research's one-press test could not answer "does the gateway end a
     siren on its own?", because the evt line that would prove it was dropped
     before it could be logged.

These checks lock the wiring: a real handler, tolerant payload parsing, the
cached device tree patched in place, and alert properties logged at INFO.

Run: python3 evt_events_test.py
"""
import asyncio
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401

from custom_components.oblo_living.coordinator import ObloDataCoordinator  # noqa: E402

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}"
          + (f"  <- {detail}" if detail and not cond else ""))


class FakeApi:
    async def async_login(self):
        return "sid"

    async def async_list_gateways(self):
        return []

    async def async_list_devices(self):
        return []


DEVICES = [
    {"id": "15", "name": "Gateway Simple alert", "class": "ohm",
     "services": [{"service": "SimpleAlertService",
                   "props": {"DeviceType": "15"},
                   "service_type": "SimpleAlertService"}]},
    {"id": "14", "name": "Hallway lights", "class": "ohm",
     "services": [{"service": "outlet0", "props": {"State": "false"},
                   "service_type": "OutletService"}]},
]


def make_coordinator():
    c = ObloDataCoordinator.__new__(ObloDataCoordinator)
    # Set only what the event path touches: no HA runtime needed here.
    c.api = FakeApi()
    c.ohm = None
    c.devices = [d for d in DEVICES]
    c.data = [d for d in DEVICES]
    c._alert_stoppers = {}
    c._alert_generation = {}
    c.updated = []
    c.async_set_updated_data = lambda d: c.updated.append(d)
    return c


class Capture(logging.Handler):
    """Collect (level, message) so we can assert what a human would see."""

    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append((record.levelno, record.getMessage()))

    def infos(self):
        return [m for lvl, m in self.lines if lvl >= logging.INFO]


print("--- attach ---")

coord = make_coordinator()


class FakeOhm:
    def __init__(self, raises=False):
        self.cb = None
        self.raises = raises

    async def async_subscribe_events(self, cb):
        if self.raises:
            raise RuntimeError("broker said no")
        self.cb = cb


check("no client -> attach returns False", asyncio.run(coord.async_attach_events()) is False,
      "attached without a client")

coord.ohm = FakeOhm()
check("client -> attach returns True", asyncio.run(coord.async_attach_events()) is True)
check("attach hands over a real callable, not a lambda:None",
      coord.ohm.cb is not None and coord.ohm.cb == coord.handle_event,
      f"{coord.ohm.cb!r}")

coord.ohm = FakeOhm(raises=True)
check("a raising subscribe is False, not an exception",
      asyncio.run(coord.async_attach_events()) is False)

coord.ohm = object()   # client without the method at all
check("client without async_subscribe_events -> False",
      asyncio.run(coord.async_attach_events()) is False)


print("\n--- payload parsing ---")

coord = make_coordinator()
cap = Capture()
_LOGGER = logging.getLogger("custom_components.oblo_living.coordinator")
_LOGGER.addHandler(cap)
_LOGGER.setLevel(logging.DEBUG)

# camelCase (what the SPA uses)
coord.handle_event({"name": "device_property_changed",
                    "params": {"deviceId": "14", "serviceName": "outlet0",
                               "propertyName": "State", "value": "true"}})
check("camelCase event patched the cache",
      coord.data[1]["services"][0]["props"]["State"] == "true",
      f"{coord.data[1]['services'][0]['props']}")
check("a real change notifies entities", len(coord.updated) == 1, f"{len(coord.updated)}")

# same value again: no pointless notification
coord.handle_event({"name": "device_property_changed",
                    "params": {"deviceId": "14", "serviceName": "outlet0",
                               "propertyName": "State", "value": "true"}})
check("unchanged value does not re-notify", len(coord.updated) == 1, f"{len(coord.updated)}")

# snake_case (gateway may not agree with the SPA on spelling)
coord.handle_event({"name": "device_property_changed",
                    "params": {"device_id": "14", "service_name": "outlet0",
                               "property_name": "State", "new_value": "false"}})
check("snake_case event patched the cache",
      coord.data[1]["services"][0]["props"]["State"] == "false",
      f"{coord.data[1]['services'][0]['props']}")

# objectId + property (another plausible spelling)
coord.handle_event({"name": "device_property_changed",
                    "params": {"objectId": "14", "service": "outlet0",
                               "property": "State", "val": "true"}})
check("objectId/property/val spelling patched the cache",
      coord.data[1]["services"][0]["props"]["State"] == "true",
      f"{coord.data[1]['services'][0]['props']}")

# unknown device: harmless, no notification
before = len(coord.updated)
coord.handle_event({"name": "device_property_changed",
                    "params": {"deviceId": "9999", "serviceName": "outlet0",
                               "propertyName": "State", "value": "true"}})
check("unknown device is a no-op", len(coord.updated) == before)

# non-property events must not crash and must not notify
before = len(coord.updated)
coord.handle_event({"name": "gtw_online_status_changed",
                    "params": {"serialNumber": "C83DD4300D5B", "isOnline": True}})
coord.handle_event({"name": "something_else", "params": {}})
coord.handle_event("not a dict at all")
check("non-property events are tolerated", len(coord.updated) == before)


print("\n--- alert properties are visible at INFO ---")

cap.lines.clear()
coord.handle_event({"name": "device_property_changed",
                    "params": {"deviceId": "15", "serviceName": "SimpleAlertService",
                               "propertyName": "PropAlertStartStop", "value": "false"}})
infos = cap.infos()
check("PropAlertStartStop change logs at INFO",
      any("PropAlertStartStop" in m for m in infos), f"{infos}")
import re
check("the alert evt line carries a millisecond stamp",
      any("PropAlertStartStop" in m
          and re.search(r"\d{2}:\d{2}:\d{2}\.\d{3}", m) for m in infos), f"{infos}")

cap.lines.clear()
coord.handle_event({"name": "device_property_changed",
                    "params": {"deviceId": "14", "serviceName": "outlet0",
                               "propertyName": "State", "value": "false"}})
check("ordinary property changes stay at DEBUG (no log spam)",
      not any("State" in m for m in cap.infos()), f"{cap.infos()}")

_LOGGER.removeHandler(cap)


print("\n--- select: unverifiable write must not raise ---")
# @hermes: SimpleAlertService has zero property rows in ohm.db, so every
# read-back fails and `confirmed` is None. The old `if not confirmed` treated
# that as a refusal and raised on every single press.
from custom_components.oblo_living.entities.select import ObloAlertTypeSelect  # noqa: E402


class NullCoordinator:
    ohm = object()

    def __init__(self, result):
        self.result = result
        self.data = [{"id": "15", "name": "Gateway Simple alert",
                      "services": [{"service": "SimpleAlertService",
                                    "props": {}, "service_type": "SimpleAlertService"}]}]

    async def async_write_service_property(self, *a, **kw):
        return self.result


ENT = {"device_id": "15", "service": "SimpleAlertService",
       "service_type": "SimpleAlertService", "name": "Gateway Simple alert",
       "name_suffix": "Type", "options": ["Notification", "Alarm", "Warning", "Emergency"],
       "unique_id": "oblo_15_SimpleAlertService_select_Type"}


async def try_select(result):
    ent = ObloAlertTypeSelect(NullCoordinator(result), ENT)
    try:
        await ent.async_select_option("Warning")
        return "ok"
    except Exception as err:
        return f"{type(err).__name__}: {err}"


check("confirmed=True -> ok", asyncio.run(try_select({"confirmed": True})) == "ok")
check("confirmed=None (no read-back on this device) -> ok, not an error",
      asyncio.run(try_select({"confirmed": None, "error": "read-back failed"})) == "ok",
      asyncio.run(try_select({"confirmed": None, "error": "read-back failed"})))
raised = asyncio.run(try_select({"confirmed": False, "error": "device said no"}))
check("confirmed=False (gateway disagrees) -> still raises", raised.startswith("HomeAssistantError"),
      raised)


print("\n--- SUMMARY ---")
passed = sum(1 for _, ok, _ in CHECKS if ok)
print(f"{passed}/{len(CHECKS)} checks passed")
for label, ok, detail in CHECKS:
    if not ok:
        print(f"  FAIL: {label} <- {detail}")
if passed != len(CHECKS):
    sys.exit(1)
print("All checks passed.")
