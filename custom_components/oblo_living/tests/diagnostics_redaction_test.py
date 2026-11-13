#!/usr/bin/env python3
"""diagnostics_redaction_test.py -- end-to-end redaction assertion.

Calls the real async_get_config_entry_diagnostics with a fake entry
containing real secret values. Asserts that NONE of the secret values
appear anywhere in the serialized diagnostics output.
"""
import os
import sys
import asyncio
import json
from unittest.mock import MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401

from custom_components.oblo_living.diagnostics import (  # noqa: E402
    async_get_config_entry_diagnostics,
)

checks = 0
failures = 0


def check(label, cond, extra=""):
    global checks, failures
    checks += 1
    if cond:
        print(f"  ok   {label} {extra}")
    else:
        failures += 1
        print(f"  FAIL {label} {extra}")


# Build a fake HA and entry with REAL secret values in data
class FakeEntry:
    def __init__(self):
        self.entry_id = "entry_test_123"
        self.unique_id = "C83DD4300D5B"
        self.version = 2
        self.domain = "oblo_living"
        # These are the values we MUST NEVER see in output
        self.data = {
            "identity": "user@example.com",
            "secret": "S3CRET-PASSWORD-XYZZY",
            "session_id": "REAL-SESSION-TOKEN-ABCDEF",
            "secureId": "ZMUUc1rFXkt5YztwZAA6QNgRpJCkKm1sOABwgSHf",
            "hash": "REAL-HASH-VALUE-123456",
            "password": "ANOTHER-REAL-PASSWORD",
            "username": "cli/user@example.com",
            "scan_interval": 60,
        }
        self.options = {}


class FakeCoordinator:
    def __init__(self):
        self.data = []
        self.entry_id = "entry_test_123"
        self.gateway_serial = "C83DD4300D5B"


class FakeApi:
    def __init__(self):
        self.session_id = "REAL-SESSION-TOKEN-ABCDEF"
        self.secure_id = "ZMUUc1rFXkt5YztwZAA6QNgRpJCkKm1sOABwgSHf"
        self.user_id = 12345


class FakeHass:
    def __init__(self, coordinator, api):
        self.data = {
            "oblo_living": {
                "entry_test_123": {
                    "coordinator": coordinator,
                    "api": api,
                }
            }
        }


async def main():
    entry = FakeEntry()
    coord = FakeCoordinator()
    api = FakeApi()
    hass = FakeHass(coord, api)

    # Call the real diagnostics function
    result = await async_get_config_entry_diagnostics(hass, entry)

    # Serialize to JSON string to search
    serialized = json.dumps(result, default=str)

    # These secret values must NOT appear anywhere
    SECRETS = [
        "S3CRET-PASSWORD-XYZZY",
        "REAL-SESSION-TOKEN-ABCDEF",
        "ZMUUc1rFXkt5YztwZAA6QNgRpJCkKm1sOABwgSHf",
        "REAL-HASH-VALUE-123456",
        "ANOTHER-REAL-PASSWORD",
    ]

    for s in SECRETS:
        check(f"secret '{s[:20]}...' absent from output", s not in serialized,
              f"(found in output)" if s in serialized else "")

    # Presence flags must be True
    check("entry summary has secureId_present=True",
          result["entry"].get("secureId_present") is True)
    check("entry summary has hash_present=True",
          result["entry"].get("hash_present") is True)
    check("entry summary has secret_present=True",
          result["entry"].get("secret_present") is True)
    check("entry summary has session_id_present=True",
          result["entry"].get("session_id_present") is True)
    check("entry summary has password_present=True",
          result["entry"].get("password_present") is True)

    # Non-secret fields must pass through
    check("entry summary has identity", result["entry"].get("identity") == "user@example.com")
    check("entry summary has username", result["entry"].get("username") == "cli/user@example.com")
    check("entry summary has scan_interval", result["entry"].get("scan_interval") == 60)

    # api summary
    check("api authenticated flag is True", result["api"].get("authenticated") is True)
    check("api session_id_present is True", result["api"].get("session_id_present") is True)
    check("api secure_id_present is True", result["api"].get("secure_id_present") is True)

    print(f"\n== {checks} checks, {failures} failures")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))