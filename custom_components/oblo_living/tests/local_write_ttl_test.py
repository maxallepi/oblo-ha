"""Tests for TTL-bounded local-write authority (v0.2.49).

The bug: `_reapply_overlay` refreshed the remembered value from ANY poll that
carried the property, and `ObloSwitch.is_on` read the poll value first. A poll
taken right after a write is stale by definition -- the gateway has not picked
the command up yet -- so a toggle flipped back about one cycle later whenever
gateway lag exceeded the poll interval. `applied=0` in the log was the tell.

The fix: a write we made keeps authority for 2 x poll interval. Inside that
window a disagreeing poll loses (our value is forced into the tree, which is
what the entity actually reads, and the overlay is not refreshed). When the
window expires the gateway wins, so a change made at the wall switch / OBLO app
/ another controller is not overridden forever. A live evt for the prop closes
the window immediately -- an event is fresher than any write we made.

Covers:
- remember_local_write timestamps; local_write_recent honours the window
- default window = 2 x configured_interval_s
- _local_write_wins: no write / agreement / disagreement-recent / expired
- _reapply_overlay: stale poll inside window -> props forced, overlay kept
- _reapply_overlay: after expiry -> gateway value adopted, overlay refreshed
- _reapply_overlay: agreeing poll -> confirmation, nothing counted
- _reapply_overlay: poll omits prop -> remembered value restored (unchanged)
- handle_event closes the window for that prop
- switch.is_on: stale poll inside window -> our value; expired -> poll value
- switch.is_on: poll omits prop -> remembered value even when expired
- switch.is_on: no poll value and no memory -> Unknown (not a lie)

Run: python3 custom_components/oblo_living/tests/local_write_ttl_test.py
"""
import asyncio
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401

from custom_components.oblo_living.coordinator import ObloDataCoordinator  # noqa: E402
from custom_components.oblo_living.entities.switch import ObloSwitch  # noqa: E402

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}"
          + (f"  <- {detail}" if detail and not cond else ""))


def make_coordinator(devices, scan_interval=60):
    hass = type("H", (), {"loop": asyncio.new_event_loop()})()
    coord = ObloDataCoordinator(hass, api=None, entry_id="test_entry",  # type: ignore[arg-type]
                                scan_interval=scan_interval)
    coord.devices = devices
    coord.data = devices
    coord.ohm = object()
    coord.async_set_updated_data = lambda data: None  # test harness
    return coord


def outlet(device_id="1052", state: "str | None" = "1"):
    """A relay whose poll DOES carry State (the flicker scenario)."""
    props = {"DeviceType": "16"}
    if state is not None:
        props["State"] = state
    return {"id": str(device_id), "name": "Hallway Lights",
            "services": [{"service": "plug0", "service_type": "PlugService",
                          "props": props}]}


def ent(device_id="1052", svc="plug0"):
    return {"device_id": str(device_id), "service": svc,
            "service_type": "PlugService", "value_prop": "State",
            "unique_id": f"oblo_{device_id}_{svc}_switch_State"}


def expire(coord, did="1052", svc="plug0", prop="State"):
    """Pretend the write happened long ago."""
    key = (str(did), str(svc), str(prop))
    coord._local_write_ts[key] = time.monotonic() - 10_000


# ----- 1. timestamp bookkeeping ------------------------------------------

print("\n--- local_write_recent ---")
c = make_coordinator([outlet()])
check("nothing written -> not recent", c.local_write_recent("1052", "plug0", "State") is False)
c.remember_local_write("1052", "plug0", "State", "0")
check("just written -> recent", c.local_write_recent("1052", "plug0", "State") is True)
check("default window is 2 x poll interval",
      c.local_write_recent("1052", "plug0", "State", window_s=2 * 60) is True)
c2 = make_coordinator([outlet()], scan_interval=15)
c2.remember_local_write("1052", "plug0", "State", "0")
expire(c2)
check("15s interval: still not recent after a long time",
      c2.local_write_recent("1052", "plug0", "State") is False)
check("explicit window overrides the default",
      c2.local_write_recent("1052", "plug0", "State", window_s=20_000) is True)

# ----- 2. _local_write_wins ----------------------------------------------

print("\n--- _local_write_wins ---")
c = make_coordinator([outlet()])
check("no remembered write -> gateway wins",
      c._local_write_wins("1052", "plug0", "State", "1") is False)
c.remember_local_write("1052", "plug0", "State", "0")
check("agreement -> gateway confirmation, no override",
      c._local_write_wins("1052", "plug0", "State", "0") is False)
check("disagreement inside window -> local wins",
      c._local_write_wins("1052", "plug0", "State", "1") is True)
expire(c)
check("disagreement after window -> gateway wins",
      c._local_write_wins("1052", "plug0", "State", "1") is False)

# ----- 3. _reapply_overlay: the reported bug ----------------------------

print("\n--- _reapply_overlay: stale poll must not fight the write ---")
c = make_coordinator([outlet(state="1")])          # poll says ON (stale)
c.remember_local_write("1052", "plug0", "State", "0")   # we just wrote OFF
c._overlay().setdefault(("1052", "plug0"), {})["State"] = "0"
applied = c._reapply_overlay()
props = c.devices[0]["services"][0]["props"]
check("stale poll: overlay not refreshed", c._overlay()[("1052", "plug0")]["State"] == "0",
      f"overlay={c._overlay()}")
check("stale poll: tree forced to our value (entity reads props)",
      props.get("State") == "0", f"props={props}")
check("stale poll: counted as an application", applied >= 1, f"applied={applied}")

print("\n--- _reapply_overlay: after expiry the gateway wins ---")
c = make_coordinator([outlet(state="1")])
c.remember_local_write("1052", "plug0", "State", "0")
c._overlay().setdefault(("1052", "plug0"), {})["State"] = "0"
expire(c)
c._reapply_overlay()
props = c.devices[0]["services"][0]["props"]
check("expired: tree takes the gateway value", props.get("State") == "1",
      f"props={props}")
check("expired: overlay refreshed from the poll",
      c._overlay()[("1052", "plug0")]["State"] == "1",
      f"overlay={c._overlay()}")

print("\n--- _reapply_overlay: agreeing poll is confirmation ---")
c = make_coordinator([outlet(state="0")])
c.remember_local_write("1052", "plug0", "State", "0")
c._overlay().setdefault(("1052", "plug0"), {})["State"] = "0"
applied = c._reapply_overlay()
props = c.devices[0]["services"][0]["props"]
check("agreement: value unchanged", props.get("State") == "0", f"props={props}")
check("agreement: overlay still holds the value",
      c._overlay()[("1052", "plug0")]["State"] == "0", f"overlay={c._overlay()}")
check("agreement: nothing counted", applied == 0, f"applied={applied}")

print("\n--- _reapply_overlay: omitted prop restored (unchanged behaviour) ---")
c = make_coordinator([outlet(state=None)])
c.remember_local_write("1052", "plug0", "State", "1")
c._overlay().setdefault(("1052", "plug0"), {})["State"] = "1"
expire(c)
applied = c._reapply_overlay()
props = c.devices[0]["services"][0]["props"]
check("omitted prop: restored from overlay", props.get("State") == "1",
      f"props={props}")
check("omitted prop: counted", applied == 1, f"applied={applied}")

# ----- 4. handle_event closes the window --------------------------------

print("\n--- evt closes the authority window ---")
c = make_coordinator([outlet(state="0")])
c.remember_local_write("1052", "plug0", "State", "0")
check("window open before evt",
      c.local_write_recent("1052", "plug0", "State") is True)
c.handle_event({"name": "device_1052_property_changed",
                "device_id": "1052", "service_name": "plug0",
                "property_name": "State", "property_value": "1"})
check("evt closed the window",
      c.local_write_recent("1052", "plug0", "State") is False)
check("evt value applied to the tree",
      c.devices[0]["services"][0]["props"].get("State") == "1",
      f"props={c.devices[0]['services'][0]['props']}")
check("remembered value still available as fallback",
      c.local_write("1052", "plug0", "State") == "0")

# ----- 5. switch entity read path ---------------------------------------

print("\n--- switch.is_on ---")
c = make_coordinator([outlet(state="1")])      # poll: ON (stale)
sw = ObloSwitch(c, ent())
c.remember_local_write("1052", "plug0", "State", "false")   # we wrote OFF
check("is_on: stale poll inside window -> OFF",
      sw.is_on is False, f"is_on={sw.is_on}")

c = make_coordinator([outlet(state="1")])
sw = ObloSwitch(c, ent())
c.remember_local_write("1052", "plug0", "State", "false")
expire(c)
check("is_on: expired window -> poll value wins (ON)",
      sw.is_on is True, f"is_on={sw.is_on}")

c = make_coordinator([outlet(state=None)])     # poll omits State entirely
sw = ObloSwitch(c, ent())
c.remember_local_write("1052", "plug0", "State", "true")
expire(c)
check("is_on: poll omits State -> remembered value even when expired",
      sw.is_on is True, f"is_on={sw.is_on}")

c = make_coordinator([outlet(state=None)])
sw = ObloSwitch(c, ent())
check("is_on: nothing to go on -> Unknown, not a lie",
      sw.is_on is None, f"is_on={sw.is_on}")

print("\n--- forget_local_writes clears timestamps ---")
c = make_coordinator([outlet()])
c.remember_local_write("1052", "plug0", "State", "0")
c.forget_local_writes()
check("reconnect clears the timestamp map",
      c.local_write_recent("1052", "plug0", "State") is False)
check("reconnect clears the remembered value",
      c.local_write("1052", "plug0", "State") is None)

# ----- 6. interaction with 429 backoff ----------------------------------

print("\n--- authority window vs rate-limit backoff ---")
c = make_coordinator([outlet()], scan_interval=60)
check("60s poll -> 120s window", c._authority_window_s() == 120,
      f"window={c._authority_window_s()}")
for _ in range(3):
    c._rate_limit_backoff(RuntimeError("429"))
eff = int(c.update_interval.total_seconds())
check("backoff reached the 300s cap", eff == 300, f"update_interval={eff}")
check("configured_interval_s is still the user's 60s",
      c.configured_interval_s == 60, f"configured={c.configured_interval_s}")
check("window grows with backoff but stops at the 180s cap",
      c._authority_window_s() == 180, f"window={c._authority_window_s()}")
# A poll that arrives after the cap is not stale any more -> gateway wins.
c.remember_local_write("1052", "plug0", "State", "0")
check("inside 180s cap: our write still wins",
      c.local_write_recent("1052", "plug0", "State") is True)
expire(c)
c._local_write_ts[("1052", "plug0", "State")] = time.monotonic() - 200
check("past the 180s cap: gateway wins even mid-backoff",
      c._local_write_wins("1052", "plug0", "State", "1") is False)
c._restore_poll_interval()
check("restore returns to 60s, not the backoff value",
      int(c.update_interval.total_seconds()) == 60,
      f"update_interval={c.update_interval}")
check("window back to 120s after restore", c._authority_window_s() == 120,
      f"window={c._authority_window_s()}")

# ----- 7. _prop_seen + note_stale_resurrection --------------------------

print("\n--- prop_seen + resurrection diagnostic ---")
c = make_coordinator([outlet()], scan_interval=60)
# simulate a poll that reported State for plug0
c._note_seen_props([{"id": "1052", "services": [
    {"name": "plug0", "props": {"DeviceType": "16", "State": "1"}}]}])
check("State seen after poll reports it",
      c.prop_ever_reported("1052", "plug0", "State") is True)
check("never-reported prop returns False",
      c.prop_ever_reported("1052", "plug0", "Power") is False)

# Reconnect clears _prop_seen
c.forget_local_writes()
check("forget_local_writes clears _prop_seen",
      c.prop_ever_reported("1052", "plug0", "State") is False)

# note_stale_resurrection only logs when prop_ever_reported AND window expired
import logging, io
log_stream = io.StringIO()
h = logging.StreamHandler(log_stream)
logging.getLogger("custom_components.oblo_living.coordinator").addHandler(h)
logging.getLogger("custom_components.oblo_living.coordinator").setLevel(logging.WARNING)

c = make_coordinator([outlet(state="1")], scan_interval=60)
c.remember_local_write("1052", "plug0", "State", "0")
expire(c)  # window expired
# Mark as "ever reported"
c._note_seen_props([{"id": "1052", "services": [
    {"name": "plug0", "props": {"State": "1"}}]}])
c.note_stale_resurrection("1052", "plug0", "State")
log = log_stream.getvalue()
check("logs WARNING once for resurrected known prop",
      "stale resurrection" in log.lower() and "plug0" in log, f"log={log!r}")
# Second call should not log again
log_stream.seek(0); log_stream.truncate()
c.note_stale_resurrection("1052", "plug0", "State")
log = log_stream.getvalue()
check("second call suppressed", log == "", f"log={log!r}")

# Never-reported prop should not log at WARNING
log_stream.seek(0); log_stream.truncate()
c.note_stale_resurrection("1052", "plug0", "Power")
log = log_stream.getvalue()
check("never-reported -> no WARNING", log == "", f"log={log!r}")

# --- wiring: is_on's omission path actually calls the diagnostic ---------
log_stream.seek(0); log_stream.truncate()
c2 = make_coordinator([outlet(state=None)], scan_interval=60)
c2._note_seen_props([{"id": "1052", "services": [
    {"name": "plug0", "props": {"State": "1"}}]}])
c2.remember_local_write("1052", "plug0", "State", "0")
expire(c2)
e2 = ObloSwitch(c2, ent())
check("omitted prop -> remembered value still shown", e2.is_on is False)
check("is_on logged the resurrection", "stale resurrection" in log_stream.getvalue().lower(),
      f"log={log_stream.getvalue()!r}")
# and a never-reporting service must NOT log
log_stream.seek(0); log_stream.truncate()
c3 = make_coordinator([outlet(state=None)], scan_interval=60)
c3.remember_local_write("1052", "plug0", "State", "0")
expire(c3)
e3 = ObloSwitch(c3, ent())
check("never-reporting service shows remembered value", e3.is_on is False)
check("never-reporting service stays quiet", log_stream.getvalue() == "",
      f"log={log_stream.getvalue()!r}")

logging.getLogger("custom_components.oblo_living.coordinator").removeHandler(h)

# ----- summary -----------------------------------------------------------

print()
passed = sum(1 for _, ok_, _ in CHECKS if ok_)
failed = [x for x in CHECKS if not x[1]]
print(f"{passed}/{len(CHECKS)} checks passed")
if failed:
    for label, _, detail in failed:
        print(f"  FAILED: {label}" + (f"  <- {detail}" if detail else ""))
    sys.exit(1)
print("ALL CHECKS PASSED")
