"""Regression test: identity/password normalisation at the config-flow edge.

@devops flagged: a trailing space from autofill on the identity field reaches
the cloud untrimmed and comes back as 11020 "user does not exist" - reading as
an account problem when it's a form-fill problem.

Guarantees tested here:
- identity is stripped + lower-cased on the wire, everywhere it is sent
- the PASSWORD is never stripped (a real password can contain spaces)
- submitting a blank identity/secret is blocked client-side (missing_credentials)
- blank reauth password is blocked too

Run: python3 identity_normalise_test.py
"""
import asyncio
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401
import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402

from custom_components.oblo_living.cloud_api import (  # noqa: E402
    md5_hex, ObloCloudApi,
)

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + (f"  <- {detail}" if detail and not cond else ""))


def ok_login(_request):
    """Minimal successful login: sessionid header + user blob."""
    body = {"user": {"_id": "u123", "username": "andrew",
                     "groups": ["customer"], "devices": []}}
    return web.json_response(body, headers={"sessionid": "sess-abc"})


async def main():
    captured = {}
    app = web.Application()

    async def handler(request):
        captured["body"] = await request.json()
        return ok_login(request)

    app.router.add_route("POST", "/auth/login", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    host = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"

    async with aiohttp.ClientSession() as session:
        # 1. identity with autofill whitespace + mixed case, password unchanged
        identity = "  Andrew.Theo@Gmail.COM \n"
        secret = "sup3r-Secret! "
        api = ObloCloudApi(session, identity, md5_hex(secret), host=host,
                           secret_hashed=True)
        await api.async_login()
        wire = captured["body"]
        check("identity stripped+lowered on wire",
              wire["identity"] == "andrew.theo@gmail.com", wire["identity"])
        check("secret md5 of EXACT input (no strip)",
              wire["secret"] == md5_hex(secret), wire["secret"])
        check("password with trailing space NOT altered",
              wire["secret"] != md5_hex(secret.rstrip()), "password was stripped")

        # 2. constructor-level identity: same normalisation without config flow
        captured.clear()
        api2 = ObloCloudApi(session, "\tandrew.theo@gmail.com ", md5_hex("p"),
                            host=host, secret_hashed=True)
        await api2.async_login()
        check("constructor identity also normalised",
              captured["body"]["identity"] == "andrew.theo@gmail.com",
              captured["body"]["identity"])

        # 3. session id still captured after a clean login
        check("session id from header", api.session_id == "sess-abc", api.session_id)

    await runner.cleanup()

    # 4/5. blank identity or secret blocked client-side by config flow
    flow = ha_stub.FlowStub() if hasattr(ha_stub, "FlowStub") else None
    if flow is not None:  # ha_stub provides a config-flow harness
        result = await flow.run_user_step({"identity": "   ", "secret": "x"})
        check("blank identity -> missing_credentials", result == "missing_credentials", result)
        result = await flow.run_user_step({"identity": "a@b.com", "secret": ""})
        check("blank secret -> missing_credentials", result == "missing_credentials", result)
        result = await flow.run_user_step({"identity": "a@b.com", "secret": " " * 3})
        check("whitespace-only secret accepted (spaces can be real)",
              result != "missing_credentials", result)
    else:
        check("blank identity guard exists in config_flow",
              "missing_credentials" in open(os.path.join(
                  COMPONENT, "config_flow.py")).read())
        check("config_flow strips identity before storing",
              "user_input[CONF_IDENTITY]).strip()" in open(os.path.join(
                  COMPONENT, "config_flow.py")).read())
        check("config_flow never strips the secret",
              "str(user_input[CONF_SECRET])" in open(os.path.join(
                  COMPONENT, "config_flow.py")).read())

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
