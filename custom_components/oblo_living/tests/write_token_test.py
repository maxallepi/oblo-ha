"""Switch write vocabulary: send the on/off token the device already speaks.

Why this file exists: the gateway stores every property as TEXT in ohm.db, and
two boolean dialects appear in the evidence -- numeric ("1"/"0") and word
("true"/"false", which is what the OBLO app itself persists in
ScenarioDeviceSetting.PropValue). Sending the wrong dialect to a healthy relay
produces `confirmed=False`, which raises in HA and makes a working switch look
broken.

So the switch no longer hard-codes a token: `write_token_for(current, want_on)`
mirrors the dialect of the value the device is currently reporting. This suite
pins that behaviour, including the case that matters -- a word-speaking device
must be switched with "true", not "1", and the current value must never be
echoed back as the new value.

Run: python3 write_token_test.py
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

from custom_components.oblo_living.coordinator import (  # noqa: E402
    ObloDataCoordinator, write_token_for,
)
from custom_components.oblo_living.entity_map import build_entities  # noqa: E402
from custom_components.oblo_living.entities.switch import ObloSwitch  # noqa: E402

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}"
          + (f"  <- {detail}" if detail and not cond else ""))


# --- the token chooser ----------------------------------------------------
def test_numeric_dialect():
    check("numeric: off -> on is '1'", write_token_for("0", True) == "1",
          write_token_for("0", True))
    check("numeric: on -> off is '0'", write_token_for("1", False) == "0",
          write_token_for("1", False))
    check("numeric: on -> on stays '1'", write_token_for("1", True) == "1",
          write_token_for("1", True))


def test_word_dialect():
    check("word: false -> on is 'true'", write_token_for("false", True) == "true",
          write_token_for("false", True))
    check("word: true -> off is 'false'", write_token_for("true", False) == "false",
          write_token_for("true", False))
    check("word: never echo the current value",
          write_token_for("false", True) != "false")


def test_case_and_whitespace():
    check("upper-case current still word", write_token_for("FALSE", True) == "true",
          write_token_for("FALSE", True))
    check("padded current still numeric", write_token_for(" 0 ", True) == "1",
          write_token_for(" 0 ", True))


def test_default_when_unknown():
    # No current value: fall back to the word form, which is what the app writes.
    check("None -> 'true'", write_token_for(None, True) == "true",
          write_token_for(None, True))
    check("None -> 'false'", write_token_for(None, False) == "false",
          write_token_for(None, False))
    # A non-boolean value (e.g. a temperature) must not be treated as a dialect.
    check("non-boolean current -> word default",
          write_token_for("21.5", True) == "true", write_token_for("21.5", True))


def test_python_bools_and_ints():
    check("bool True current", write_token_for(True, False) == "false",
          write_token_for(True, False))
    check("int 0 current", write_token_for(0, True) == "1",
          write_token_for(0, True))


# --- end to end: the switch uses the chooser ------------------------------
class FakeMqtt:
    def __init__(self, readbacks):
        self.writes = []
        self._readbacks = list(readbacks)

    async def async_set_property(self, device_id, service_name, property_name,
                                 value, service_type=None, timeout=None):
        self.writes.append({"device_id": device_id, "service_name": service_name,
                            "property_name": property_name, "value": value,
                            "service_type": service_type})
        return {"property_value": value}

    async def async_get_property(self, device_id, service_name, property_name,
                                 service_type=None, timeout=None):
        return self._readbacks.pop(0)


def make_coordinator(ohm, state):
    hass = type("H", (), {"loop": None})()
    coord = ObloDataCoordinator(hass, api=None)
    coord.ohm = ohm
    coord.devices = [
        {"id": "1052", "name": "Hallway Lights", "class": "ohm",
         "services": [{"service": "outlet0", "props": {"State": state},
                       "service_type": "OutletService"}]},
    ]
    coord.data = list(coord.devices)
    return coord


def first_switch(coord):
    ents = [e for e in build_entities(coord.data) if e["platform"] == "switch"]
    return ObloSwitch(coord, ents[0]) if ents else None


def test_switch_speaks_word_dialect():
    # Device reports "false"; read-back returns "true" -> confirmed.
    ohm = FakeMqtt(["true"])
    coord = make_coordinator(ohm, "false")
    sw = first_switch(coord)
    check("word device: switch entity built", sw is not None)
    check("word device: is_on False", sw is not None and sw.is_on is False)
    asyncio.run(sw.async_turn_on())
    sent = ohm.writes[-1]["value"]
    check("word device: turn_on sends 'true'", sent == "true", sent)
    check("word device: service_type preserved",
          ohm.writes[-1]["service_type"] == "OutletService",
          ohm.writes[-1]["service_type"])


def test_switch_speaks_numeric_dialect():
    ohm = FakeMqtt(["1"])
    coord = make_coordinator(ohm, "0")
    sw = first_switch(coord)
    asyncio.run(sw.async_turn_on())
    sent = ohm.writes[-1]["value"]
    check("numeric device: turn_on sends '1'", sent == "1", sent)


def test_switch_off_mirrors_too():
    ohm = FakeMqtt(["false"])
    coord = make_coordinator(ohm, "true")
    sw = first_switch(coord)
    asyncio.run(sw.async_turn_off())
    sent = ohm.writes[-1]["value"]
    check("word device: turn_off sends 'false'", sent == "false", sent)


def test_unconfirmed_still_raises():
    # Read-back disagrees -> confirmed=False -> raise, never a silent success.
    ohm = FakeMqtt(["false"])
    coord = make_coordinator(ohm, "false")
    sw = first_switch(coord)
    raised = False
    try:
        asyncio.run(sw.async_turn_on())
    except Exception as exc:  # HomeAssistantError
        raised = "unconfirmed" in str(exc).lower()
    check("mismatched read-back raises unconfirmed", raised)


def main():
    test_numeric_dialect()
    test_word_dialect()
    test_case_and_whitespace()
    test_default_when_unknown()
    test_python_bools_and_ints()
    test_switch_speaks_word_dialect()
    test_switch_speaks_numeric_dialect()
    test_switch_off_mirrors_too()
    test_unconfirmed_still_raises()
    bad = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(bad)}/{len(CHECKS)} checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
