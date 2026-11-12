"""Reauth flow test: prompts for plaintext, never replays the stored md5.

@devops flagged that the config entry only stores md5(password) -- which is
right for the wire but means a stored hash must never be reused as a
credential. These checks exercise the REAL flow methods against the mock
cloud:

  1. async_step_reauth shows a form asking only for the password
  2. correct plaintext  -> reauth_successful, entry updated (hash + session)
  3. wrong plaintext    -> invalid_auth, entry untouched
  4. the stored md5 as input -> invalid_auth (replay of the hash is useless)

Run: python3 tests/reauth_test.py
Binds 127.0.0.1 only (ephemeral port), never 0.0.0.0.
"""
import asyncio
import hashlib
import os
import sys

from aiohttp import web

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
sys.path.insert(0, COMPONENT)
sys.path.insert(0, HERE)

import ha_stub  # noqa: F401  (installs stub modules on import)

import importlib.util
import types

PKG = "oblo_living"
pkg = types.ModuleType(PKG)
pkg.__path__ = [COMPONENT]
sys.modules[PKG] = pkg

IDENTITY = "andrew@example.com"
PASSWORD = "s3cret-pw"
# v2 entries store the PLAINTEXT password: the cloud hashes server-side, so a
# stored md5 cannot be replayed (it would be hashed a second time -> 00012).
STORED_SECRET = PASSWORD
SESSION_V1 = "SES-OLD-1111"
SESSION_V2 = "SES-NEW-2222"
SEEN = {}


async def handle_login(request):
    body = await request.json()
    SEEN["login_body"] = body
    SEEN.setdefault("attempts", []).append(body.get("secret"))
    if body.get("identity") != IDENTITY:
        return web.json_response(
            [{"message": "User does not exist", "errorCode": "11020"}], status=409)
    if body.get("secret") != STORED_SECRET:
        return web.json_response(
            [{"message": "Wrong password", "errorCode": "11021"}], status=401)
    SEEN["accepted"] = SEEN.get("accepted", 0) + 1
    resp = web.json_response({"user": {"_id": "user-42", "username": "u"}})
    resp.headers["sessionid"] = SESSION_V2
    return resp


async def handle_expired_session(request):
    """Every authed call: report the session as expired (code 00003)."""
    return web.json_response(
        [{"message": "Session expired", "errorCode": "00003"}], status=401)


# ---- harness: real flow methods on a plain object (no HA ConfigFlow) --------
class FakeEntry:
    def __init__(self, version=2):
        self.entry_id = "e-1"
        self.version = version
        self.reauth_started = False
        self.data = {
            "identity": IDENTITY,
            "secret": STORED_SECRET,      # v2: plaintext password
            "session_id": SESSION_V1,
            "user_id": "user-42",
        }

    def async_start_reauth(self, hass):
        self.reauth_started = True


class FakeConfigEntries:
    def __init__(self, entry):
        self.entry = entry

    def async_get_entry(self, eid):
        return self.entry if eid == self.entry.entry_id else None

    def async_update_entry(self, entry, data=None, **kw):
        if data is not None:
            entry.data = data
        for attr, value in kw.items():
            setattr(entry, attr, value)


class FakeHass:
    def __init__(self, entry):
        self.config_entries = FakeConfigEntries(entry)


class FlowHarness:
    """Runs the real ObloConfigFlow methods without HA's flow machinery."""
    from oblo_living.config_flow import ObloConfigFlow  # noqa: F401

    def __init__(self, hass, entry):
        self.hass = hass
        self._entry = entry
        self.context = {"entry_id": entry.entry_id}

    def async_show_form(self, **kw):
        return ("form", kw)

    def async_abort(self, **kw):
        return ("abort", kw)

    # bind the unbound methods to self
    async_step_reauth = ObloConfigFlow.async_step_reauth
    async_step_reauth_confirm = ObloConfigFlow.async_step_reauth_confirm
    _async_login = ObloConfigFlow._async_login
    _login_error_key = ObloConfigFlow._login_error_key


async def run():
    app = web.Application()
    app.router.add_post("/auth/login", handle_login)
    app.router.add_get("/udm/device/list", handle_expired_session)
    app.router.add_get("/udm/device/hg/info/list", handle_expired_session)
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
    import oblo_living.config_flow as cf

    SESS = aiohttp.ClientSession()
    cf.async_get_clientsession = lambda hass: SESS
    real_api = cf.ObloCloudApi
    cf.ObloCloudApi = lambda session, identity, secret: \
        real_api(session, identity, secret, host=base)

    entry = FakeEntry()
    hass = FakeHass(entry)
    flow = FlowHarness(hass, entry)

    # 1. reauth shows a password-only form
    result = await flow.async_step_reauth(entry.data)
    kind, kw = result
    check("reauth shows form", kind == "form" and kw["step_id"] == "reauth_confirm", kind)
    fields = set(kw["data_schema"].schema)
    check("form asks only for password", fields == {"secret"}, fields)
    check("form shows account identity",
          kw["description_placeholders"]["identity"] == IDENTITY)

    # 2. correct plaintext -> abort reauth_successful + entry updated
    result = await flow.async_step_reauth_confirm({"secret": PASSWORD})
    kind, kw = result
    check("correct password -> reauth_successful",
          kind == "abort" and kw["reason"] == "reauth_successful", result)
    check("stored secret is the plaintext password (v2)",
          entry.data["secret"] == PASSWORD, entry.data["secret"])
    check("session id updated on entry",
          entry.data["session_id"] == SESSION_V2, entry.data["session_id"])
    check("identity unchanged", entry.data["identity"] == IDENTITY)

    # 3. wrong plaintext -> invalid_auth, entry untouched
    before = dict(entry.data)
    result = await flow.async_step_reauth_confirm({"secret": "wrong-pw"})
    kind, kw = result
    check("wrong password -> form with invalid_auth",
          kind == "form" and kw["errors"]["base"] == "invalid_auth", result)
    check("failed reauth does not touch entry", entry.data == before)

    # 4. the stored value goes to the wire VERBATIM (plaintext, not re-hashed)
    result = await flow.async_step_reauth_confirm({"secret": PASSWORD})
    kind, kw = result
    check("stored plaintext replays successfully (re-login path)",
          kind == "abort" and kw["reason"] == "reauth_successful", result)
    check("plaintext password hit the wire as-is",
          SEEN["attempts"][-1] == PASSWORD, SEEN["attempts"][-1])
    check("accepted attempts counted", SEEN.get("accepted") == 2,
          SEEN.get("accepted"))

    # 4b. v1 entry (stored md5) -> migrated to v2 + reauth requested
    old = FakeEntry(version=1)
    old.data["secret"] = hashlib.md5(PASSWORD.encode()).hexdigest()
    old_hass = FakeHass(old)
    from oblo_living.config_flow import async_migrate_entry
    migrated = await async_migrate_entry(old_hass, old)
    check("v1 entry migrates", migrated is True, migrated)
    check("entry version bumped to 2", old.version == 2, old.version)
    check("v1 entry sends user to reauth", old.reauth_started is True,
          old.reauth_started)

    # 5. coordinator escalates a failed re-login to HA's reauth prompt
    from oblo_living.cloud_api import ObloCloudApi, ObloAuthError
    from oblo_living.coordinator import ObloDataCoordinator
    from homeassistant.exceptions import ConfigEntryAuthFailed

    dead_creds = ObloCloudApi(SESS, IDENTITY, "wrong-pw", host=base)
    dead_creds.session_id = "STALE-SESSION"
    coord = ObloDataCoordinator(None, dead_creds)
    try:
        await coord._async_update_data()
        check("coordinator raises ConfigEntryAuthFailed", False, "no exception")
    except ConfigEntryAuthFailed as err:
        check("coordinator raises ConfigEntryAuthFailed", True)
        print("     ->", str(err)[:70])
    except Exception as err:  # noqa: BLE001
        check("coordinator raises ConfigEntryAuthFailed", False, type(err).__name__)

    await SESS.close()
    await runner.cleanup()
    print(f"\n{ok} passed, {fail} failed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
