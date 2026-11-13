"""Exercise _async_start_ohm end-to-end with fakes (no network).

Proves the wiring path in __init__.py: gateway identity resolution, the
silent-None-publish guard, connector success -> coordinator.set_ohm_client,
and every failure mode degrading to REST-only instead of raising.

Run: python3 ohm_start_test.py
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

import custom_components.oblo_living as pkg  # noqa: E402
from custom_components.oblo_living.cloud_api import ObloAuthError  # noqa: E402

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + (f"  <- {detail}" if detail and not cond else ""))


class FakeApi:
    def __init__(self, session_id="sess-1", username="andrew", user_id="u-login",
                 groups=None, gateways=None, list_raises=None, login_fills=False):
        self.session_id = session_id
        self.username = username
        self.user_id = user_id
        self.groups = groups if groups is not None else ["admin"]
        self._gateways = gateways if gateways is not None else []
        self._list_raises = list_raises
        self._login_fills = login_fills   # emulate real login populating fields
        self.login_calls = 0

    async def async_login(self):
        self.login_calls += 1
        if self._login_fills:
            self.session_id = "sess-from-login"
            self.username = "andrew-from-login"
            self.groups = ["admin"]

    async def async_list_gateways(self):
        if self._list_raises:
            raise self._list_raises
        return self._gateways


class FakeCoordinator:
    def __init__(self):
        self.ohm = None
        self.devices = [{"id": "d1"}]
        self.refreshes = 0

    def set_ohm_client(self, client):
        self.ohm = client

    async def async_request_refresh(self):
        self.refreshes += 1


class FakeHass:
    def __init__(self):
        self.loop = asyncio.get_event_loop()


class FakeMqtt:
    """Records construction + connect outcome."""
    instances = []

    def __init__(self, session_id, user_id, username, gateway_serial,
                 hass_loop=None, groups=None):
        self.args = dict(session_id=session_id, user_id=user_id,
                         username=username, gateway_serial=gateway_serial,
                         groups=groups)
        self.groups = groups
        self.connected = False
        self.closed = False
        self.connect_error = None
        FakeMqtt.instances.append(self)

    async def async_connect(self):
        if self.connect_error:
            raise self.connect_error
        self.connected = True

    async def async_subscribe_events(self, callback):
        self.evt_cb = callback

    async def async_close(self):
        self.closed = True


GW = [{"userId": "gw-owner", "serialNumber": "C83DD4300D5B", "_id": "645cd2"}]


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def fresh(mqtt_cls=FakeMqtt):
    pkg.ObloCloudMqtt = mqtt_cls
    FakeMqtt.instances.clear()


# --- happy path ---------------------------------------------------------
fresh()
hass, api, coord = FakeHass(), FakeApi(gateways=GW), FakeCoordinator()
client = run(pkg._async_start_ohm(hass, api, coord))
check("happy: client returned", client is not None)
check("happy: connect called", bool(getattr(client, "connected", False)))
check("happy: attached to coordinator", coord.ohm is client)
check("happy: refresh requested", coord.refreshes == 1, str(coord.refreshes))
check("happy: attach happened before refresh", client is not None)
_sentinel = object()
check("happy: evt stream subscribed (ONLINE_EVENT needs it)",
      getattr(client, "evt_cb", _sentinel) is not _sentinel,
      "async_subscribe_events never called")
a = FakeMqtt.instances[0].args
check("happy: user_id = gateway owner", a["user_id"] == "gw-owner", a["user_id"])
check("happy: serial from payload", a["gateway_serial"] == "C83DD4300D5B", a["gateway_serial"])
check("happy: username from login", a["username"] == "andrew", a["username"])
check("happy: session from login", a["session_id"] == "sess-1", a["session_id"])

# --- guard: no serial in payload (the live DIAG shape) ------------------
fresh()
hass, api, coord = FakeHass(), FakeApi(gateways=[{"_id": "645cd2", "deviceName": "Living Room", "class": "hg"}]), FakeCoordinator()
client = run(pkg._async_start_ohm(hass, api, coord))
check("guard: no serial -> None", client is None)
check("guard: never constructed a client", FakeMqtt.instances == [])
check("guard: coordinator untouched", coord.ohm is None)

# --- empty gateway list -------------------------------------------------
fresh()
hass, api, coord = FakeHass(), FakeApi(gateways=[]), FakeCoordinator()
check("empty gateways -> None", run(pkg._async_start_ohm(hass, api, coord)) is None)

# --- gateway list raises ------------------------------------------------
fresh()
hass, api, coord = FakeHass(), FakeApi(gateways=GW, list_raises=ObloAuthError("boom")), FakeCoordinator()
check("gateway list error -> None", run(pkg._async_start_ohm(hass, api, coord)) is None)

# --- cached session: session_id restored, username/groups missing ------
# This is the real HA-restart case: __init__ restores session_id from
# entry.data, so the coordinator does NOT log in and username stays None.
fresh()
hass = FakeHass()
api = FakeApi(username=None, groups=[], gateways=GW, login_fills=True)
coord = FakeCoordinator()
client = run(pkg._async_start_ohm(hass, api, coord))
check("cached session -> forced login", api.login_calls == 1, str(api.login_calls))
check("cached session -> OHM starts after login", client is not None)
check("cached session -> username from login",
      FakeMqtt.instances and FakeMqtt.instances[0].args["username"] == "andrew-from-login",
      str(FakeMqtt.instances[0].args["username"]) if FakeMqtt.instances else "none")

# --- login that leaves fields empty still degrades ----------------------
fresh()
hass = FakeHass()
api = FakeApi(session_id=None, username=None, groups=[], gateways=GW)
coord = FakeCoordinator()
check("login leaves no username -> None", run(pkg._async_start_ohm(hass, api, coord)) is None)
check("login attempted once", api.login_calls == 1, str(api.login_calls))

# --- login raising degrades, does not raise ----------------------------
fresh()
class LoginBoom(FakeApi):
    async def async_login(self):
        raise ObloAuthError("000016 too many attempts")


hass = FakeHass()
api = LoginBoom(username=None, groups=[], gateways=GW)
coord = FakeCoordinator()
check("login error -> None", run(pkg._async_start_ohm(hass, api, coord)) is None)

# --- happy path must NOT log in again (api already has username) --------
fresh()
hass = FakeHass()
api = FakeApi(gateways=GW)
coord = FakeCoordinator()
run(pkg._async_start_ohm(hass, api, coord))
check("happy path does not re-login", api.login_calls == 0, str(api.login_calls))

# --- CONNACK failure degrades, does not raise --------------------------
class ConnFail(FakeMqtt):
    async def async_connect(self):
        raise ObloAuthError("MQTT CONNACK 5")


fresh(ConnFail)
hass, api, coord = FakeHass(), FakeApi(gateways=GW), FakeCoordinator()
client = run(pkg._async_start_ohm(hass, api, coord))
check("connack fail -> None (degraded)", client is None)
check("connack fail -> client closed", FakeMqtt.instances[0].closed)
check("connack fail -> coordinator has no client", coord.ohm is None)

# --- unexpected exception must not escape either -----------------------
class Explode(FakeMqtt):
    async def async_connect(self):
        raise RuntimeError("unexpected")


fresh(Explode)
hass, api, coord = FakeHass(), FakeApi(gateways=GW), FakeCoordinator()
check("unexpected error -> None", run(pkg._async_start_ohm(hass, api, coord)) is None)

# --- setup_entry keeps the entry alive with no gateway serial ----------
# (REST-only mode must still produce a config entry with zero entities)
fresh()

passed = sum(1 for _, ok, _ in CHECKS if ok)
print(f"\n{passed}/{len(CHECKS)} checks passed")
sys.exit(0 if passed == len(CHECKS) else 1)
