"""Gateway identity resolution + OHM setup guards.

Locks the silent-None-publish trap: if the gateway payload lacks userId or
serial, the topic becomes oblo/None/gtw/None/ohm/req -- QoS 0, broker drops it,
and the client just times out with no error. These checks make that failure
explicit instead.

Run: python3 ohm_setup_test.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401

from custom_components.oblo_living import _pick_gateway_identity  # noqa: E402

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + (f"  <- {detail}" if detail and not cond else ""))


class FakeApi:
    def __init__(self, user_id="login-uid", username="demo-user", groups=None):
        self.user_id = user_id
        self.username = username
        self.groups = groups or []


api = FakeApi()

# --- full gateway payload ------------------------------------------------
uid, serial, keys = _pick_gateway_identity(
    [{"userId": "gw-owner", "serialNumber": "A1B2C3D4E5F6", "_id": "gw-0001"}], api)
check("userId from payload", uid == "gw-owner", uid)
check("serial from serialNumber", serial == "A1B2C3D4E5F6", serial)
check("returns key list", "serialNumber" in keys, str(keys))

# --- serial under alternate keys ----------------------------------------
uid, serial, _ = _pick_gateway_identity([{"userId": "u1", "serial": "ABC123"}], api)
check("serial from 'serial'", serial == "ABC123", serial)
uid, serial, _ = _pick_gateway_identity([{"userId": "u1", "sn": "SN9"}], api)
check("serial from 'sn'", serial == "SN9", serial)

# --- the live DIAG shape: _id + deviceName, no serial -------------------
uid, serial, keys = _pick_gateway_identity(
    [{"_id": "000000000000000000000000", "deviceName": "Living Room", "class": "hg"}], api)
check("no serial -> serial empty (guard trips)", serial == "", repr(serial))
check("userId falls back to login user", uid == "login-uid", uid)

# --- picks the object that actually carries a serial --------------------
uid, serial, _ = _pick_gateway_identity(
    [{"_id": "first-no-serial"}, {"userId": "gw2", "serialNumber": "A1B2C3D4E5F6"}], api)
check("skips serial-less candidate", serial == "A1B2C3D4E5F6", serial)
check("userId from the serial-bearing gateway", uid == "gw2", uid)

# --- junk inputs --------------------------------------------------------
uid, serial, _ = _pick_gateway_identity([], api)
check("empty list -> login user + no serial", uid == "login-uid" and serial == "", f"{uid}/{serial}")
uid, serial, _ = _pick_gateway_identity(None, api)
check("None -> login user + no serial", uid == "login-uid" and serial == "", f"{uid}/{serial}")
uid, serial, _ = _pick_gateway_identity(["not-a-dict", 42], api)
check("non-dict entries ignored", serial == "", repr(serial))

# --- login user id present but empty string must still guard ------------
api_no_uid = FakeApi(user_id=None)
uid, serial, _ = _pick_gateway_identity([{"_id": "x"}], api_no_uid)
check("no user id anywhere -> empty (guard trips)", uid == "", repr(uid))

passed = sum(1 for _, ok, _ in CHECKS if ok)
print(f"\n{passed}/{len(CHECKS)} checks passed")
sys.exit(0 if passed == len(CHECKS) else 1)
