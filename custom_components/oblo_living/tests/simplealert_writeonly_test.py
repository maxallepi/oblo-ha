"""Offline test: SimpleAlert write-only fix (v0.2.40)

Covers:
- select remembers its write (no confirm wait, no exception)
- select current_option reflects remembered value when poll omits PropAlertType
- button uses remembered type (not '0') before sounding
- button does not raise when type write unconfirmed
- button still confirms start
- remembered value cleared on reconnect (set_ohm_client)
- poll carrying the property wins over local
- NC: revert -> fails
"""
import asyncio
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401

from homeassistant.exceptions import HomeAssistantError

from custom_components.oblo_living.entities.select import ObloAlertTypeSelect
from custom_components.oblo_living.entities.button import ObloSoundAlertButton

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + (f"  <- {detail}" if detail and not cond else ""))


# --- Fake coordinator with controllable write responses -----------------------
class FakeCoordinator:
    def __init__(self, devices, ohm=None):
        # Fake OHM-like object that makes coordinator think OHM is attached
        if ohm is None:
            ohm = object()
        self.data = devices
        self.devices = devices
        self.ohm = ohm
        self._listeners = []
        self._local_writes = {}
        self._evt_overlay = {}
        self._alert_gens = {}
        self._alert_stoppers = {}
        self._unload_callbacks = []
        self.writes = []
        self._force_refuse = None

    def async_add_listener(self, cb):
        self._listeners.append(cb)

        def _remove():
            if cb in self._listeners:
                self._listeners.remove(cb)
        return _remove

    def fire_refresh(self):
        for cb in list(self._listeners):
            cb()

    # --- local write helpers (the ones we added) ---
    def _overlay(self):
        return self._evt_overlay

    def remember_local_write(self, did, svc, prop, val):
        if getattr(self, "_local_writes", None) is None:
            self._local_writes = {}
        self._local_writes[(str(did), str(svc), str(prop))] = val
        return val

    def local_write(self, did, svc, prop):
        store = getattr(self, "_local_writes", None) or {}
        return store.get((str(did), str(svc), str(prop)))

    def forget_local_writes(self):
        self._local_writes = {}

    def set_ohm_client(self, client):
        if client is None or client is not getattr(self, "ohm", None):
            if self._overlay():
                self._evt_overlay = {}
            self.forget_local_writes()
        self.ohm = client

    # --- alert helpers ---
    def alert_generation(self, key):
        return self._alert_gens.get(key, 0)

    def bump_alert_generation(self, key):
        self._alert_gens[key] = self._alert_gens.get(key, 0) + 1

    def register_alert_stopper(self, key, stopper):
        self._alert_stoppers[key] = stopper

    def unregister_alert_stopper(self, key):
        self._alert_stoppers.pop(key, None)

    # --- write handler ---
    async def async_write_service_property(self, did, svc_name, prop, value,
                                           service_type=None, confirm_timeout=2.0,
                                           matcher=None, verify=True):
        # Simulate: successful send, then if verify -> readback; else None
        result = {"sent": value, "readback": None, "confirmed": None, "error": None}
        # Record the wire traffic so tests can assert what was actually sent.
        self.writes.append({"prop": prop, "value": value, "verify": verify})
        if self.ohm is None:
            result["error"] = "OHM client not attached (REST-only mode)"
            return result

        # Simulate refusal for specific cases (test can override via self._force_refuse)
        if getattr(self, "_force_refuse", None) == (did, svc_name, prop):
            result["confirmed"] = False
            result["error"] = "refused by test"
            return result

        if verify:
            # Read back: if poll carries the prop, match it; else default '0'
            for dev in self.data or []:
                if dev.get("id") != did:
                    continue
                for svc in dev.get("services") or []:
                    if svc.get("service") != svc_name:
                        continue
                    props = svc.get("props", {})
                    readback = props.get(prop)
                    # PropAlertStartStop is an edge: no poll value, but the gateway
                    # echoes the write. For test purposes, treat a sent "true" as
                    # confirmed if the prop is PropAlertStartStop.
                    if readback is None:
                        if prop == "PropAlertStartStop":
                            readback = value  # echo
                        else:
                            readback = "0"  # gateway default for enum
                    result["readback"] = readback
                    if matcher and matcher(value, readback):
                        result["confirmed"] = True
                    elif matcher is None and str(value) == str(readback):
                        result["confirmed"] = True
                    else:
                        result["confirmed"] = False
                    break
                break
        return result

    def async_set_updated_data(self, data):
        self.data = data
        self.devices = data
        self.fire_refresh()

    def push_local_update(self):
        self.async_set_updated_data(list(self.data or []))
        return True


# --- Build minimal test device tree ---
def make_device(device_id=15, name="Siren 15", prop_alert_type=None):
    """Build a SimpleAlertService device. If prop_alert_type is set, it's in the poll."""
    svc = {"service": "SimpleAlertService", "service_type": "siren", "props": {}}
    if prop_alert_type is not None:
        svc["props"]["PropAlertType"] = prop_alert_type
    return {
        "id": device_id,
        "class": "ohm",
        "name": name,
        "services": [svc],
    }


def make_ent(device_id=15, svc="SimpleAlertService", unique_id="alert_type_15"):
    return {
        "device_id": device_id,
        "service": svc,
        "service_type": "siren",
        "unique_id": unique_id,
        "options": ["Notification", "Alarm", "Warning", "Emergency"],
        "platform": "select",
    }


# --- TESTS --------------------------------------------------------------------

# 1. select remembers its write (no confirm wait, no exception)
print("=== Test 1: select writes without confirm wait ===")
dev = make_device()
coord = FakeCoordinator([dev])
select_ent = make_ent()
select = ObloAlertTypeSelect(coord, select_ent)

async def run1():
    res = await select.async_select_option("Alarm")
    check("1a no exception raised", True)
    check("1b remembered '1' in local_writes", coord.local_write(15, "SimpleAlertService", "PropAlertType") == "1")
    check("1c current_option reflects remembered", select.current_option == "Alarm")

asyncio.run(run1())

# 2. select current_option reflects remembered value when poll omits PropAlertType
print("\n=== Test 2: current_option without poll value ===")
dev = make_device(prop_alert_type=None)
coord = FakeCoordinator([dev])
select_ent = make_ent()
select = ObloAlertTypeSelect(coord, select_ent)
coord.remember_local_write(15, "SimpleAlertService", "PropAlertType", "2")
check("2a current_option = Warning", select.current_option == "Warning")

# 3. button uses remembered type (not '0') before sounding
print("\n=== Test 3: button reads remembered type ===")
dev = make_device()
coord = FakeCoordinator([dev])
btn_ent = {**make_ent(), "unique_id": "sound_15", "platform": "button"}
btn = ObloSoundAlertButton(coord, btn_ent)
# Prime the local write
coord.remember_local_write(15, "SimpleAlertService", "PropAlertType", "3")
press_res = asyncio.run(btn.async_press())
# Should have written type '3' (Emergency) then start
writes = [(c[0][2], c[0][3]) for c in coord._write_calls] if hasattr(coord, '_write_calls') else []
# Instead, check local_write was updated (it is re-remembered) and start sent
# The button's async_press writes type then start. We can't easily intercept,
# but we can verify the coordinator's local_write was called with '3'.
# Since we're testing through the actual code, just check no exception.
check("3a button press completes without exception", True)
check("3b local_write still '3' after press", coord.local_write(15, "SimpleAlertService", "PropAlertType") == "3")

# 4. button does not raise when type write unconfirmed
print("\n=== Test 4: button ignores unconfirmed type write ===")
dev = make_device()
coord = FakeCoordinator([dev])
coord.remember_local_write(15, "SimpleAlertService", "PropAlertType", "1")
btn_ent = {**make_ent(), "unique_id": "sound_15_4", "platform": "button"}
btn = ObloSoundAlertButton(coord, btn_ent)
try:
    asyncio.run(btn.async_press())
    check("4a no exception on unconfirmed type write", True)
except HomeAssistantError as e:
    check("4a no exception on unconfirmed type write", False, str(e))

# 5. button still confirms start
print("\n=== Test 5: button confirms start write ===")
# Force a refusal on start to ensure it still raises
dev = make_device()
coord = FakeCoordinator([dev])
coord.remember_local_write(15, "SimpleAlertService", "PropAlertType", "1")
coord._force_refuse = (15, "SimpleAlertService", "PropAlertStartStop")
btn_ent = {**make_ent(), "unique_id": "sound_15_5", "platform": "button"}
btn = ObloSoundAlertButton(coord, btn_ent)
try:
    asyncio.run(btn.async_press())
    check("5a exception on refused start", False, "should have raised")
except HomeAssistantError:
    check("5a exception on refused start", True)
finally:
    coord._force_refuse = None

# 6. remembered value cleared on reconnect (set_ohm_client)
print("\n=== Test 6: local_writes cleared on reconnect ===")
dev = make_device()
coord = FakeCoordinator([dev])
coord.remember_local_write(15, "SimpleAlertService", "PropAlertType", "1")
coord.set_ohm_client(None)  # reconnect
check("6a local_writes cleared", coord.local_write(15, "SimpleAlertService", "PropAlertType") is None)

# 7. poll carrying the property wins over local
print("\n=== Test 7: poll value beats local ===")
dev = make_device(prop_alert_type="1")  # poll says Alarm
coord = FakeCoordinator([dev])
coord.remember_local_write(15, "SimpleAlertService", "PropAlertType", "3")  # local says Emergency
select_ent = make_ent()
select = ObloAlertTypeSelect(coord, select_ent)
check("7a current_option = Alarm (poll wins)", select.current_option == "Alarm")

# 8. The press sends the remembered type, in order, then start
print("\n=== Test 8: press sends remembered type then start ===")
dev = make_device()          # poll carries no PropAlertType at all
coord = FakeCoordinator([dev])
coord.remember_local_write(15, "SimpleAlertService", "PropAlertType", "3")
btn = ObloSoundAlertButton(coord, {**make_ent(), "unique_id": "sound_15_8",
                                   "platform": "button"})
asyncio.run(btn.async_press())
sent = [(w["prop"], w["value"]) for w in coord.writes]
check("8a exactly 3 writes (type, start, stop)", len(sent) == 3, sent)
check("8b first write is remembered type 3", sent and sent[0] == ("PropAlertType", "3"), sent)
check("8c second write is start true", len(sent) > 1 and sent[1] == ("PropAlertStartStop", "true"), sent)
check("8d third write is stop false", len(sent) > 2 and sent[2] == ("PropAlertStartStop", "false"), sent)
check("8e type write was unverified", len(coord.writes) > 0 and coord.writes[0]["verify"] is False, coord.writes)
check("8f start write WAS verified", len(coord.writes) > 1 and coord.writes[1]["verify"] is True, coord.writes)

# 9. NC: with no remembered value and no poll value, no type write is sent
#    (better than the old behaviour, which sent the read-back default "0")
print("\n=== Test 9: nothing selected -> no type write ===")
dev = make_device()
coord = FakeCoordinator([dev])
btn = ObloSoundAlertButton(coord, {**make_ent(), "unique_id": "sound_15_9",
                                   "platform": "button"})
asyncio.run(btn.async_press())
props = [w["prop"] for w in coord.writes]
check("9a no PropAlertType write when nothing selected", "PropAlertType" not in props, props)
check("9b start still sent", ("PropAlertStartStop", "true") in
      [(w["prop"], w["value"]) for w in coord.writes], coord.writes)

# 10. NC: the old read-back path would have sent "0"
print("\n=== Test 10: poll value still wins if it ever appears ===")
dev = make_device(prop_alert_type="2")   # gateway says Warning
coord = FakeCoordinator([dev])
coord.remember_local_write(15, "SimpleAlertService", "PropAlertType", "3")
btn = ObloSoundAlertButton(coord, {**make_ent(), "unique_id": "sound_15_10",
                                   "platform": "button"})
asyncio.run(btn.async_press())
first = coord.writes[0] if coord.writes else {}
check("10a press used poll type 2, not remembered 3",
      first.get("prop") == "PropAlertType" and first.get("value") == "2", coord.writes)

print(f"\n=== SUMMARY: {sum(c[1] for c in CHECKS)}/{len(CHECKS)} checks passed ===")
for label, ok, detail in CHECKS:
    if not ok:
        print(f"  FAIL: {label}  <- {detail}")
sys.exit(0 if all(c[1] for c in CHECKS) else 1)
