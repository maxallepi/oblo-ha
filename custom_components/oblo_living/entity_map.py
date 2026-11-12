"""Entity map: real ohm.db service names -> HA platforms.

Built from the actual gateway backup (~/oblo_extract/backup/ohm/ohm.db), not
from APK class names. The home contains Z-Wave sensors, one relay, weather and
a Wise hub - there are no OBLO dimmers/shutters in this install, so the map is
service-driven (works for any device class that exposes the service).
"""

# service name -> (platform, device_class, unit, value_property)
SERVICE_MAP = {
    # motion / occupancy
    "pir0":             ("binary_sensor", "motion",  None,  "Motion"),
    "PirService":       ("binary_sensor", "motion",  None,  "Motion"),
    # door / window contact
    "ContactService":   ("binary_sensor", "door",    None,  "Contact"),
    # tamper
    "tamper0":          ("binary_sensor", "tamper",  None,  "Activated"),
    "TamperService":    ("binary_sensor", "tamper",  None,  "Activated"),
    # environment
    "temperature0":     ("sensor", "temperature", "\u00b0C", "Temperature"),
    "TemperatureService1": ("sensor", "temperature", "\u00b0C", "Temperature"),
    "TemperatureService2": ("sensor", "temperature", "\u00b0C", "Temperature"),
    "HumidityService1": ("sensor", "humidity",   "%",   "Humidity"),
    "lightIntensity0":  ("sensor", "illuminance", "lx",  "Illuminance"),
    "battery0":         ("sensor", "battery",    "%",   "Level"),
    "BatteryService":   ("sensor", "battery",    "%",   "Level"),
    # relay / outlet
    "outlet0":          ("switch", None, None, "On"),
    # basic0 is the Z-Wave Basic command class: a real actuator only on
    # relay-class devices, so the builder filters it by device class.
    "basic0":           ("switch", None, None, "On"),
    # metering
    "power0":           ("sensor", "power", "W", "Power"),
    # weather
    "WeatherService":   ("sensor", None, None, None),
    "WindService":      ("sensor", None, None, None),
    # alert
    "SimpleAlertService": ("binary_sensor", None, None, "Active"),
}


def build_entities(devices):
    """devices: list from ohm.db dump [{'id','class','name','services':[{'service','props'}]}].

    Returns [{'device_id','class','name','service','platform','device_class','unit','state'}].
    """
    out = []
    for dev in devices:
        for svc in dev.get("services", []):
            name = svc["service"]
            if name not in SERVICE_MAP:
                continue
            platform, dclass, unit, prop = SERVICE_MAP[name]
            # basic0 is only an actuator on relay-type devices; on sensors it is
            # the Z-Wave Basic command class and must not become a switch.
            if name == "basic0" and "relay" not in dev.get("class", "").lower():
                continue
            # if both outlet0 and basic0 exist on same device, basic0 is redundant
            if name == "basic0" and any(svc["service"] == "outlet0" for svc in dev.get("services", [])):
                continue
            props = dict(svc.get("props") or [])
            state = props.get(prop) if prop else None
            out.append({
                "device_id": dev["id"],
                "class": dev["class"],
                "name": dev["name"],
                "service": name,
                "platform": platform,
                "device_class": dclass,
                "unit": unit,
                # prop name the state lives in -- the entity reads this key
                "value_prop": prop,
                "state": state,
                "unique_id": f"oblo_{dev['id']}_{name}",
            })
    return out
