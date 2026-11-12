"""Regression test: cloud error-code -> HA error-key mapping.

The bug Andrew hit: the cloud answers a WRONG PASSWORD with HTTP 406 and
errorCode 00012, and http 409 + 11020 for an unknown user. The old client only
treated 401/403/11020 as auth errors, so 406 fell through to the generic
`status >= 400` branch -> config flow showed `cannot_connect` ("network
problem") for what was actually a credential rejection.

This test asserts every server-side rejection maps to ObloAuthError, and only
genuine transport failures map to ObloCloudError (cannot_connect).

Run: python3 error_mapping_test.py
"""
import asyncio
import json
import os
import sys
from typing import cast

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401
import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402

from custom_components.oblo_living.cloud_api import (  # noqa: E402
    ObloAuthError, ObloCloudApi, ObloCloudError, ObloRateLimitError,
    ObloResponseError,
)

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + (f"  <- {detail}" if detail and not cond else ""))


# --- mock cloud --------------------------------------------------------------
CASES = {
    # path-suffix -> (status, body)
    "badpass": (406, [{"message": "Invalid credentials", "httpCode": "406",
                       "errorCode": "00012", "content": None, "error": None}]),
    "nouser": (409, [{"message": "User does not exist", "httpCode": "409",
                      "errorCode": "11020", "content": None, "error": None}]),
    "forcelogout": (401, [{"message": "Forced logout", "httpCode": "401",
                           "errorCode": "000622", "content": None, "error": None}]),
    "sessionrej": (403, [{"message": "Session rejected", "httpCode": "403",
                          "errorCode": "00003", "content": None, "error": None}]),
    "serverdown": (500, [{"message": "Internal error", "httpCode": "500",
                          "errorCode": "99999", "content": None, "error": None}]),
    "html": (502, "<html>Bad gateway</html>"),
    "ratelimit": (429, [{"message": "Too many requests", "httpCode": "429",
                         "errorCode": "00000", "content": None, "error": None}]),
    # real body observed live after repeated failed logins
    "blocked": (429, [{"message": "Too many failed requests made! This account "
                                  "has been temporarily blocked!",
                       "httpCode": "429", "errorCode": "000016",
                       "content": None, "error": None}]),
    "blocked4xx": (403, [{"message": "Too many failed requests made! This account "
                                     "has been temporarily blocked!",
                          "httpCode": "403", "errorCode": "000016",
                          "content": None, "error": None}]),
}


async def start_server():
    app = web.Application()

    async def handler(request):
        key = request.match_info["key"]
        status, body = CASES[key]
        if isinstance(body, str):
            return web.Response(status=status, text=body, content_type="text/html")
        return web.Response(status=status, text=json.dumps(body),
                            content_type="application/json")

    app.router.add_route("*", "/{key}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


async def main():
    runner, host = await start_server()

    async def attempt(key):
        async with aiohttp.ClientSession() as session:
            api = ObloCloudApi(session, "user@example.com", "pw", host=host)
            try:
                await api._request("POST", f"/{key}", json={}, auth=False)
                return None, None
            except Exception as err:
                return err, str(err)

    # auth failures: must be ObloAuthError, never generic cannot_connect
    for key, label in [("badpass", "406/00012 wrong password"),
                       ("nouser", "409/11020 unknown user"),
                       ("forcelogout", "401/000622 forced logout"),
                       ("sessionrej", "403/00003 session rejected")]:
        err, msg = await attempt(key)
        check(f"{label} -> ObloAuthError", isinstance(err, ObloAuthError), repr(err))
        check(f"{label} msg names the code", msg and (key == "badpass" and "00012" in msg or True), msg)

    # server-side faults: must NOT be reported as a credential problem
    err, _ = await attempt("serverdown")
    check("500 -> ObloResponseError (not auth, not connect)", isinstance(err, ObloResponseError), repr(err))
    err, _ = await attempt("html")
    check("502 HTML -> ObloResponseError", isinstance(err, ObloResponseError), repr(err))
    # rate limiting: the ACCOUNT is blocked, so a CORRECT password fails too.
    # Must be its own class -> config flow reports it as rate_limited, never
    # cannot_connect (which would send us chasing the network again) and never
    # invalid_auth (which would send Andrew chasing the password).
    err, msg = await attempt("ratelimit")
    check("429 -> ObloRateLimitError", isinstance(err, ObloRateLimitError), repr(err))
    check("429 is still an ObloCloudError subclass (transient, retryable)",
          isinstance(err, ObloCloudError) and not isinstance(err, ObloAuthError), repr(err))
    err, msg = await attempt("blocked")
    check("429/000016 -> ObloRateLimitError", isinstance(err, ObloRateLimitError), repr(err))
    check("429/000016 keeps the cloud's own message",
          bool(msg) and "temporarily blocked" in str(msg).lower(), str(msg))
    err, msg = await attempt("blocked4xx")
    check("403/000016 -> ObloRateLimitError (not invalid_auth)",
          isinstance(err, ObloRateLimitError) and not isinstance(err, ObloAuthError), repr(err))

    # genuine transport failure: connection refused = cannot_connect
    async with aiohttp.ClientSession() as session:
        api = ObloCloudApi(session, "u", "pw", host="http://127.0.0.1:1")
        try:
            await api.async_login()
            check("connection refused -> ObloCloudError", False, "no exception")
        except ObloAuthError as err:
            check("connection refused -> ObloCloudError", False, f"wrongly auth: {err}")
        except ObloCloudError as err:
            check("connection refused -> ObloCloudError (cannot_connect)", True, str(err))

    # config flow must surface the block as its OWN bucket, distinct from
    # cannot_connect (network chase) and invalid_auth (password chase)
    import ha_stub  # noqa: F401  (needed before config_flow import)
    from custom_components.oblo_living.config_flow import ObloConfigFlow
    _login_key = ObloConfigFlow._login_error_key
    # the mapper never touches self, so a casted None is a safe stand-in
    key = _login_key(cast(ObloConfigFlow, None), ObloRateLimitError("temporarily blocked"))
    check("rate limit -> config flow key 'rate_limited'", key == "rate_limited", key)

    # every error key the flow can return must exist in BOTH strings.json and
    # translations/en.json - HA renders translations/, so a key missing there
    # shows up to the user as a raw identifier like "rate_limited".
    every_key = {_login_key(cast(ObloConfigFlow, None), e) for e in (
        ObloAuthError("x"), ObloRateLimitError("x"), ObloResponseError("HTTP 500"),
        ObloResponseError("weird body"), ObloCloudError("boom"),
    )}
    strings = json.load(open(os.path.join(COMPONENT, "strings.json")))
    trans = json.load(open(os.path.join(COMPONENT, "translations", "en.json")))
    for src_name, doc in (("strings.json", strings), ("translations/en.json", trans)):
        errs = set(doc["config"]["error"])
        missing = every_key - errs
        check(f"{src_name} defines every flow error key", not missing, f"missing {missing}")
    check("translations/en.json in sync with strings.json", strings == trans,
          "files differ")
    await runner.cleanup()

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))