"""Test normalise_ohm_objects with OHM object_list shape from app.js spec."""
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402

from custom_components.oblo_living.coordinator import normalise_ohm_objects  # noqa: E402
from custom_components.oblo_living.entity_map import build_entities  # noqa: E402

CHECKS = []

def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + (f"  <- {detail}" if detail and not cond else ""))

# Mock OHM object_list (shape from app.js: id, device_name, service_list[{name,category,property_list[{name,value}]}])
mock = [{
    "id": "abc123",
    "device_name": "Living Room Motion",
    "service_list": [
        {"name": "PirService", "category": "binary_sensor",
         "property_list": [{"name": "State", "value": "1"}]},
        {"name": "TemperatureService", "category": "sensor",
         "property_list": [{"name": "Value", "value": "21.5"}]},
        {"name": "BatteryService", "category": "sensor",
         "property_list": [{"name": "Level", "value": "85"}]},
    ]
}, {
    "id": "def456",
    "device_name": "Kitchen Outlet",
    "service_list": [
        {"name": "OutletService", "category": "switch",
         "property_list": [{"name": "State", "value": "0"}]},
        {"name": "PowerLimitService", "category": "sensor",
         "property_list": [{"name": "LimitValue", "value": "2500"}]},
    ]
}]

out = normalise_ohm_objects(mock)
check("returns 2 devices", len(out) == 2)
check("first device id preserved", out[0]["id"] == "abc123")
check("first device name preserved", out[0]["name"] == "Living Room Motion")
check("first device has 3 services", len(out[0]["services"]) == 3)
check("service has service+props keys", "service" in out[0]["services"][0] and "props" in out[0]["services"][0])
check("PirService props has State=1", out[0]["services"][0]["props"].get("State") == "1")
check("TemperatureService props has Value=21.5", out[0]["services"][1]["props"].get("Value") == "21.5")
check("BatteryService props has Level=85", out[0]["services"][2]["props"].get("Level") == "85")
check("second device id", out[1]["id"] == "def456")
check("OutletService props State=0", out[1]["services"][0]["props"].get("State") == "0")
check("PowerLimitService props LimitValue=2500", out[1]["services"][1]["props"].get("LimitValue") == "2500")

# Feed to build_entities - should produce entities
entities = build_entities(out)
check("build_entities produces entities", len(entities) > 0)
eids = {e["unique_id"] for e in entities}
check("has PirService binary_sensor", any("PirService" in eid for eid in eids))
check("has TemperatureService sensor", any("TemperatureService" in eid for eid in eids))
check("has BatteryService sensor", any("BatteryService" in eid for eid in eids))
check("has OutletService switch", any("OutletService" in eid for eid in eids))
check("has PowerLimitService sensor", any("PowerLimitService" in eid for eid in eids))

# Edge cases
check("empty list returns empty", normalise_ohm_objects([]) == [])
check("None returns empty", normalise_ohm_objects(None) == [])
check("skips object without id", len(normalise_ohm_objects([{"device_name": "x"}])) == 0)
check("skips service without name",
        len(normalise_ohm_objects([{"id": "x", "service_list": [{}]}])[0]["services"]) == 0)

# Summary
passed = sum(1 for _, ok, _ in CHECKS if ok)
failed = len(CHECKS) - passed
print(f"\n{passed}/{len(CHECKS)} checks passed")
sys.exit(0 if failed == 0 else 1)
