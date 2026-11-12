"""cloud_mqtt.py — OHM-over-WSS client behaviour, with paho faked out."""
import asyncio
import json
import os
import sys
import types

COMPONENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, COMPONENT)
_pkg = types.ModuleType("oblo_living")
_pkg.__path__ = [COMPONENT]
sys.modules.setdefault("oblo_living", _pkg)

import ha_stub  # noqa: F401
from oblo_living import cloud_mqtt
from oblo_living.cloud_api import ObloAuthError
from oblo_living.cloud_mqtt import ObloCloudMqtt, ObloTimeoutError

CHECKS = []


def check(desc, ok, detail=None):
    CHECKS.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {desc}")
    if not ok and detail is not None:
        print(f"     -> {detail}")


class FakePaho:
    def __init__(self):
        self.published = []
        self.subscriptions = []

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, json.loads(payload), qos))

    def subscribe(self, topic, qos=1):
        self.subscriptions.append(topic)


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


async def test_object_list():
    c, fake = make_client()
    task = asyncio.create_task(c.async_object_list())
    await asyncio.sleep(0.05)

    check("publish happened", len(fake.published) == 1, fake.published)
    topic, body, qos = fake.published[0]
    check("req topic uses gateway owner userId + serial",
          topic == "oblo/uid0123456789abcdef/gtw/C83DD4300D5B/ohm/req", topic)
    check("command name", body["name"] == "get_object_list_by_type_detailed", body)
    check("params.type=device", body["params"] == {"type": "device"}, body)
    check("type=command", body.get("type") == "command", body)
    check("uid is int", isinstance(body["uid"], int), body)
    check("QoS 0 for commands", qos == 0, qos)

    # simulate the rsp
    rsp = json.dumps({
        "uid": body["uid"], "code": 0,
        "params": {"object_list": [{"id": "d1", "device_name": "Hall", "service_list": []}]},
    })
    c._on_message(None, None, types.SimpleNamespace(
        topic="oblo/uid0123456789abcdef/cli/sid0123456789abcdef/rsp",
        payload=rsp.encode()))

    result = await asyncio.wait_for(task, timeout=5)
    check("object_list returned", len(result) == 1 and result[0]["id"] == "d1", result)
    check("future cleared after response", not c._futures, c._futures)
    await c.async_close()


async def test_error_code():
    c, fake = make_client()
    task = asyncio.create_task(c.async_object_list())
    await asyncio.sleep(0.05)
    uid = fake.published[0][1]["uid"]
    rsp = json.dumps({"uid": uid, "code": -1, "message": "Privileged mode is active"})
    c._on_message(None, None, types.SimpleNamespace(
        topic="oblo/uid0123456789abcdef/cli/sid0123456789abcdef/rsp",
        payload=rsp.encode()))
    err = None
    try:
        await asyncio.wait_for(task, timeout=5)
    except ObloAuthError as e:
        err = e
    check("non-zero code -> ObloAuthError with server message",
          err is not None and "Privileged mode" in str(err), err)
    await c.async_close()


async def test_uid_isolation():
    """A response for someone else's uid must not resolve our future."""
    c, fake = make_client()
    task = asyncio.create_task(c.async_object_list())
    await asyncio.sleep(0.05)
    c._on_message(None, None, types.SimpleNamespace(
        topic="oblo/uid0123456789abcdef/cli/sid0123456789abcdef/rsp",
        payload=json.dumps({"uid": 99999, "code": 0, "params": {"object_list": []}}).encode()))
    check("foreign uid ignored", not task.done())
    task.cancel()
    await c.async_close()


async def test_timeout_retries():
    old = cloud_mqtt.REQUEST_TIMEOUT
    cloud_mqtt.REQUEST_TIMEOUT = 0.2
    try:
        c, fake = make_client()
        err = None
        try:
            await c.async_object_list()
        except ObloTimeoutError as e:
            err = e
        check("timeout raises ObloTimeoutError", err is not None, err)
        check("retried 3 times", len(fake.published) == 3, len(fake.published))
        check("uid increments per attempt",
              [p[1]["uid"] for p in fake.published] == sorted({p[1]["uid"] for p in fake.published}),
              [p[1]["uid"] for p in fake.published])
        await c.async_close()
    finally:
        cloud_mqtt.REQUEST_TIMEOUT = old


async def test_events():
    c, fake = make_client()
    got = []
    await c.async_subscribe_events(got.append)
    check("evt subscription made",
          any("/gtw/+/ohm/evt" in s for s in fake.subscriptions), fake.subscriptions)
    c._on_message(None, None, types.SimpleNamespace(
        topic="oblo/uid0123456789abcdef/gtw/C83DD4300D5B/ohm/evt",
        payload=json.dumps({"name": "device_property_changed",
                            "params": {"device_id": "d1", "service_name": "PirService",
                                       "property_name": "State", "property_value": "1"}}).encode()))
    await asyncio.sleep(0.05)
    check("event dispatched to callback", len(got) == 1 and got[0]["name"] == "device_property_changed", got)
    await c.async_close()


async def test_connack():
    c, fake = make_client()
    c._connected.clear()
    c._on_connect(None, None, {}, 5)
    check("CONNACK 5 -> ObloAuthError stored", isinstance(c._conn_error, ObloAuthError), c._conn_error)
    check("CONNACK 5 does not subscribe",
          not any("/rsp" in s for s in fake.subscriptions), fake.subscriptions)

    c2, fake2 = make_client()
    c2._connected.clear()
    # _on_connect uses self._client to subscribe; ensure it's set
    c2._client = fake2
    c2._on_connect(None, None, {}, 0)
    subs = [s for s in fake2.subscriptions if s.endswith("/rsp")]
    check("CONNACK 0 subscribes session rsp",
          "oblo/uid0123456789abcdef/cli/sid0123456789abcdef/rsp" in subs, subs)
    check("CONNACK 0 also subscribes userId rsp (non-admin path)",
          "oblo/uid0123456789abcdef/cli/uid0123456789abcdef/rsp" in subs, subs)


async def test_bad_payload():
    c, fake = make_client()
    c._on_message(None, None, types.SimpleNamespace(
        topic="oblo/x/rsp", payload=b"not json at all"))
    check("garbage payload does not raise", True)


def main():
    async def run():
        await test_object_list()
        await test_error_code()
        await test_uid_isolation()
        await test_timeout_retries()
        await test_events()
        await test_connack()
        await test_bad_payload()

    asyncio.run(run())
    failed = CHECKS.count(False)
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} checks pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())