"""End-to-end test of cloud_api against a local mock of cloud.oblo.rs.

The mock reproduces exactly what the SPA (app.js) expects, so this test
proves the client speaks the same protocol:

  * POST /auth/login  -> session id in the **sessionid response header**,
                         body = {"user": {...}}
  * secret must be md5(password), identity lower-cased
  * authenticated calls must carry `sessionId: <header value>`

Run: python3 tests/cloud_mock_test.py
Binds 127.0.0.1 only (ephemeral port), never 0.0.0.0.
"""
import asyncio
import hashlib
import json
import os
import sys

from aiohttp import web

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
sys.path.insert(0, COMPONENT)

# --- minimal Home Assistant stub so the component modules import ------------
sys.path.insert(0, HERE)
try:
    import ha_stub  # noqa: F401  (installs stub modules on import)
except Exception as err:  # pragma: no cover - stub problems are loud
    print("WARN: ha_stub not importable:", err)

# make "oblo_living.X" resolve to this component's files
import importlib.util
import types

PKG = "oblo_living"
pkg = types.ModuleType(PKG)
pkg.__path__ = [COMPONENT]
sys.modules[PKG] = pkg

from oblo_living.cloud_api import (  # noqa: E402
    ObloAuthError, ObloCloudApi, ObloCloudError, md5_hex, normalise_devices,
)
from oblo_living.coordinator import merge_devices  # noqa: E402

IDENTITY = "  Andrew@Example.COM "
PASSWORD = "s3cret-pw"
EXPECTED_SECRET = hashlib.md5(PASSWORD.encode()).hexdigest()
SESSION = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"

SEEN = {}

GATEWAY_LIST = {
    "data": [
        {
            "_id": "gw-1",
            "serialNumber": "34_6748115_33_0232421",
            "connectionStatus": "connected",
            "devices": [
                {
                    "deviceId": "dev-100",
                    "deviceName": "Living Room Light",
                    "type": "light",
                    "services": [{"service": "ohm", "props": ["on", "brightness"]}],
                },
                {
                    "deviceId": "dev-101",
                    "deviceName": "Kitchen Switch",
                    "type": "switch",
                    "services": [{"service": "relay", "props": ["state"]}],
                },
            ],
        }
    ]
}

HG_INFO = {
    "data": [
        {
            "_id": "gw-1",
            "serialNumber": "34_6748115_33_0232421",
            "devices": [
                {
                    "deviceId": "dev-100",
                    "deviceName": "Living Room Light",
                    "type": "light",
                    "services": [
                        {"service": "ohm", "props": ["on", "brightness", "ct"]},
                    ],
                }
            ],
        }
    ]
}


async def handle_login(request):
    body = await request.json()
    SEEN["login_body"] = body
    SEEN["login_headers"] = dict(request.headers)
    identity = (body.get("identity") or "")
    if identity != "andrew@example.com":
        return web.json_response(
            [{"message": "User does not exist", "errorCode": "11020", "httpCode": 409}],
            status=409)
    if body.get("secret") != EXPECTED_SECRET:
        return web.json_response(
            [{"message": "Wrong password", "errorCode": "11021", "httpCode": 401}],
            status=401)
    resp = web.json_response({
        "user": {
            "_id": "user-42",
            "username": "34_6748115_33_0232421_abc",
            "groups": ["home"],
            "devices": [],
        }
    })
    resp.headers["sessionid"] = SESSION
    return resp


def _check_session(request):
    got = request.headers.get("sessionId")
    SEEN["auth_header"] = got
    if got != SESSION:
        return web.json_response(
            [{"message": "Session expired", "errorCode": "00003", "httpCode": 401}],
            status=401)
    return None


async def handle_gateway_list(request):
    bad = _check_session(request)
    if bad:
        return bad
    SEEN["list_params"] = dict(request.query)
    return web.json_response(GATEWAY_LIST)


async def handle_hg_info(request):
    bad = _check_session(request)
    if bad:
        return bad
    return web.json_response(HG_INFO)


async def handle_expire(request):
    """Force the next authed call to fail, to exercise re-login."""
    SEEN["expire"] = True
    return web.json_response({"errorCode": "00000"})


async def run():
    app = web.Application()
    app.router.add_post("/auth/login", handle_login)
    app.router.add_get("/udm/device/list", handle_gateway_list)
    app.router.add_get("/udm/device/hg/info/list", handle_hg_info)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    print(f"mock cloud on {base}")

    ok = 0
    fail = 0

    def check(name, cond, detail=""):
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"PASS {name}")
        else:
            fail += 1
            print(f"FAIL {name} {detail}")

    import aiohttp
    async with aiohttp.ClientSession() as sess:
        # ---- 1. happy path login ------------------------------------------
        api = ObloCloudApi(sess, IDENTITY, PASSWORD, host=base)
        user = await api.async_login()
        check("login returns user object", isinstance(user, dict) and user.get("_id") == "user-42")
        check("session id from sessionid header", api.session_id == SESSION, api.session_id)
        check("user_id from body.user._id", api.user_id == "user-42", api.user_id)
        check("username from body.user", api.username == "34_6748115_33_0232421_abc")
        check("identity lower-cased + stripped",
              SEEN["login_body"]["identity"] == "andrew@example.com",
              SEEN["login_body"]["identity"])
        check("secret is md5(password)",
              SEEN["login_body"]["secret"] == EXPECTED_SECRET,
              SEEN["login_body"]["secret"])
        check("privacy/terms flags sent",
              SEEN["login_body"]["isPrivacyPolicyAccepted"] is True
              and SEEN["login_body"]["areTermsOfServiceAccepted"] is True)
        check("externalLogin False", SEEN["login_body"]["externalLogin"] is False)

        # ---- 2. authenticated calls carry sessionId header ----------------
        gateways = await api.async_list_gateways()
        check("gateway list parsed", isinstance(gateways, list) and len(gateways) == 1)
        check("sessionId header sent", SEEN["auth_header"] == SESSION)
        check("gateway list query params",
              SEEN["list_params"].get("owner") == "true"
              and SEEN["list_params"].get("limit") == "100",
              SEEN["list_params"])

        details = await api.async_gateway_devices()
        check("hg/info list parsed", isinstance(details, list) and len(details) == 1)

        # ---- 3. normalise + de-dup ---------------------------------------
        devs = normalise_devices(gateways)
        check("normalise yields 2 devices", len(devs) == 2, [d["id"] for d in devs])
        check("gateway container not a device",
              all(d["id"] != "gw-1" for d in devs), [d["id"] for d in devs])
        by_id = {d["id"]: d for d in devs}
        check("device name/class mapped",
              by_id["dev-100"]["name"] == "Living Room Light"
              and by_id["dev-100"]["class"] == "light")
        check("service props preserved",
              by_id["dev-100"]["services"][0]["props"] == ["on", "brightness"])
        merged = {d["id"]: d for d in merge_devices(
            normalise_devices(gateways), normalise_devices(details))}
        check("details merge keeps richest props (3)",
              len(merged["dev-100"]["services"][0]["props"]) == 3,
              merged["dev-100"]["services"])
        check("merge does not invent devices", len(merged) == 2, sorted(merged))

        # ---- 4. wrong password -> ObloAuthError --------------------------
        bad = ObloCloudApi(sess, "andrew@example.com", "wrong", host=base)
        try:
            await bad.async_login()
            check("wrong password raises", False)
        except ObloAuthError as err:
            check("wrong password raises", True)
            print("     ->", err)

        # ---- 5. unknown identity -> ObloAuthError ------------------------
        ghost = ObloCloudApi(sess, "nobody@example.com", "x", host=base)
        try:
            await ghost.async_login()
            check("unknown identity raises", False)
        except ObloAuthError as err:
            check("unknown identity raises", True)
            print("     ->", err)

        # ---- 6. rejected session -> ObloAuthError, then re-login works ----
        stale = ObloCloudApi(sess, IDENTITY, PASSWORD, host=base)
        await stale.async_login()
        stale.session_id = "STALE"
        try:
            await stale.async_list_gateways()
            check("stale session raises", False)
        except ObloAuthError as err:
            check("stale session raises", True)
            print("     ->", err)
        await stale.async_login()  # what the coordinator does
        again = await stale.async_list_gateways()
        check("re-login recovers", isinstance(again, list) and len(again) == 1)

        # ---- 7. connection failure -> ObloCloudError ---------------------
        dead = ObloCloudApi(sess, IDENTITY, PASSWORD, host="http://127.0.0.1:1")
        try:
            await dead.async_login()
            check("connection refused raises", False)
        except ObloCloudError as err:
            check("connection refused raises", True)
            print("     ->", str(err)[:70])

    await runner.cleanup()
    print(f"\n{ok} passed, {fail} failed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
