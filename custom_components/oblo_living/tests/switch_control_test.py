"""Control path: write a property, then confirm it by read-back.

The rule this file exists to enforce: a write is NEVER reported as success on
the strength of the command reply. PUBACK is unconditional on this broker and
`code: 0` only means the gateway accepted the command, so a switch that flips
its UI on that basis is a switch that lies.

Three verdicts, and they must not be confused:
  confirmed=True   a read-back matched the value we sent
  confirmed=False  the gateway answered and the value did NOT change
  confirmed=None   the read-back itself failed -- unknown, never success

Run: python3 switch_control_test.py
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

from homeassistant.exceptions import HomeAssistantError  # noqa: E402

from custom_components.oblo_living.cloud_mqtt import ObloCommandError  # noqa: E402
from custom_components.oblo_living.coordinator import (  # noqa: E402
    ObloDataCoordinator, values_match,
)
from custom_components.oblo_living.const import (  # noqa: E402
    DOMAIN, OHM_STATE_ON, OHM_STATE_OFF,
)
from custom_components.oblo_living.entity_map import build_entities  # noqa: E402
from custom_components.oblo_living.entities.switch import ObloSwitch  # noqa: E402
from custom_components.oblo_living.services import (  # noqa: E402
    async_handle_get_property, async_handle_set_property,
    async_register_services,
)

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}"
          + (f"  <- {detail}" if detail and not cond else ""))


class FakeMqtt:
    """Records writes; replays a scripted sequence of read-backs."""

    def __init__(self, readbacks=None, read_error=None, send_error=None):
        self.writes = []
        self.reads = []
        self._readbacks = list(readbacks if readbacks is not None else ["1"])
        self._read_error = read_error
        self._send_error = send_error

    async def async_set_property(self, device_id, service_name, property_name,
                                 value, service_type=None, timeout=None):
        self.writes.append({"device_id": device_id, "service_name": service_name,
                            "property_name": property_name, "value": value,
                            "service_type": service_type})
        if self._send_error:
            raise self._send_error
        return {"property_value": value}

    async def async_get_property(self, device_id, service_name, property_name,
                                 service_type=None, timeout=None):
        self.reads.append({"device_id": device_id, "service_name": service_name,
                           "property_name": property_name,
                           "service_type": service_type})
        if self._read_error:
            raise self._read_error
        if len(self._readbacks) > 1:
            return self._readbacks.pop(0)
        return self._readbacks[0]


def make_coordinator(ohm=None, devices=None):
    hass = type("H", (), {"loop": None})()
    coord = ObloDataCoordinator(hass, api=None)
    coord.ohm = ohm
    coord.devices = devices if devices is not None else [
        {"id": "dev-relay-1", "name": "Relay", "class": "ohm",
         "services": [{"service": "outlet0",
                       "props": {"State": "0"},
                       "service_type": "OutletService"}]},
    ]
    coord.data = list(coord.devices)
    return coord


# --- value comparison -----------------------------------------------------
def test_values_match():
    check("text match", values_match("1", "1"))
    check("int vs text", values_match(1, "1"))
    check("bool vs text", values_match(True, "1"))
    check("'true' vs 1", values_match(True, "true"))
    check("mismatch 1 vs 0", not values_match("1", "0"))
    check("off spellings agree", values_match("0", "false"))
    check("float equality", values_match(21.0, "21.0"))
    check("never matches on garbage", not values_match("1", "unavailable"))


# --- coordinator: the three verdicts --------------------------------------
async def test_confirmed():
    ohm = FakeMqtt(readbacks=["1"])
    coord = make_coordinator(ohm)
    res = await coord.async_write_service_property(
        "dev-relay-1", "outlet0", "State", OHM_STATE_ON,
        service_type="OutletService", confirm_timeout=1.0)
    check("write sends the value", ohm.writes and ohm.writes[0]["value"] == "1",
          ohm.writes)
    check("write carries service_type",
          ohm.writes[0]["service_type"] == "OutletService", ohm.writes[0])
    check("write carries instance alias",
          ohm.writes[0]["service_name"] == "outlet0", ohm.writes[0])
    check("read-back happens", len(ohm.reads) == 1, len(ohm.reads))
    check("confirmed on matching read-back", res["confirmed"] is True, res)
    check("readback carried in result", res["readback"] == "1", res)
    # cache poked so the UI flips without waiting for the 60 s poll
    props = coord.devices[0]["services"][0]["props"]
    check("cached prop updated after confirmation", props["State"] == "1", props)


async def test_confirmed_after_lag():
    """A device that reports the old value once, then catches up, is fine."""
    ohm = FakeMqtt(readbacks=["1"])          # first read: stale
    coord = make_coordinator(ohm)
    # script: stale -> stale -> correct
    ohm._readbacks = ["0", "0", "1"]
    res = await coord.async_write_service_property(
        "dev-relay-1", "outlet0", "State", OHM_STATE_ON, confirm_timeout=2.0)
    check("polls until the value catches up", res["confirmed"] is True, res)
    check("more than one read for a laggy device", len(ohm.reads) >= 2,
          len(ohm.reads))


async def test_unconfirmed():
    """Gateway accepts, value never changes -> confirmed=False, not success."""
    ohm = FakeMqtt(readbacks=["0"])
    coord = make_coordinator(ohm)
    res = await coord.async_write_service_property(
        "dev-relay-1", "outlet0", "State", OHM_STATE_ON, confirm_timeout=0.3)
    check("mismatch is confirmed=False", res["confirmed"] is False, res)
    check("readback kept for the error message", res["readback"] == "0", res)
    check("cache NOT poked on failure",
          coord.devices[0]["services"][0]["props"]["State"] == "0",
          coord.devices[0]["services"][0]["props"])


async def test_readback_failed():
    """Read-back error is 'unknown', never success."""
    ohm = FakeMqtt(read_error=RuntimeError("timed out"))
    coord = make_coordinator(ohm)
    res = await coord.async_write_service_property(
        "dev-relay-1", "outlet0", "State", OHM_STATE_ON, confirm_timeout=0.3)
    check("read-back failure is confirmed=None", res["confirmed"] is None, res)
    check("read-back failure is explained", "read-back" in (res["error"] or ""),
          res["error"])


async def test_refused():
    """A refused write is False, not an auth error, and is not retried."""
    ohm = FakeMqtt(send_error=ObloCommandError("code=7 no such service"))
    coord = make_coordinator(ohm)
    res = await coord.async_write_service_property(
        "dev-relay-1", "outlet0", "State", OHM_STATE_ON, confirm_timeout=0.3)
    check("refusal is confirmed=False", res["confirmed"] is False, res)
    check("refusal is labelled refused", "refused" in (res["error"] or ""),
          res["error"])
    check("refusal is not retried", len(ohm.writes) == 1, len(ohm.writes))
    check("no read-back after a refusal", len(ohm.reads) == 0, len(ohm.reads))


async def test_no_ohm_client():
    coord = make_coordinator(None)
    res = await coord.async_write_service_property(
        "dev-relay-1", "outlet0", "State", OHM_STATE_ON)
    check("REST-only mode refuses writes", res["confirmed"] is None, res)
    check("REST-only reason is explicit", "OHM" in (res["error"] or ""),
          res["error"])


# --- entity behaviour -----------------------------------------------------
async def test_switch_entity():
    ohm = FakeMqtt(readbacks=["1"])
    coord = make_coordinator(ohm)
    ents = [e for e in build_entities(coord.data) if e["platform"] == "switch"]
    check("one switch built from OutletService", len(ents) == 1, ents)
    check("build_entities carries service_type",
          ents[0].get("service_type") == "OutletService", ents[0])

    sw = ObloSwitch(coord, ents[0])
    check("switch available with OHM client", sw.available is True)
    check("is_on False from State 0", sw.is_on is False)

    await sw.async_turn_on()
    check("turn_on sends '1'", ohm.writes[-1]["value"] == OHM_STATE_ON,
          ohm.writes[-1])
    check("turn_on used the canonical type",
          ohm.writes[-1]["service_type"] == "OutletService", ohm.writes[-1])
    check("entity reports on after confirmation", sw.is_on is True)

    # an unconfirmed write must raise, never silently flip the UI
    coord.ohm = FakeMqtt(readbacks=["1"])       # device will not turn off
    sw2 = ObloSwitch(coord, ents[0])
    coord.devices[0]["services"][0]["props"]["State"] = "1"
    coord.data = list(coord.devices)
    try:
        await sw2.async_turn_off()
        check("unconfirmed turn_off raises", False, "returned normally")
    except HomeAssistantError as err:
        check("unconfirmed turn_off raises", True, str(err))
        check("error names the reason", "unconfirmed" in str(err).lower(), str(err))
    check("UI state not flipped by a failed write", sw2.is_on is True)

    # no OHM client -> entity unavailable rather than a dead switch
    coord.ohm = None
    check("switch unavailable without OHM", ObloSwitch(coord, ents[0]).available is False)


def test_entity_count_unchanged():
    """Adding service_type must not add or remove a single entity."""
    devices = [{"id": "dev-relay-1", "name": "Relay", "class": "ohm",
                "services": [{"service": "outlet0", "props": {"State": "0"},
                              "service_type": "OutletService"},
                             {"service": "basic0", "props": {"State": "0"},
                              "service_type": "BasicService"}]}]
    ents = build_entities(devices)
    check("alias collapse still holds: one switch, not two",
          len([e for e in ents if e["platform"] == "switch"]) == 1,
          [e["unique_id"] for e in ents])
    check("REST-sourced device yields service_type None",
          build_entities([{"id": "d", "name": "n", "class": "relay",
                           "services": [{"service": "outlet0",
                                         "props": {"State": "1"}}]}])[0]
          .get("service_type") is None)


# --- HA services (Developer Tools path) -----------------------------------
class FakeServices:
    def __init__(self):
        self.registered = {}

    def has_service(self, domain, name):
        return (domain, name) in self.registered

    def async_register(self, domain, name, handler, schema=None, **kwargs):
        self.registered[(domain, name)] = {"handler": handler,
                                          "schema": schema, "kwargs": kwargs}


class FakeHass:
    def __init__(self, coord):
        self.data = {DOMAIN: {"entry-1": {"coordinator": coord}}}
        self.services = FakeServices()


class FakeCall:
    def __init__(self, data):
        self.data = data


async def test_service_set_property():
    ohm = FakeMqtt(readbacks=["1"])
    coord = make_coordinator(ohm)
    hass = FakeHass(coord)
    call = FakeCall({"device_id": "dev-relay-1", "service_name": "outlet0",
                     "property_name": "State", "property_value": "1",
                     "service_type": "OutletService"})
    res = await async_handle_set_property(hass, call)
    check("set_property returns the verdict", res.get("confirmed") is True, res)
    check("set_property result carries readback", res.get("readback") == "1", res)

    # the same call against a device that does not move must raise
    coord.ohm = FakeMqtt(readbacks=["0"])
    try:
        await async_handle_set_property(hass, call)
        check("unconfirmed set_property raises", False, "returned normally")
    except HomeAssistantError as err:
        check("unconfirmed set_property raises", True, str(err))
        check("service error carries sent + readback",
              "sent=" in str(err) and "readback=" in str(err), str(err))
        check("service error says unconfirmed", "unconfirmed" in str(err).lower(),
              str(err))


async def test_service_get_property():
    ohm = FakeMqtt(readbacks=["1"])
    hass = FakeHass(make_coordinator(ohm))
    call = FakeCall({"device_id": "dev-relay-1", "service_name": "outlet0",
                     "property_name": "State"})
    res = await async_handle_get_property(hass, call)
    check("get_property returns the value", res.get("property_value") == "1", res)
    check("get_property bypasses the cache", ohm.reads and
          ohm.reads[0]["service_name"] == "outlet0", ohm.reads)

    hass2 = FakeHass(make_coordinator(None))
    try:
        await async_handle_get_property(hass2, call)
        check("get_property without OHM raises", False, "returned normally")
    except HomeAssistantError:
        check("get_property without OHM raises", True)


async def test_service_registration():
    hass = FakeHass(make_coordinator(FakeMqtt()))
    await async_register_services(hass)
    check("set_property registered",
          (DOMAIN, "set_property") in hass.services.registered,
          list(hass.services.registered))
    check("get_property registered",
          (DOMAIN, "get_property") in hass.services.registered,
          list(hass.services.registered))
    check("services declare response support",
          all(v["kwargs"].get("supports_response") is not None
              for v in hass.services.registered.values()),
          {k: v["kwargs"] for k, v in hass.services.registered.items()})
    count = len(hass.services.registered)
    await async_register_services(hass)          # reload of the entry
    check("registration is idempotent", len(hass.services.registered) == count,
          len(hass.services.registered))


def main():
    test_values_match()

    async def run():
        await test_confirmed()
        await test_confirmed_after_lag()
        await test_unconfirmed()
        await test_readback_failed()
        await test_refused()
        await test_no_ohm_client()
        await test_switch_entity()
        await test_service_set_property()
        await test_service_get_property()
        await test_service_registration()

    asyncio.run(run())
    test_entity_count_unchanged()

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
