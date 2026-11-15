"""device_manufacturer must never crash the platform on non-string ManufacturerID.

The OBLO gateway sends ManufacturerSpecific.ManufacturerID as a bare int (345),
not "345" or "0x0159". int(raw, 0) raises TypeError on that, and a bare
except ValueError does not catch it, so build_entities() raised during every
platform's async_setup_entry and HA tore down the whole config entry
("Failed to set up the oblo_living button platform").

Regression: every payload value type must either resolve to a vendor or fall
back to MANUFACTURER without raising, and int 345 must map to Qubino.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))  # repo root (package import)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401  (installs the homeassistant stubs on import)

from custom_components.oblo_living.entity_map import (  # noqa: E402
    build_entities, device_manufacturer, MANUFACTURER,
)


def _svc(mfr_val):
    return [{"service": "outlet0", "props": {"State": "true"},
             "service_type": "OutletService"},
            {"service": "ManufacturerSpecific",
             "props": [["ManufacturerID", mfr_val], ["ProductTypeID", 1]],
             "service_type": "ManufacturerSpecific"}]


def _tree(mfr_val):
    return [{"id": 1052, "name": "Hallway Lights",
             "class": "Qubino On/Off Relay", "services": _svc(mfr_val)}]


checks = []


def check(name, cond):
    checks.append((name, bool(cond)))
    print(("PASS  " if cond else "FAIL  ") + name)


# 1. The wire form is an int -- this is what used to raise TypeError.
check("int 345 -> Qubino", device_manufacturer(_svc(345)) == "Qubino")
check("int 345 -> build_entities returns entities",
      len(build_entities(_tree(345))) >= 1)

# 2. String forms still resolve (existing behaviour must not regress).
check("str '345' -> Qubino", device_manufacturer(_svc("345")) == "Qubino")
check("str '0x0159' -> Qubino", device_manufacturer(_svc("0x0159")) == "Qubino")

# 3. Other numeric/truthy payload shapes must fall back, never raise.
check("float 345.0 -> Qubino (numeric coercion)",
      device_manufacturer(_svc(345.0)) == "Qubino")
check("float 12345.6 -> fallback", device_manufacturer(_svc(12345.6)) == MANUFACTURER)
check("bool True -> fallback", device_manufacturer(_svc(True)) == MANUFACTURER)
check("dict -> fallback", device_manufacturer(_svc({"a": 1})) == MANUFACTURER)
check("None -> fallback", device_manufacturer(_svc(None)) == MANUFACTURER)

# 4. No ManufacturerSpecific service -> no crash, fallback.
check("absent service -> fallback",
      device_manufacturer([{"service": "outlet0", "props": {}}]) == MANUFACTURER)

# 5. Unknown vendor id -> OBLO Living fallback, no crash.
check("unknown int id -> fallback", device_manufacturer(_svc(999999)) == MANUFACTURER)

fails = [n for n, ok in checks if not ok]
print(f"\n{len(checks) - len(fails)}/{len(checks)} checks pass")
sys.exit(1 if fails else 0)
