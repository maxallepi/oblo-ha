"""Tests for poll-rate guards: interval clamping and 429 backoff.

Context: the OBLO cloud blocks an ACCOUNT (HTTP 429 / errorCode 000016) after
too many requests, and every poll is three round trips. Two ways this integration
could still hammer it:

1. The options flow clamps 15-3600 s, but ObloDataCoordinator's constructor takes
   `scan_interval` straight from entry.options -- an options dict written by an
   older build (or by hand in .storage) carried 5 s, so setup used 5 s.
2. On a 429 the poll returned cached data and kept the SAME interval, so a
   blocked account kept hitting the endpoint that had just blocked it, which is
   how a 15-minute block becomes a longer one.

Covers:
- constructor clamps below-floor and above-ceiling, keeps valid values
- set_scan_interval clamps and records the user's configured interval
- 429 raises the interval (2x), capped at 300 s, returns cached devices
- consecutive 429s keep doubling until the cap
- a successful poll restores the configured interval and clears the streak
- a generic cloud error still becomes UpdateFailed (not swallowed)
- re-login path restores the interval on success

Run: python3 custom_components/oblo_living/tests/poll_rate_guard_test.py
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

from custom_components.oblo_living.coordinator import ObloDataCoordinator  # noqa: E402
from custom_components.oblo_living.cloud_api import (  # noqa: E402
    ObloAuthError, ObloCloudError, ObloRateLimitError,
)
from custom_components.oblo_living.const import (  # noqa: E402
    MAX_SCAN_INTERVAL_S, MIN_SCAN_INTERVAL_S,
)

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}"
          + (f"  <- {detail}" if detail and not cond else ""))


def make_coordinator(scan_interval=60):
    hass = type("H", (), {"loop": asyncio.new_event_loop()})()
    return ObloDataCoordinator(hass, api=None, scan_interval=scan_interval,
                               entry_id="test_entry")


def interval_of(coord):
    return int(coord.update_interval.total_seconds())


CACHED = [{"id": "1052", "name": "Hallway Lights", "services": []}]


def run_update(coord, fetch_impl):
    """Run one _async_update_data cycle with a scripted _async_fetch."""
    async def fetch():
        return await fetch_impl()

    coord._async_fetch = fetch
    return asyncio.get_event_loop().run_until_complete(
        coord._async_update_data())


loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# ----- 1. constructor clamping ---------------------------------------------

print("\n--- constructor clamps scan_interval ---")
c = make_coordinator(5)
check("scan_interval=5 -> 15 s", interval_of(c) == MIN_SCAN_INTERVAL_S,
      f"interval={interval_of(c)}")
check("configured_interval_s records the clamp", c.configured_interval_s == 15,
      f"configured={c.configured_interval_s}")

c = make_coordinator(99999)
check("scan_interval=99999 -> 3600 s", interval_of(c) == MAX_SCAN_INTERVAL_S,
      f"interval={interval_of(c)}")

c = make_coordinator(60)
check("scan_interval=60 kept", interval_of(c) == 60, f"interval={interval_of(c)}")
check("3600 s stays the ceiling constant", MAX_SCAN_INTERVAL_S == 3600,
      f"MAX={MAX_SCAN_INTERVAL_S}")

# ----- 2. set_scan_interval ------------------------------------------------

print("\n--- set_scan_interval ---")
c = make_coordinator(60)
returned = c.set_scan_interval(5)
check("set_scan_interval(5) returns 15", returned == 15, f"returned={returned}")
check("set_scan_interval(5) clamps the interval", interval_of(c) == 15,
      f"interval={interval_of(c)}")
check("set_scan_interval(5) records configured=15",
      c.configured_interval_s == 15, f"configured={c.configured_interval_s}")

# ----- 3. 429 backoff ------------------------------------------------------

print("\n--- 429 -> backoff ---")

async def raise_rl():
    raise ObloRateLimitError("too many attempts (000016)")

c = make_coordinator(60)
c.devices = list(CACHED)
out = run_update(c, raise_rl)
check("429 does not raise", isinstance(out, list), f"out={type(out).__name__}")
check("429 returns cached devices", out == CACHED, f"out={out!r}")
check("429 doubles 60 -> 120", interval_of(c) == 120, f"interval={interval_of(c)}")
check("429 streak is 1", c._rate_limit_streak == 1,
      f"streak={getattr(c, '_rate_limit_streak', None)}")
check("configured interval untouched by backoff",
      c.configured_interval_s == 60, f"configured={c.configured_interval_s}")

out = run_update(c, raise_rl)
check("second 429 doubles 120 -> 240", interval_of(c) == 240,
      f"interval={interval_of(c)}")
out = run_update(c, raise_rl)
check("third 429 caps at 300", interval_of(c) == 300,
      f"interval={interval_of(c)}")
out = run_update(c, raise_rl)
check("fourth 429 stays at cap 300", interval_of(c) == 300,
      f"interval={interval_of(c)}")

# ----- 4. success restores ------------------------------------------------

print("\n--- success restores configured interval ---")

async def ok():
    return list(CACHED)

c = make_coordinator(60)
run_update(c, raise_rl)          # 60 -> 120
check("after 429 interval is 120", interval_of(c) == 120,
      f"interval={interval_of(c)}")
out = run_update(c, ok)
check("after success interval restored to 60", interval_of(c) == 60,
      f"interval={interval_of(c)}")
check("streak cleared", getattr(c, "_rate_limit_streak", 0) == 0,
      f"streak={getattr(c, '_rate_limit_streak', None)}")
check("success returns fresh data", out == CACHED, f"out={out!r}")

# ----- 5. floor respected when starting at the floor ----------------------

print("\n--- backoff from the floor ---")
c = make_coordinator(15)
run_update(c, raise_rl)
check("15 s floor doubles to 30 (not below floor)", interval_of(c) == 30,
      f"interval={interval_of(c)}")

# ----- 6. generic cloud error still fails the poll ------------------------

print("\n--- generic cloud error ---")

async def boom():
    raise ObloCloudError("HTTP 500")

c = make_coordinator(60)
try:
    run_update(c, boom)
    raised = None
except Exception as err:      # noqa: BLE001 - we want the type
    raised = err
check("ObloCloudError becomes UpdateFailed",
      raised is not None and type(raised).__name__ == "UpdateFailed",
      f"raised={type(raised).__name__ if raised else None}")
check("failed poll does not change the interval", interval_of(c) == 60,
      f"interval={interval_of(c)}")

# ----- 7. re-login path restores -----------------------------------------

print("\n--- re-login success restores interval ---")

async def expired():
    raise ObloAuthError("session expired")

async def relogin_ok():
    return list(CACHED)

c = make_coordinator(60)
run_update(c, raise_rl)              # streak 1, interval 120
c._async_refresh_relogin = relogin_ok
out = run_update(c, expired)
check("re-login success returns data", out == CACHED, f"out={out!r}")
check("re-login success restores 60", interval_of(c) == 60,
      f"interval={interval_of(c)}")

# ----- summary ------------------------------------------------------------

print()
passed = sum(1 for _, ok_, _ in CHECKS if ok_)
failed = [x for x in CHECKS if not x[1]]
print(f"{passed}/{len(CHECKS)} checks passed")
if failed:
    for label, _, detail in failed:
        print(f"  FAILED: {label}" + (f"  <- {detail}" if detail else ""))
    sys.exit(1)
print("ALL CHECKS PASSED")
