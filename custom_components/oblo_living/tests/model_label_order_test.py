"""Model label must be decided by what a device IS, not by payload order.

Regression guard for the v0.2.37 defect: device_model() returned the first
service it met in MODEL_BY_SERVICE, so a Z-Wave motion sensor that also reports
temperature/light/basic was labelled "Temperature Sensor", "Light Sensor" or
"Smart Relay" depending only on the order the gateway listed its services.
Same hardware, four possible labels, all confidently wrong on the device page.

v0.2.38 ranks services by primacy instead.
"""
import itertools
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401

from custom_components.oblo_living.entity_map import (  # noqa: E402
    APP_CLASS_NAMES, MODEL_BY_SERVICE, build_entities, canonical_service,
    device_model,
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


# The exact service list of Andrew's real motion sensors (ohm.db, device 1029),
# in the order the gateway reports it.
MOTION_SERVICES = [
    "InitDeviceService", "OperationalService", "ZWaveService",
    "ZwaveSecurityService", "association1", "association2", "temperature0",
    "DefaultNotification", "tamper0", "pir0", "ManufacturerSpecific",
    "lightIntensity0", "basic0", "battery0", "ConfigService", "wakeup0",
]

RELAY_SERVICES = [
    "InitDeviceService", "ManufacturerSpecific", "OperationalService",
    "StateSyncService", "ZWaveService", "basic0", "outlet0", "power0",
    "ConfigService",
]

print("== payload order must not choose the label")
random.seed(11)
variants = set()
for _ in range(200):
    shuffled = MOTION_SERVICES[:]
    random.shuffle(shuffled)
    variants.add(device_model("SENSOR NOTIFICATION", shuffled))
check("motion sensor: one label across 200 payload orders", len(variants) == 1,
      f"({sorted(variants)})")
check("motion sensor labels as 'Motion Sensor'",
      variants == {"Motion Sensor"}, f"({sorted(variants)})")

relay_variants = set()
for _ in range(200):
    shuffled = RELAY_SERVICES[:]
    random.shuffle(shuffled)
    relay_variants.add(device_model("SENSOR NOTIFICATION", shuffled))
check("relay with a generic class: one label across orders", len(relay_variants) == 1,
      f"({sorted(relay_variants)})")
check("relay labels as 'Smart Relay' (OutletService outranks basic0/power0)",
      relay_variants == {"Smart Relay"}, f"({sorted(relay_variants)})")

print("\n== exhaustive permutations of the deciding services")
permuted = set()
for order in itertools.permutations(["basic0", "pir0", "temperature0",
                                     "lightIntensity0"]):
    permuted.add(device_model("SENSOR NOTIFICATION", list(order)))
check("all 24 permutations agree on 'Motion Sensor'",
      permuted == {"Motion Sensor"}, f"({sorted(permuted)})")

print("\n== app class table still wins over any service")
check("Qubino class beats outlet0",
      device_model("Qubino On/Off Relay", ["outlet0", "power0"]) == "On/Off Relay")
check("SimpleAlert class beats SimpleAlertService",
      device_model("SimpleAlert", ["SimpleAlertService"]) == "Internal Siren")
check("Everspring class beats ContactService",
      device_model("Everspring Door Window Mini",
                   ["ContactService", "TemperatureService"])
      == "Door/Window Detector MINI")

print("\n== unchanged fallbacks")
check("unknown class + outlet0 -> Smart Relay",
      device_model("SomeUnknownClass", ["outlet0"]) == "Smart Relay")
check("real class, no services -> class itself",
      device_model("Acme Widget", []) == "Acme Widget")
check("source marker 'ohm' -> OBLO Device", device_model("ohm", []) == "OBLO Device")
check("source marker 'hg' -> OBLO Device", device_model("hg", []) == "OBLO Device")
check("nothing at all -> OBLO Device", device_model(None, []) == "OBLO Device")
# Real case from Andrew's ohm.db: object 1044 is type "zone-ctrl" with Class "7".
# A bare numeric class is an id, not a model; it must not land on the device page.
check("numeric class '7' -> OBLO Device", device_model("7", []) == "OBLO Device")
check("numeric class with alarm services -> OBLO Device",
      device_model("7", ["SafetyAlarmEventService", "ZoneCtrl"]) == "OBLO Device")
check("power meter alone keeps its label",
      device_model("SENSOR NOTIFICATION", ["power0"]) == "Power Meter")
check("weather station keeps its label",
      device_model("ObloWeatherDevice", ["WeatherService"]) == "Weather Station",
      f"({device_model('ObloWeatherDevice', ['WeatherService'])})")

print("\n== every MODEL_BY_SERVICE key still resolves through the rank lists")
PRIMARY = ["OutletService", "LightService", "DoorLockService", "SmokeService",
           "FloodService", "GasService", "PirService", "ContactService",
           "SimpleAlertService", "WeatherService"]
AUX = ["TemperatureService", "HumidityService", "LightIntensityService",
       "PowerConsumptionService", "BatteryService", "BasicService"]
COVERED = set(PRIMARY) | set(AUX)
ALIASES = {"outlet0": "OutletService", "power0": "PowerConsumptionService",
           "pir0": "PirService", "temperature0": "TemperatureService",
           "lightIntensity0": "LightIntensityService", "basic0": "BasicService"}
uncovered = [k for k in MODEL_BY_SERVICE
             if (ALIASES.get(k) or canonical_service(k)) not in COVERED]
check("no MODEL_BY_SERVICE key is orphaned by the rank lists", not uncovered,
      f"(uncovered: {uncovered})" if uncovered else "")
for key, expected in sorted(MODEL_BY_SERVICE.items()):
    got = device_model("", [key])
    check(f"  label for {key}", got == expected,
          "" if got == expected else f"({got!r} != {expected!r})")

print("\n== per-device labels from a build_entities run")
devices = [{"id": 1029, "class": "SENSOR NOTIFICATION",
            "name": "Front Motion Sensor 1029",
            "services": [{"service": s, "props": []} for s in MOTION_SERVICES]},
           {"id": 1052, "class": "Qubino On/Off Relay", "name": "Hallway Lights",
            "services": [{"service": s, "props": []} for s in RELAY_SERVICES]}]
ents = build_entities(devices)
models = {e["device_id"]: e["model"] for e in ents}
check("1029 -> Motion Sensor", models.get(1029) == "Motion Sensor",
      f"({models.get(1029)!r})")
check("1052 -> On/Off Relay", models.get(1052) == "On/Off Relay",
      f"({models.get(1052)!r})")
check("one model per device (no split device records)",
      len({e["model"] for e in ents if e["device_id"] == 1029}) == 1)

print(f"\n== {checks} checks, {failures} failures")
sys.exit(1 if failures else 0)
