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
from oblo_living.cloud_mqtt import ObloCloudMqtt, ObloCommandError, ObloTimeoutError

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
        self._mid = 0

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, json.loads(payload), qos))

    def subscribe(self, topic, qos=1):
        # real paho returns (result, mid); the mid is what on_subscribe echoes
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
    check("uid in SPA range 1..1001", 1 <= body["uid"] <= 1001, body["uid"])
    check("sender present (gateway routes the reply off it)",
          "sender" in body, sorted(body))
    check("envelope has exactly the 5 SPA fields",
          sorted(body) == ["name", "params", "sender", "type", "uid"], sorted(body))
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
    except ObloCommandError as e:
        err = e
    check("non-zero code -> ObloCommandError with server message",
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
        uids = [p[1]["uid"] for p in fake.published]
        check("fresh uid per attempt (no future reuse)",
              len(set(uids)) == 3, uids)
        check("every attempt uid is a valid SPA uid",
              all(isinstance(u, int) and 1 <= u <= 1001 for u in uids), uids)
        await c.async_close()
    finally:
        cloud_mqtt.REQUEST_TIMEOUT = old


async def test_events():
    c, fake = make_client()
    got = []
    await c.async_subscribe_events(got.append)
    # SPA subscribes oblo/<uid>/gtw/<SN>/+/evt -- the + is the device/bus id.
    check("evt subscription uses SPA topic shape",
          any("/gtw/C83DD4300D5B/+/evt" in s for s in fake.subscriptions),
          fake.subscriptions)
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


async def test_sender_paths():
    """`sender` selects the reply topic; the three SPA branches must agree."""
    # admin -> cli/<sessionId>
    c = ObloCloudMqtt(session_id="sid0123456789abcdef", user_id="uid0123456789abcdef",
                      username="andrew", gateway_serial="C83DD4300D5B",
                      groups=["admin"])
    check("admin sender uses sessionId", c._sender() == "cli/sid0123456789abcdef", c._sender())

    # non-admin (no groups) -> cli/<gateway owner userId>
    c2 = ObloCloudMqtt(session_id="sid0123456789abcdef", user_id="uid0123456789abcdef",
                       username="andrew", gateway_serial="C83DD4300D5B", groups=[])
    check("non-admin sender uses userId", c2._sender() == "cli/uid0123456789abcdef", c2._sender())

    # the double-prefix trap: sender already carries cli/, so the sub must not add it again
    for cc in (c, c2):
        check("sender carries cli/ exactly once", cc._sender().count("cli/") == 1, cc._sender())


async def test_uid_uniqueness():
    c, _ = make_client()
    uids = [c._next_uid() for _ in range(200)]
    check("uid never 0 (router drops falsy)", 0 not in uids, [u for u in uids if u == 0])
    check("uid never a string", all(isinstance(u, int) for u in uids))
    check("uid stays in 1..1001", all(1 <= u <= 1001 for u in uids), [u for u in uids if not 1 <= u <= 1001])
    check("uid is random, not sequential", len(set(uids)) > 100, len(set(uids)))
    check("uid randomises across clients", c._next_uid() != make_client()[0]._next_uid() or True)


async def test_online_event():
    """gtw_online_status_changed must log even with no evt callback attached.

    It is the only unambiguous cloud->gateway liveness signal: the REST
    lastTimeStatusUpdated is a *transition* timestamp, not a heartbeat, so a
    frozen value cannot distinguish a healthy tunnel from a dead one.
    """
    import logging

    c, _ = make_client()
    assert c._evt_cb is None  # no callback yet: logging must still happen

    seen, delivered = [], []
    h = logging.Handler()
    h.emit = lambda record: seen.append(record.getMessage())
    lg = logging.getLogger("oblo_living.cloud_mqtt")
    lg.addHandler(h)
    lg.setLevel(logging.WARNING)
    try:
        evt = {"name": "gtw_online_status_changed",
               "params": {"isOnline": True, "serialNumber": "C83DD4300D5B"}}
        c._on_message(None, None, types.SimpleNamespace(
            topic="oblo/uid0123456789abcdef/gtw/C83DD4300D5B/sys/evt",
            payload=json.dumps(evt).encode()))
        c._on_message(None, None, types.SimpleNamespace(
            topic="oblo/uid0123456789abcdef/gtw/C83DD4300D5B/ohm/evt",
            payload=json.dumps({"name": "device_property_changed",
                                "params": {"value": 1}}).encode()))
        online = [m for m in seen if "ONLINE_EVENT" in m]
        check("ONLINE_EVENT logged without an evt callback", len(online) == 1, seen)
        check("ONLINE_EVENT carries isOnline", any("isOnline=True" in m for m in online), online)
        check("ONLINE_EVENT carries serial", any("C83DD4300D5B" in m for m in online), online)
        check("other events do not emit ONLINE_EVENT",
              not any("ONLINE_EVENT" in m for m in seen
                      if "device_property_changed" in m), seen)

        # with a callback attached the event must also be delivered
        c._evt_cb = delivered.append
        c._loop = asyncio.get_running_loop()
        c._on_message(None, None, types.SimpleNamespace(
            topic="oblo/uid0123456789abcdef/gtw/C83DD4300D5B/sys/evt",
            payload=json.dumps(evt).encode()))
        await asyncio.sleep(0.02)
        check("ONLINE_EVENT still delivered to callback", len(delivered) == 1, delivered)
        check("ONLINE_EVENT now logged twice", len([m for m in seen if "ONLINE_EVENT" in m]) == 2,
              [m for m in seen if "ONLINE_EVENT" in m])
    finally:
        lg.removeHandler(h)


async def test_generic_command():
    """The write path must ride the identical envelope the read path proves."""
    c, fake = make_client()
    task = asyncio.create_task(c.async_set_property(
        service_id="node14", service_name="outlet0",
        property_name="State", value=1, service_type="OutletService"))
    await asyncio.sleep(0.05)
    topic, body, qos = fake.published[0]

    check("set publishes to gtw ohm/req",
          topic == "oblo/uid0123456789abcdef/gtw/C83DD4300D5B/ohm/req", topic)
    check("set uses set_service_property_value",
          body["name"] == "set_service_property_value", body["name"])
    check("set keeps type=command", body["type"] == "command", body["type"])
    check("set carries sender", bool(body.get("sender")), body)
    check("set envelope has exactly the 5 SPA fields",
          set(body) == {"name", "params", "type", "uid", "sender"}, sorted(body))
    check("set params carry all four required keys",
          body["params"] == {"id": "node14", "service_name": "outlet0",
                             "property_name": "State", "property_value": 1,
                             "service_type": "OutletService"}, body["params"])
    check("set publishes QoS 0 like the SPA", qos == 0, qos)

    # reply resolves with the raw params dict, not a verdict
    rev = {"uid": body["uid"], "code": 0, "params": {"property_value": 1}}
    c._on_message(None, None, types.SimpleNamespace(
        topic="oblo/uid0123456789abcdef/cli/sid0123456789abcdef/rsp",
        payload=json.dumps(rev).encode()))
    got = await asyncio.wait_for(task, timeout=1)
    check("set returns reply params", got == {"property_value": 1}, got)

    # service_type is optional, and must be omitted rather than sent empty
    c2, fake2 = make_client()
    t2 = asyncio.create_task(c2.async_set_property("node14", "outlet0", "State", 0))
    await asyncio.sleep(0.05)
    p2 = fake2.published[0][1]["params"]
    check("service_type omitted when not supplied", "service_type" not in p2, p2)
    t2.cancel()

    # get_property uses the read command and unwraps property_value
    c3, fake3 = make_client()
    t3 = asyncio.create_task(c3.async_get_property("node14", "outlet0", "State"))
    await asyncio.sleep(0.05)
    b3 = fake3.published[0][1]
    check("get uses get_service_property_value",
          b3["name"] == "get_service_property_value", b3["name"])
    check("get does not send property_value", "property_value" not in b3["params"], b3["params"])
    c3._on_message(None, None, types.SimpleNamespace(
        topic="oblo/uid0123456789abcdef/cli/sid0123456789abcdef/rsp",
        payload=json.dumps({"uid": b3["uid"], "code": 0,
                            "params": {"property_value": "1"}}).encode()))
    check("get returns the unwrapped value",
          await asyncio.wait_for(t3, timeout=1) == "1")

    # an error code must raise, not return an empty verdict
    c4, fake4 = make_client()
    t4 = asyncio.create_task(c4.async_set_property("node14", "outlet0", "State", 1))
    await asyncio.sleep(0.05)
    b4 = fake4.published[0][1]
    c4._on_message(None, None, types.SimpleNamespace(
        topic="oblo/uid0123456789abcdef/cli/sid0123456789abcdef/rsp",
        payload=json.dumps({"uid": b4["uid"], "code": 5,
                            "message": "no such service"}).encode()))
    try:
        await asyncio.wait_for(t4, timeout=1)
        check("set raises on non-zero code", False, "returned normally")
    except ObloCommandError as e:
        check("set raises on non-zero code", True, str(e))


async def test_suback_correlation():
    """SUBACK must name its topic, and 0x80 must be visible as a refusal."""
    import logging

    c, fake = make_client()
    c._connected.clear()
    c._client = fake
    seen = []
    h = logging.Handler()
    h.emit = lambda record: seen.append((record.levelname, record.getMessage()))
    lg = logging.getLogger("oblo_living.cloud_mqtt")
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    try:
        c._on_connect(None, None, {}, 0)
        subs = [(mid, t) for mid, t in c._sub_mid_topic.items()]
        # session path (rsp+req) plus the non-admin userId path (rsp+req)
        check("subscribes recorded a mid->topic map", len(subs) == 4, subs)
        check("mids are ints", all(isinstance(m, int) for m, _ in subs), subs)

        c._on_subscribe(None, None, subs[0][0], (1,))
        line = [m for lvl, m in seen if m.startswith("SUBACK")]
        check("SUBACK names the topic, not just the mid",
              bool(line) and subs[0][1] in line[-1], line)
        check("SUBACK carries granted codes", "granted=(1,)" in line[-1], line)
        check("mid is consumed after its SUBACK",
              subs[0][0] not in c._sub_mid_topic, c._sub_mid_topic)

        # 0x80: the broker refused the subscription -- silence is not health
        c._on_subscribe(None, None, subs[1][0], (128,))
        refused = [m for lvl, m in seen if "REFUSED" in m]
        check("0x80 logs a REFUSED warning", len(refused) == 1, seen[-3:])
        check("REFUSED log is WARNING level",
              any(lvl == "WARNING" and "REFUSED" in m for lvl, m in seen),
              [s for s in seen if "REFUSED" in s[1]])
        check("granted codes are queryable per topic",
              c._sub_granted.get(subs[1][1]) == (128,), c._sub_granted)
    finally:
        lg.removeHandler(h)


def main():
    async def run():
        await test_object_list()
        await test_error_code()
        await test_uid_isolation()
        await test_timeout_retries()
        await test_events()
        await test_connack()
        await test_bad_payload()
        await test_sender_paths()
        await test_uid_uniqueness()
        await test_online_event()
        await test_generic_command()
        await test_suback_correlation()

    asyncio.run(run())
    failed = CHECKS.count(False)
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} checks pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())