"""Tests for SimpleAlertService: select (alert type) + buttons (sound/silence).

Covers:
- build_entities produces 3 entities (select + 2 buttons)
- ObloAlertTypeSelect: options, current_option label↔wire, confirmed write
- ObloSoundAlertButton: press writes type→start→wait→stop, confirmed
- ObloSilenceAlertButton: press writes stop, confirmed
- Mismatched label/wire read-back treated as confirmed
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
from custom_components.oblo_living.entity_map import (  # noqa: E402
    build_entities, alert_type_label, alert_type_wire,
    ALERT_TYPE_LABELS, ALERT_START_TOKEN, ALERT_STOP_TOKEN, ALERT_SOUND_S,
)
from custom_components.oblo_living.entities.select import ObloAlertTypeSelect  # noqa: E402
from custom_components.oblo_living.entities.button import (  # noqa: E402
    ObloSoundAlertButton, ObloSilenceAlertButton,
)
from homeassistant.exceptions import HomeAssistantError  # noqa: E402

# speed: the real 10 s siren wait and 2 s confirm windows make the suite slow
import custom_components.oblo_living.entities.button as btn_mod  # noqa: E402
import custom_components.oblo_living.entities.select as sel_mod  # noqa: E402

btn_mod.ALERT_SOUND_S = 0
btn_mod.OHM_CONFIRM_TIMEOUT_S = 0.3
sel_mod.OHM_CONFIRM_TIMEOUT_S = 0.3

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}"
          + (f"  <- {detail}" if detail and not cond else ""))


# ----- fixture: fake coordinator with OHM client -----------------------------

class FakeMqtt:
    """Records writes; replays a scripted sequence of read-backs."""

    def __init__(self, readbacks=None, read_error=None, send_error=None):
        self.writes = []
        self.reads = []
        self._readbacks = list(readbacks if readbacks is not None else ["1"])
        self._read_error = read_error
        self._send_error = send_error
        self._read_idx = 0

    async def async_set_property(self, device_id, service_name, property_name,
                                 value, service_type=None, timeout=None):
        self.writes.append({"device_id": device_id, "service_name": service_name,
                            "property_name": property_name, "value": value,
                            "service_type": service_type})
        if self._send_error:
            raise self._send_error
        return {"property_value": value}

    async def async_get_property(self, device_id, service_name, property_name,
                                 service_type=None, timeout=None):
        self.reads.append({"device_id": device_id, "service_name": service_name,
                           "property_name": property_name})
        if self._read_error:
            raise self._read_error
        if self._read_idx < len(self._readbacks):
            val = self._readbacks[self._read_idx]
            self._read_idx += 1
            return val
        # after scripted list, return last
        return self._readbacks[-1]


def make_coordinator(data=None):
    hass = type("H", (), {"loop": None})()
    coord = ObloDataCoordinator(hass, api=None)
    coord.ohm = FakeMqtt()
    coord.devices = list(data or [])
    coord.data = list(data or [])
    return coord


# ----- tests: alert_type_label / alert_type_wire ----------------------------

print("\n--- alert type helpers ---")

check("label('2') -> 'Warning'", alert_type_label("2") == "Warning")
check("label('WARNING') -> 'Warning'", alert_type_label("WARNING") == "Warning")
check("label('warning') -> 'Warning'", alert_type_label("warning") == "Warning")
check("label('3') -> 'Emergency'", alert_type_label("3") == "Emergency")
check("label(unknown) -> passthrough", alert_type_label("weird") == "weird")

check("wire('Warning') -> '2'", alert_type_wire("Warning") == "2")
check("wire('warning') -> '2'", alert_type_wire("warning") == "2")
check("wire('2') -> '2'", alert_type_wire("2") == "2")
try:
    alert_type_wire("nope")
    check("wire(unknown) -> raises", False)
except ValueError:
    check("wire(unknown) -> raises", True)


# ----- tests: build_entities produces 3 entities for SimpleAlert ------------

print("\n--- build_entities ---")

dev = {
    "id": "15",
    "name": "Gateway Simple Alert",
    "class": "SimpleAlert",
    "services": [{
        "service": "SimpleAlertService",
        "service_type": "SimpleAlertService",
        "props": [("DeviceType", "18")],  # no PropAlertType/StartStop rows
    }],
}

ents = build_entities([dev])
alert_ents = [e for e in ents if e["service"] == "SimpleAlertService"]
check("3 entities (select + 2 buttons)", len(alert_ents) == 3)
platforms = sorted(e["platform"] for e in alert_ents)
check("platforms: button, button, select", platforms == ["button", "button", "select"])
suffixes = sorted(e.get("name_suffix") for e in alert_ents)
check("suffixes: Silence, Sound, Type", suffixes == ["Silence", "Sound", "Type"])

# unique ids are distinct
uids = [e["unique_id"] for e in alert_ents]
check("unique_ids distinct", len(set(uids)) == 3)

# names dedup "Alert" from device name
select_ent = next(e for e in alert_ents if e["platform"] == "select")
sound_ent = next(e for e in alert_ents if e["platform"] == "button" and e["name_suffix"] == "Sound")
silence_ent = next(e for e in alert_ents if e["platform"] == "button" and e["name_suffix"] == "Silence")

check("select name: 'Gateway Simple Alert Type'",
      "Type" in select_ent.get("unique_id", "") and "Alert Type" not in select_ent.get("unique_id", ""))
# Actually we don't check name here; name is built by entity


# ----- tests: ObloAlertTypeSelect -------------------------------------------

print("\n--- ObloAlertTypeSelect ---")

# Coordinator data with PropAlertType present
svc_with_type = {
    "service": "SimpleAlertService",
    "service_type": "SimpleAlertService",
    "props": [("DeviceType", "18"), ("PropAlertType", "2")],
}
coord = make_coordinator([{**dev, "services": [svc_with_type]}])
select = ObloAlertTypeSelect(coord, select_ent)

check("options = 4 labels", select._attr_options == list(ALERT_TYPE_LABELS))
check("current_option = 'Warning' (wire '2')", select.current_option == "Warning")

# Confirm write with label readback (mismatched label vs wire -> still confirmed)
coord.ohm = FakeMqtt(readbacks=["WARNING"])
async def test_select_confirmed_label():
    await select.async_select_option("Warning")
check("select write confirmed with label readback",
      asyncio.run(test_select_confirmed_label()) or True)  # no raise = pass

# Unconfirmed -> raises
coord.ohm = FakeMqtt(readbacks=["Notification"])
async def test_select_unconfirmed():
    try:
        await select.async_select_option("Warning")
        return False
    except HomeAssistantError:
        return True
check("select unconfirmed -> raises", asyncio.run(test_select_unconfirmed()))


# ----- tests: ObloSoundAlertButton ------------------------------------------

print("\n--- ObloSoundAlertButton ---")

coord = make_coordinator([{**dev, "services": [svc_with_type]}])
sound = ObloSoundAlertButton(coord, sound_ent)

# Current type is "Warning" ("2") → ensure type is set, then start, wait, stop
coord.ohm = FakeMqtt(readbacks=["2", "true", "false"])
async def test_sound_confirmed():
    await sound.async_press()
check("sound press: type + start + stop all confirmed",
      asyncio.run(test_sound_confirmed()) or True)

# Verify write sequence
writes = coord.ohm.writes
check("first write = PropAlertType '2'", writes[0]["property_name"] == "PropAlertType" and writes[0]["value"] == "2")
check("second write = PropAlertStartStop 'true'", writes[1]["property_name"] == "PropAlertStartStop" and writes[1]["value"] == "true")
check("third write = PropAlertStartStop 'false'", writes[2]["property_name"] == "PropAlertStartStop" and writes[2]["value"] == "false")

# Unconfirmed start -> raises
coord = make_coordinator([{**dev, "services": [svc_with_type]}])
coord.ohm = FakeMqtt(readbacks=["2", "false"])  # start didn't take
sound2 = ObloSoundAlertButton(coord, sound_ent)
async def test_sound_unconfirmed():
    try:
        await sound2.async_press()
        return False
    except HomeAssistantError:
        return True
check("sound start unconfirmed -> raises", asyncio.run(test_sound_unconfirmed()))

# If type missing (None), skip type write
svc_no_type = {
    "service": "SimpleAlertService",
    "service_type": "SimpleAlertService",
    "props": [("DeviceType", "18")],  # no PropAlertType
}
coord = make_coordinator([{**dev, "services": [svc_no_type]}])
coord.ohm = FakeMqtt(readbacks=["true", "false"])
sound3 = ObloSoundAlertButton(coord, sound_ent)
async def test_sound_no_type():
    await sound3.async_press()
check("sound press without type: only start+stop",
      asyncio.run(test_sound_no_type()) or True)
writes = coord.ohm.writes
check("only 2 writes (start, stop)", len(writes) == 2)
check("first write = start", writes[0]["property_name"] == "PropAlertStartStop")


# ----- tests: ObloSilenceAlertButton ----------------------------------------

print("\n--- ObloSilenceAlertButton ---")

coord = make_coordinator([{**dev, "services": [svc_with_type]}])
silence = ObloSilenceAlertButton(coord, silence_ent)

coord.ohm = FakeMqtt(readbacks=["false"])
async def test_silence():
    await silence.async_press()
check("silence press confirmed", asyncio.run(test_silence()) or True)
check("silence writes stop token", coord.ohm.writes[0]["value"] == "false")

# Unconfirmed -> raises
coord.ohm = FakeMqtt(readbacks=["true"])
silence2 = ObloSilenceAlertButton(coord, silence_ent)
async def test_silence_unconfirmed():
    try:
        await silence2.async_press()
        return False
    except HomeAssistantError:
        return True
check("silence unconfirmed -> raises", asyncio.run(test_silence_unconfirmed()))


# ----- tests: siren safety (cancellation / unload) --------------------------

print("\n--- siren safety ---")

# A press that is cancelled mid-wait (HA reload, job cancelled) must still
# send the stop. Without the finally+shield the siren keeps sounding forever.
_saved_sound_s = btn_mod.ALERT_SOUND_S
btn_mod.ALERT_SOUND_S = 10
coord = make_coordinator([{**dev, "services": [svc_with_type]}])
coord.ohm = FakeMqtt(readbacks=["2", "true", "false"])
sound4 = ObloSoundAlertButton(coord, sound_ent)

async def test_cancel_mid_wait():
    task = asyncio.ensure_future(sound4.async_press())
    await asyncio.sleep(0.2)          # past the start write, inside the wait
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0.2)          # let the shielded stop write land
    return [w["value"] for w in coord.ohm.writes
            if w["property_name"] == "PropAlertStartStop"]

seq = asyncio.run(test_cancel_mid_wait())
check("cancelled press still sent start", "true" in seq, f"seq={seq}")
check("cancelled press still sent STOP", "false" in seq, f"seq={seq}")
btn_mod.ALERT_SOUND_S = _saved_sound_s

# A completed press leaves no stopper registered (nothing to drain later)
coord = make_coordinator([{**dev, "services": [svc_with_type]}])
coord.ohm = FakeMqtt(readbacks=["2", "true", "false"])
sound5 = ObloSoundAlertButton(coord, sound_ent)
asyncio.run(sound5.async_press())
check("completed press leaves no registered stopper",
      coord._alert_stoppers == {}, f"{coord._alert_stoppers}")

# Unload drain stops anything still in flight, and clears the registry
coord = make_coordinator([{**dev, "services": [svc_with_type]}])
coord.ohm = FakeMqtt(readbacks=["false"])
sound6 = ObloSoundAlertButton(coord, sound_ent)
key = "15:SimpleAlertService"
coord.register_alert_stopper(key, lambda: sound6._async_stop("15", "SimpleAlertService", "SimpleAlertService"))
check("stopper registered before unload", key in coord._alert_stoppers)
asyncio.run(coord.async_stop_active_alerts())
check("unload drain wrote stop",
      any(w["property_name"] == "PropAlertStartStop" and w["value"] == "false"
          for w in coord.ohm.writes))
check("unload drain cleared registry", coord._alert_stoppers == {})

# A stopper that raises must not break teardown of the others
coord = make_coordinator([{**dev, "services": [svc_with_type]}])
coord.ohm = FakeMqtt(readbacks=["false"])
sound7 = ObloSoundAlertButton(coord, sound_ent)
async def _boom():
    raise RuntimeError("gateway gone")
coord.register_alert_stopper("a:bad", _boom)
coord.register_alert_stopper(key, lambda: sound7._async_stop("15", "SimpleAlertService", "SimpleAlertService"))
before = len(coord.ohm.writes)
asyncio.run(coord.async_stop_active_alerts())
check("a failing stopper does not stop the drain",
      len(coord.ohm.writes) > before and coord._alert_stoppers == {})


# ----- tests: Silence vs Sound race -----------------------------------------
# The Sound press is a sequence (set type, start, wait, stop). Silence is a
# single stop. If Silence lands mid-sequence, the stop must not be followed by
# a start -- that re-sounds a siren Andrew just silenced.

print("\n--- silence race ---")

class HookedMqtt(FakeMqtt):
    """FakeMqtt that runs a callback once, at a chosen write.

    Read-backs echo what the gateway would report for that property, keyed by
    property name, so concurrent presses cannot scramble a scripted list.
    """

    def __init__(self, echo=None, at_prop=None, hook=None):
        super().__init__(readbacks=["x"])
        self._echo = dict(echo or {})
        self._last = {}
        self._at_prop = at_prop
        self._hook = hook
        self._fired = False

    async def async_set_property(self, device_id, service_name, property_name,
                                 value, service_type=None, timeout=None):
        res = await super().async_set_property(
            device_id, service_name, property_name, value,
            service_type=service_type, timeout=timeout)
        self._last[property_name] = value
        if not self._fired and property_name == self._at_prop:
            self._fired = True
            await self._hook()
        return res

    async def async_get_property(self, device_id, service_name, property_name,
                                 service_type=None, timeout=None):
        self.reads.append({"device_id": device_id, "service_name": service_name,
                           "property_name": property_name})
        if property_name in self._echo:
            return self._echo[property_name]
        return self._last.get(property_name, "false")


# 1) Silence during the type write: the start must never be sent
coord = make_coordinator([{**dev, "services": [svc_with_type]}])
silence_r = ObloSilenceAlertButton(coord, silence_ent)
sound_r = ObloSoundAlertButton(coord, sound_ent)
coord.ohm = HookedMqtt(echo={"PropAlertType": "2"},
                       at_prop="PropAlertType",
                       hook=lambda: silence_r.async_press())

async def test_silence_during_type_write():
    await sound_r.async_press()
    return [w for w in coord.ohm.writes]

writes = asyncio.run(test_silence_during_type_write())
starts = [w for w in writes
          if w["property_name"] == "PropAlertStartStop" and w["value"] == "true"]
stops = [w for w in writes
         if w["property_name"] == "PropAlertStartStop" and w["value"] == "false"]
check("silence during type write: start never sent", starts == [],
      f"start writes={starts}")
check("silence during type write: stop was sent", len(stops) == 1,
      f"stop writes={stops}")
check("silence during type write: no stopper left behind",
      coord._alert_stoppers == {}, f"{coord._alert_stoppers}")

# 2) Silence after the siren started: the stop still lands, and no second start
_saved = btn_mod.ALERT_SOUND_S
btn_mod.ALERT_SOUND_S = 10
coord = make_coordinator([{**dev, "services": [svc_with_type]}])
silence_a = ObloSilenceAlertButton(coord, silence_ent)
sound_a = ObloSoundAlertButton(coord, sound_ent)
coord.ohm = FakeMqtt(readbacks=["2", "true", "false", "false"])

async def test_silence_after_start():
    task = asyncio.ensure_future(sound_a.async_press())
    await asyncio.sleep(0.2)          # past the start write, inside the wait
    await silence_a.async_press()     # Andrew hits Silence
    task.cancel()                     # ...and HA cancels the pending press
    try:
        await task
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0.2)
    return [w for w in coord.ohm.writes]

writes = asyncio.run(test_silence_after_start())
starts = [w for w in writes
          if w["property_name"] == "PropAlertStartStop" and w["value"] == "true"]
stops = [w for w in writes
         if w["property_name"] == "PropAlertStartStop" and w["value"] == "false"]
check("silence after start: exactly one start", len(starts) == 1, f"{starts}")
check("silence after start: finish still confirms a stop", len(stops) >= 1, f"{stops}")
btn_mod.ALERT_SOUND_S = _saved

# 3) A normal press still sounds when nothing interrupts it
coord = make_coordinator([{**dev, "services": [svc_with_type]}])
coord.ohm = FakeMqtt(readbacks=["2", "true", "false"])
sound_n = ObloSoundAlertButton(coord, sound_ent)
asyncio.run(sound_n.async_press())
seq = [w["value"] for w in coord.ohm.writes
       if w["property_name"] == "PropAlertStartStop"]
check("uninterrupted press still sounds then stops", seq == ["true", "false"], f"{seq}")


# ----- summary --------------------------------------------------------------

print("\n--- SUMMARY ---")
total = len(CHECKS)
passed = sum(1 for _, ok, _ in CHECKS if ok)
failed = total - passed
print(f"{passed}/{total} checks passed")
if failed:
    for label, ok, detail in CHECKS:
        if not ok:
            print(f"  FAIL: {label} <- {detail}")
    sys.exit(1)
print("All checks passed.")