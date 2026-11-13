"""Write-path safety: a refused command must never look like an auth failure.

Two failures are conflated by a single exception type, and the write path is
where it becomes user-visible:

  - ObloAuthError    -> coordinator re-logs in, and on repeat raises
                        ConfigEntryAuthFailed => HA shows the reauth form.
  - reply code != 0  -> the gateway ANSWERED and refused (not writable, bad
                        value type, unknown service).

Mapping the second onto the first means toggling one unsupported switch asks
the user for their password. ObloCommandError exists to keep them apart.

Also covers the reconnect wedge: a paho Client is constructed *before*
connect() is called, so a failed first connect left `self._client` non-None and
async_connect() short-circuited forever -- the client published into a dead
socket and timed out until HA restarted.

Run: python3 command_error_test.py
"""
import asyncio
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
COMP = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMP))
sys.path.insert(0, HERE)
sys.path.insert(0, COMP)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401

from custom_components.oblo_living import cloud_mqtt as cm  # noqa: E402
from custom_components.oblo_living.cloud_api import ObloAuthError  # noqa: E402
from custom_components.oblo_living.cloud_mqtt import (  # noqa: E402
    ObloCloudMqtt, ObloCloudMqttError, ObloCommandError,
)
from custom_components.oblo_living.coordinator import (  # noqa: E402
    ObloDataCoordinator, normalise_ohm_objects,
)

CHECKS = []


def check(desc, ok, detail=""):
    CHECKS.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + desc + ("" if ok else f"  <- {detail}"))


class FakeApi:
    async def async_login(self):
        return "sid"

    async def async_list_gateways(self):
        return [{"id": "gw1", "cached": 1}]

    async def async_gateway_devices(self):
        return [{"id": "gw1", "cached": 1}]


class FakeOhm:
    def __init__(self, error=None):
        self.error = error

    async def async_object_list(self):
        if self.error:
            raise self.error
        return []


async def test_exception_hierarchy():
    # The coordinator branches on ObloAuthError. If a command error is also one,
    # every refusal re-logs in -- the whole point of this test file.
    check("ObloCommandError is a cloud MQTT error",
          issubclass(ObloCommandError, ObloCloudMqttError))
    check("ObloCommandError is NOT an ObloAuthError (no reauth trigger)",
          not issubclass(ObloCommandError, ObloAuthError))
    e = ObloCommandError("no such service", 5)
    check("code is preserved on the exception", e.code == 5, e.code)


async def test_coordinator_degrades_and_still_reauths():
    # refused command -> warn + REST fallback, never a reauth prompt
    coord = ObloDataCoordinator(None, FakeApi())  # type: ignore[arg-type]
    coord.set_ohm_client(FakeOhm(ObloCommandError("not writable", 5)))
    devices = await coord._async_fetch()
    check("refused command degrades to REST fallback", len(devices) == 1, devices)

    # genuine auth failure must STILL propagate to the reauth path
    coord2 = ObloDataCoordinator(None, FakeApi())  # type: ignore[arg-type]
    coord2.set_ohm_client(FakeOhm(ObloAuthError("session expired")))
    raised = False
    try:
        await coord2._async_fetch()
    except ObloAuthError:
        raised = True
    check("real auth failure still propagates (reauth preserved)", raised)


class FakePaho:
    def __init__(self):
        self.published = []
        self.subscriptions = []
        self._mid = 0

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, payload, qos))

    def subscribe(self, topic, qos=1):
        self.subscriptions.append(topic)
        self._mid += 1
        return (0, self._mid)


def make_client():
    c = ObloCloudMqtt(
        session_id="sid0123456789abcdef",
        user_id="uid0123456789abcdef",
        username="andrew",
        gateway_serial="C83DD4300D5B",
    )
    fake = FakePaho()
    c._client = fake
    c._connected.set()
    return c, fake


async def test_refusal_is_not_retried():
    """A refused write must be sent once. Retrying could actuate twice."""
    c, fake = make_client()
    task = asyncio.create_task(c.async_set_property("node14", "outlet0", "State", 1))
    await asyncio.sleep(0.05)
    uid = __import__("json").loads(fake.published[0][1])["uid"]
    c._on_message(None, None, types.SimpleNamespace(
        topic="oblo/uid0123456789abcdef/cli/sid0123456789abcdef/rsp",
        payload=__import__("json").dumps(
            {"uid": uid, "code": 5, "message": "no such service"}).encode()))
    try:
        await asyncio.wait_for(task, timeout=2)
        check("refused write raises", False, "returned normally")
    except ObloCommandError:
        check("refused write raises", True)
    check("refused write published exactly once (no replay)",
          len(fake.published) == 1, len(fake.published))


class _FakeClientObj:
    def __init__(self, connect_error=None):
        self.connect_error = connect_error
        self.connected_to = None
        self.loop_started = False
        self.subscriptions = []

    def username_pw_set(self, *a, **k):
        pass

    def ws_set_options(self, **k):
        pass

    def tls_set(self, **k):
        pass

    def tls_insecure_set(self, *a):
        pass

    def connect(self, host, port, keepalive):
        self.connected_to = (host, port)
        if self.connect_error:
            raise self.connect_error

    def loop_start(self):
        self.loop_started = True

    def loop_stop(self):
        self.loop_started = False

    def disconnect(self):
        pass

    def subscribe(self, topic, qos=1):
        self.subscriptions.append(topic)
        return (0, len(self.subscriptions))


class _FakeMqttModule:
    def __init__(self, connect_error=None):
        self.built = []
        self.connect_error = connect_error

    def Client(self, *a, **k):
        c = _FakeClientObj(self.connect_error)
        self.built.append(c)
        return c

    # paho exposes constants on the module
    MQTTv311 = 4
    MQTTv5 = 5


async def test_reconnect_after_failed_connect():
    """A stale client must be rebuilt, not silently reused."""
    fake_mqtt = _FakeMqttModule(connect_error=OSError("dial tcp: refused"))
    orig = cm._mqtt
    cm._mqtt = lambda: fake_mqtt
    try:
        c = ObloCloudMqtt(
            session_id="sid0123456789abcdef",
            user_id="uid0123456789abcdef",
            username="andrew",
            gateway_serial="C83DD4300D5B",
        )
        # first attempt fails at the socket
        try:
            await c.async_connect()
        except Exception:
            pass
        check("failed connect leaves no usable client state",
              c._conn_error is not None, c._conn_error)

        # second attempt must build a NEW client rather than return early
        fake_mqtt.connect_error = None
        try:
            await asyncio.wait_for(c.async_connect(), timeout=5)
        except asyncio.TimeoutError:
            pass
        check("retry after a failed connect builds a fresh client",
              len(fake_mqtt.built) == 2, len(fake_mqtt.built))
        check("retry actually dialled the broker",
              fake_mqtt.built[-1].connected_to is not None,
              fake_mqtt.built[-1].connected_to)
    finally:
        cm._mqtt = orig


async def test_service_type_preserved():
    objs = [{
        "id": "dev1",
        "device_name": "Kitchen Outlet",
        "service_list": [{
            "name": "outlet0",
            "type": "OutletService",
            "property_list": [{"name": "State", "value": "0"}],
        }],
    }]
    devs = normalise_ohm_objects(objs)
    svc = devs[0]["services"][0]
    check("service_type preserved for the write path",
          svc.get("service_type") == "OutletService", svc)
    check("service alias preserved", svc["service"] == "outlet0", svc["service"])
    check("props preserved", svc["props"] == {"State": "0"}, svc["props"])

    # category is the fallback when the payload uses that key instead
    objs[0]["service_list"][0].pop("type")
    objs[0]["service_list"][0]["category"] = "OutletService"
    devs2 = normalise_ohm_objects(objs)
    check("category used when type is absent",
          devs2[0]["services"][0].get("service_type") == "OutletService")

    # no type at all must stay None, not a string "None"
    objs[0]["service_list"][0].pop("category")
    devs3 = normalise_ohm_objects(objs)
    check("absent type is None", devs3[0]["services"][0].get("service_type") is None)


async def main():
    await test_exception_hierarchy()
    await test_coordinator_degrades_and_still_reauths()
    await test_refusal_is_not_retried()
    await test_reconnect_after_failed_connect()
    await test_service_type_preserved()

    ok = all(CHECKS)
    print(f"\n{sum(CHECKS)}/{len(CHECKS)} checks")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
