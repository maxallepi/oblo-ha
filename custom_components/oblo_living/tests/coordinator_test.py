"""Coordinator must NOT re-login/retry when the account is blocked (000016).

Regression test for the trap @devops flagged in the v0.2.8 round: a refresh
that hits HTTP 429 / errorCode 000016 inherits the login-block trap. The old
coordinator fell into the generic ObloCloudError branch, re-logged in every
60s poll, and kept the account blocked indefinitely without a human touching
the UI. The fix: ObloRateLimitError must short-circuit BEFORE the re-login and
return the cached device list instead of retrying.

Run: python3 coordinator_test.py
"""
import asyncio
import os
import sys
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401

from custom_components.oblo_living.coordinator import (  # noqa: E402
    ObloDataCoordinator, diag_shape,
)
from custom_components.oblo_living.cloud_api import (  # noqa: E402
    ObloAuthError, ObloCloudError, ObloRateLimitError, normalise_devices,
)

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + (f"  <- {detail}" if detail and not cond else ""))


# --- diag_shape redaction -------------------------------------------------
def test_diag_shape():
    # v0.2.13 raw dump printed secureId + hash. v0.2.17 must not.
    gw = [{"type": "hg", "serialNumber": "SN1", "secureId": "SEED", "hash": "H", "userId": "U"}]
    out = diag_shape(gw)
    check("count present", out["count"] == 1)
    check("keys listed", "secureId" in out["keys"] and "hash" in out["keys"])
    check("no VALUES of secureId/hash", "SEED" not in str(out) and "H" not in str(out))
    check("type present", out["type"] == "hg")
    check("has_devices bool", out["has_devices"] == False)
    check("device_count 0", out["device_count"] == 0)

    # non-gateway object passes through with keys
    dev = [{"deviceId": "d1", "name": "Sensor", "class": "sensor", "services": []}]
    out2 = diag_shape(dev)
    check("dev count 1", out2["count"] == 1)
    check("dev keys", "deviceId" in out2["keys"])

    # empty list
    check("empty list -> count 0", diag_shape([])["count"] == 0)
    check("non-dict item skipped", diag_shape(["x", {"a": 1}])["count"] == 1)


class FakeApi:
    """Records which coordinator paths were taken; raises what we tell it."""

    def __init__(self):
        self.calls = []
        self.gateways = [{"id": "gw1"}, {"id": "gw2"}]
        self.details = [{"id": "gw1", "services": [{"props": [{"n": 1}]}]}]
        self.fetch_error = None      # error from async_list_gateways
        self.relogin_error = None    # error from async_login (re-login only)

    async def async_login(self):
        self.calls.append("login")
        if self.relogin_error:
            raise self.relogin_error
        # a fresh session fixes a plain session-expiry, so stop raising it
        self.fetch_error = None
        return "sid"

    async def async_list_gateways(self):
        self.calls.append("gateways")
        if self.fetch_error:
            raise self.fetch_error
        return self.gateways

    async def async_gateway_devices(self):
        self.calls.append("details")
        if self.fetch_error:
            raise self.fetch_error
        return self.details


async def main():
    test_diag_shape()

    # --- normal cycle populates the cache -------------------------------
    api = FakeApi()
    coord = ObloDataCoordinator(None, api)
    coord.devices = await coord._async_fetch()
    check("normal fetch returns 2 devices", len(coord.devices) == 2,
          f"got {len(coord.devices)}")

    # --- refresh hits 429/000016 ---------------------------------------
    api2 = FakeApi()
    coord2 = ObloDataCoordinator(None, api2)  # type: ignore[arg-type]
    coord2.devices = await coord2._async_fetch()
    check("cache primed", len(coord2.devices) == 2)
    api2.calls.clear()
    api2.fetch_error = ObloRateLimitError("Too many failed requests! Blocked.")
    result = await coord2._async_update_data()
    check("blocked fetch -> cached data returned, no exception",
          result is coord2.devices and len(result) == 2, repr(result))
    check("blocked fetch -> NO re-login attempted", "login" not in api2.calls,
          f"calls={api2.calls}")

    # --- fetch hits plain auth error -> re-login happens (unchanged) ----
    api3 = FakeApi()
    coord3 = ObloDataCoordinator(None, api3)  # type: ignore[arg-type]
    coord3.devices = await coord3._async_fetch()
    api3.calls.clear()
    api3.fetch_error = ObloAuthError("session rejected (00003)")
    result = await coord3._async_update_data()
    check("session error -> re-login then refetch",
          "login" in api3.calls and "gateways" in api3.calls, f"calls={api3.calls}")
    check("session error -> fresh data", len(result) == 2, repr(result))

    # --- fetch hits ObloAuthError, re-login ALSO blocked ----------------
    api4 = FakeApi()
    coord4 = ObloDataCoordinator(None, api4)  # type: ignore[arg-type]
    coord4.devices = await coord4._async_fetch()
    api4.calls.clear()
    api4.fetch_error = ObloAuthError("session expired")
    api4.relogin_error = ObloRateLimitError("blocked again")
    result = await coord4._async_update_data()
    check("blocked re-login -> cached data, no exception",
          result is coord4.devices and len(result) == 2, repr(result))

    # --- fetch hits ObloAuthError, re-login rejected -> ConfigEntryAuthFailed
    api5 = FakeApi()
    coord5 = ObloDataCoordinator(None, api5)  # type: ignore[arg-type]
    coord5.devices = await coord5._async_fetch()
    api5.calls.clear()
    api5.fetch_error = ObloAuthError("session expired")
    api5.relogin_error = ObloAuthError("Invalid credentials (00012)")
    from homeassistant.exceptions import ConfigEntryAuthFailed
    try:
        await coord5._async_update_data()
        check("rejected re-login -> ConfigEntryAuthFailed", False, "no exception")
    except ConfigEntryAuthFailed:
        check("rejected re-login -> ConfigEntryAuthFailed", True)
    except Exception as err:
        check("rejected re-login -> ConfigEntryAuthFailed", False,
              f"wrong exception: {type(err).__name__}: {err}")

    # --- genuine network fault still raises UpdateFailed ----------------
    api6 = FakeApi()
    coord6 = ObloDataCoordinator(None, api6)  # type: ignore[arg-type]
    coord6.devices = await coord6._async_fetch()
    api6.calls.clear()
    api6.fetch_error = ObloCloudError("connection refused")
    from homeassistant.helpers.update_coordinator import UpdateFailed
    try:
        await coord6._async_update_data()
        check("transport error -> UpdateFailed", False, "no exception")
    except UpdateFailed:
        check("transport error -> UpdateFailed", True)
    except Exception as err:
        check("transport error -> UpdateFailed", False,
              f"wrong exception: {type(err).__name__}: {err}")

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))