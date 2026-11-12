"""Canonical service names from the OBLO cloud catalog -> entities.

Regression test: the cloud /udm/device/hg/info/list returns canonical names
(PirService, TemperatureService, etc.). This ensures SERVICE_MAP maps them and
that gateway-backup aliases (pir0, basic0...) do not duplicate entities.
"""
import sys, os, types

COMPONENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, COMPONENT)
_pkg = types.ModuleType("oblo_living")
_pkg.__path__ = [COMPONENT]
sys.modules.setdefault("oblo_living", _pkg)
import ha_stub  # noqa: F401

from oblo_living.entity_map import build_entities, SERVICE_MAP, IGNORED_SERVICES

CHECKS = []


# Canonical cloud device payload shape (what /udm/device/hg/info/list returns)
CANONICAL_DEVICES = [
    {
        "id": "dev-motion-1",
        "class": "MotionSensor",
        "name": "Hall Motion",
        "services": [
            {"service": "PirService", "props": [["State", "1"]]},
        ],
    },
    {
        "id": "dev-contact-1",
        "class": "DoorSensor",
        "name": "Back Door",
        "services": [
            {"service": "ContactService", "props": [["State", "0"]]},
            {"service": "TamperService", "props": [["Activated", "0"]]},
            {"service": "BatteryService", "props": [["Level", "85"]]},
        ],
    },
    {
        "id": "dev-weather-1",
        "class": "WeatherStation",
        "name": "Garden Weather",
        "services": [
            {"service": "TemperatureService", "props": [["Value", "22.5"]]},
            {"service": "HumidityService", "props": [["Value", "55"]]},
            {"service": "LightIntensityService", "props": [["Intensity", "12000"]]},
            {"service": "WindService", "props": [["Speed", "15"]]},
        ],
    },
    {
        "id": "dev-relay-1",
        "class": "Relay",
        "name": "Hallway Lights",
        "services": [
            {"service": "OutletService", "props": [["State", "1"]]},
            {"service": "BasicService", "props": [["State", "1"]]},
            {"service": "PowerLimitService", "props": [["LimitValue", "3000"]]},
        ],
    },
    {
        "id": "dev-alert-1",
        "class": "SimpleAlert",
        "name": "Front Door Alert",
        "services": [
            {"service": "SimpleAlertService", "props": [["PropAlertStartStop", "1"]]},
        ],
    },
    {
        # gateway-backup style device: alias + canonical on the same object
        "id": "dev-alias-1",
        "class": "SENSOR NOTIFICATION",
        "name": "TV Motion",
        "services": [
            {"service": "pir0", "props": [["State", "0"]]},
            {"service": "PirService", "props": [["State", "0"]]},
            {"service": "battery0", "props": [["Level", "90"]]},
        ],
    },
]


def check(desc, ok, detail=None):
    CHECKS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {desc}")
    if not ok and detail is not None:
        print(f"     -> {detail}")
    return ok


def main():
    entities = build_entities(CANONICAL_DEVICES)

    sensors = [e for e in entities if e["platform"] == "sensor"]
    bins = [e for e in entities if e["platform"] == "binary_sensor"]
    switches = [e for e in entities if e["platform"] == "switch"]
    print(f"Built {len(entities)} entities: {len(sensors)} sensors, "
          f"{len(bins)} binary, {len(switches)} switches")

    m = [e for e in bins if e["unique_id"] == "oblo_dev-motion-1_PirService"]
    check("PirService -> binary_sensor (motion)", len(m) == 1, m)
    if m:
        check("PirService device_class", m[0]["device_class"] == "motion",
              m[0].get("device_class"))
        check("PirService value_prop", m[0]["value_prop"] == "State",
              m[0].get("value_prop"))
        check("entity name does not repeat the device word",
              m[0]["name"] == "Hall Motion", m[0].get("name"))

    check("ContactService -> binary_sensor (door)",
          len([e for e in bins if e["unique_id"] == "oblo_dev-contact-1_ContactService"]) == 1)
    check("TamperService -> binary_sensor (tamper)",
          len([e for e in bins if e["unique_id"] == "oblo_dev-contact-1_TamperService"]) == 1)
    b = [e for e in sensors if e["unique_id"] == "oblo_dev-contact-1_BatteryService"]
    check("BatteryService -> sensor (battery)", len(b) == 1, b)
    if b:
        check("BatteryService value_prop", b[0]["value_prop"] == "Level",
              b[0].get("value_prop"))

    check("TemperatureService -> sensor (temperature)",
          len([e for e in sensors if e["unique_id"] == "oblo_dev-weather-1_TemperatureService"]) == 1)
    check("HumidityService -> sensor (humidity)",
          len([e for e in sensors if e["unique_id"] == "oblo_dev-weather-1_HumidityService"]) == 1)
    check("LightIntensityService -> sensor (illuminance)",
          len([e for e in sensors if e["unique_id"] == "oblo_dev-weather-1_LightIntensityService"]) == 1)
    check("WindService -> sensor",
          len([e for e in sensors if e["unique_id"] == "oblo_dev-weather-1_WindService"]) == 1)

    check("OutletService -> switch",
          len([e for e in switches if e["unique_id"] == "oblo_dev-relay-1_OutletService"]) == 1)
    check("BasicService skipped when OutletService present",
          len([e for e in switches if e["unique_id"] == "oblo_dev-relay-1_BasicService"]) == 0)
    check("PowerLimitService -> sensor (power)",
          len([e for e in sensors if e["unique_id"] == "oblo_dev-relay-1_PowerLimitService"]) == 1)
    check("SimpleAlertService -> binary_sensor",
          len([e for e in bins if e["unique_id"] == "oblo_dev-alert-1_SimpleAlertService"]) == 1)

    # alias handling: pir0 + PirService on one device must not double up
    alias_dev = [e for e in entities if e["device_id"] == "dev-alias-1"]
    check("alias device yields exactly 2 entities (motion + battery)",
          len(alias_dev) == 2, [(e["service"], e["platform"]) for e in alias_dev])
    check("canonical spelling wins over alias",
          {e["service"] for e in alias_dev} == {"PirService", "battery0"},
          {e["service"] for e in alias_dev})

    seen = {e["service"] for e in entities}
    expected = {
        "PirService", "ContactService", "TamperService", "BatteryService",
        "TemperatureService", "HumidityService", "LightIntensityService",
        "WindService", "OutletService", "PowerLimitService",
        "SimpleAlertService",
    }
    check("all canonical services covered", not expected - seen,
          f"missing: {expected - seen}")

    overlap = IGNORED_SERVICES & set(SERVICE_MAP.keys())
    check("IGNORED_SERVICES disjoint from SERVICE_MAP", not overlap,
          f"overlap: {overlap}")

    # read_prop tolerates a renamed/case-changed property
    from oblo_living.entity_map import read_prop
    check("read_prop exact match", read_prop({"props": [["State", "1"]]}, "State") == "1")
    check("read_prop case-insensitive", read_prop({"props": [["state", "1"]]}, "State") == "1")
    check("read_prop falls back to a value-looking prop",
          read_prop({"props": [["Whatever", "7"]]}, "State") == "7")

    failed = CHECKS.count(False)
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} checks pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())