"""Entity map: OBLO canonical service names -> HA platforms.

Built from the OBLO app's canonical service catalog (oblo_service_property_catalog.json)
which is what /udm/device/hg/info/list returns. Gateway backup numbered variants
(pir0, temperature0, outlet0, etc.) are kept as aliases for local MQTT compatibility.
"""

# service name -> (platform, device_class, unit, value_property)
SERVICE_MAP = {
    # ── Motion / occupancy ──────────────────────────────────────────────
    "PirService":        ("binary_sensor", "motion",   None,   "State"),
    "pir0":              ("binary_sensor", "motion",   None,   "State"),

    # ── Door / window contact ───────────────────────────────────────────
    "ContactService":    ("binary_sensor", "door",     None,   "State"),

    # ── Tamper ──────────────────────────────────────────────────────────
    "TamperService":     ("binary_sensor", "tamper",   None,   "Activated"),
    "tamper0":           ("binary_sensor", "tamper",   None,   "Activated"),

    # ── Environment ─────────────────────────────────────────────────────
    "TemperatureService": ("sensor", "temperature", "°C", "Value"),
    "TemperatureService1": ("sensor", "temperature", "°C", "Value"),
    "TemperatureService2": ("sensor", "temperature", "°C", "Value"),
    "temperature0":      ("sensor", "temperature", "°C", "Value"),
    "HumidityService":   ("sensor", "humidity",   "%",  "Value"),
    "HumidityService1":  ("sensor", "humidity",   "%",  "Value"),
    "LightIntensityService": ("sensor", "illuminance", "lx", "Intensity"),
    "lightIntensity0":   ("sensor", "illuminance", "lx", "Intensity"),
    "BatteryService":    ("sensor", "battery",    "%",  "Level"),
    "battery0":          ("sensor", "battery",    "%",  "Level"),

    # ── Relay / Outlet / Switch ────────────────────────────────────────
    "OutletService":     ("switch", None, None, "State"),
    "outlet0":           ("switch", None, None, "State"),
    "BasicService":      ("switch", None, None, "State"),
    "basic0":            ("switch", None, None, "State"),
    "DoorbirdRelayService": ("switch", None, None, "State"),

    # ── Metering / Power ───────────────────────────────────────────────
    "PowerLimitService": ("sensor", "power", "W", "LimitValue"),
    "power0":            ("sensor", "power", "W", "Power"),

    # ── Weather ─────────────────────────────────────────────────────────
    "WeatherService":    ("sensor", None, None, None),
    "WindService":       ("sensor", None, None, None),

    # ── Alert / Safety ──────────────────────────────────────────────────
    "SimpleAlertService": ("binary_sensor", None, None, "PropAlertStartStop"),
    "SmokeService":      ("binary_sensor", "smoke", None, "Detected"),
    "FloodService":      ("binary_sensor", "moisture", None, "Detected"),
    "GasService":        ("binary_sensor", "gas", None, "Detected"),
    "FallDetectionService": ("binary_sensor", None, None, "Detected"),
    "GlassBreakService": ("binary_sensor", "vibration", None, "State"),
    "PressureInfoService": ("binary_sensor", "moisture", None, "Leak"),

    # ── Light / Dimmer ──────────────────────────────────────────────────
    "LightService":      ("switch", None, None, "State"),
    "DimService":        ("light", None, None, "Level"),
    "HueService":        ("light", None, None, "HueLevel"),
    "LightColorTemperatureService": ("light", None, None, "Temperature"),
    "ColorService":      ("light", None, None, "RGB"),

    # ── Cover / Shutter ─────────────────────────────────────────────────
    "RollerShutterService": ("cover", None, None, "Level"),
    "RollerShutterConfigurationService": ("sensor", None, None, "ShadeType"),

    # ── Climate / Thermostat ────────────────────────────────────────────
    "ThermostatService": ("climate", None, None, "Value"),
    "ThermostatModeService": ("select", None, None, "ThermostatMode"),
    "FanService":        ("fan", None, None, "Mode"),

    # ── Lock / Security ─────────────────────────────────────────────────
    "DoorLockService":   ("lock", None, None, "DoorState"),
    "SecurityButtonService": ("binary_sensor", None, None, "State"),
    "ButtonService":     ("binary_sensor", None, None, "State"),

    # ── Alarm / Siren ───────────────────────────────────────────────────
    "AlarmService":      ("binary_sensor", None, None, "AlarmStatus"),
    "SafetyAlarmEventService": ("binary_sensor", None, None, "State"),
    "SecurityAlarmEventService": ("binary_sensor", None, None, "State"),
    "MalfunctionAlarmEventService": ("binary_sensor", None, None, "State"),

    # ── Air Quality ─────────────────────────────────────────────────────
    "AirQualityService": ("sensor", None, None, "Category"),

    # ── Pressure / Leak ─────────────────────────────────────────────────
    "PressureService":   ("sensor", None, None, "Type"),

    # ── Camera / Media ──────────────────────────────────────────────────
    "SnapshotService":   ("camera", None, None, "Snapshot"),
    "VRecService":       ("sensor", None, None, "Duration"),
    "DoorbellService":   ("binary_sensor", None, None, "Ring"),
    "MediaPlaybackService": ("media_player", None, None, "Status"),
    "VolumeService":     ("sensor", None, None, "Level"),

    # ── Voice / IR ──────────────────────────────────────────────────────
    "IRBlasterService":  ("remote", None, None, "Device1"),
    "VocoContentService": ("media_player", None, None, "TextToSpeech"),

    # ── Hue / Device Info ───────────────────────────────────────────────
    "HueDeviceInfoService": ("sensor", None, None, "Name"),

    # ── Gateway / System ────────────────────────────────────────────────
    "WiseDeviceService": ("sensor", None, None, "ConnectionType"),
    "OperationalService": ("binary_sensor", "connectivity", None, "State"),
    # ZoneCtrl / ActivityService are gateway bookkeeping, not device state

    # ── Config / Diagnostic (not exposed as entities) ──────────────────
    # ConfigService, BatteryConfigService, ContactConfigService, etc.
    # These are config-only; skipped by not being in this map.
}

# Services explicitly ignored (config/diagnostic/internal only)
IGNORED_SERVICES = {
    "ConfigService", "BatteryConfigService", "ContactConfigService",
    "FloodConfigService", "GasConfigService", "GlassBreakConfigService",
    "GlobalAlarmConfigService", "HeatConfigService", "OfflineConfigService",
    "OnlineConfigService", "PirConfigService", "PowerLimitConfigService",
    "SeismicConfigService", "SmokeConfigService", "TamperConfigService",
    "VibrationConfigService", "WeatherConfigService", "AlarmConfigService",
    "AlarmControllerService", "AstronomyTimeService", "AddressService",
    "GeolocationControllerService", "HTTPServerResponderService", "HTTPServerService",
    "InitDeviceService", "IpService", "LocationService", "Messaging",
    "OtaControllerService", "SSDPControllerService", "ScenarioControllerService",
    "StateSyncService", "StatusControllerService",
    "SystemCtrlService", "WakeUpService", "WiseBLEMeshService3", "WiseBusService",
    "WiseControllerService", "WiseDiagnosticService", "WiseResponderService",
    "ZWaveService", "ZwaveSecurityService", "SigmaZwaveService", "BusService",
    "DiscoveryService", "BroadLinkBusService", "ManufacturerSpecific",
    "DefaultNotification",
    "ZoneCtrl", "ActivityService",
    "association1", "association2", "association3", "association4", "association5",
    "association6", "association7", "association8", "association9",
    "multiAssociation1", "multiAssociation2", "multiAssociation3", "multiAssociation4",
    "multiAssociation5", "multiAssociation6", "multiAssociation7", "multiAssociation8",
    "multiAssociation9", "wakeup0",
}


# friendly suffix per service, so entity names read "Hallway Motion" not
# "Hallway PirService"
LABELS = {
    "PirService": "Motion", "pir0": "Motion",
    "ContactService": "Contact",
    "TamperService": "Tamper", "tamper0": "Tamper",
    "TemperatureService": "Temperature", "TemperatureService1": "Temperature 1",
    "TemperatureService2": "Temperature 2", "temperature0": "Temperature",
    "HumidityService": "Humidity", "HumidityService1": "Humidity",
    "LightIntensityService": "Illuminance", "lightIntensity0": "Illuminance",
    "BatteryService": "Battery", "battery0": "Battery",
    "OutletService": "Outlet", "outlet0": "Outlet",
    "BasicService": "Switch", "basic0": "Switch",
    "PowerLimitService": "Power Limit", "power0": "Power",
    "SimpleAlertService": "Alert",
    "SmokeService": "Smoke", "FloodService": "Flood", "GasService": "Gas",
    "GlassBreakService": "Glass Break", "PressureInfoService": "Leak",
    "FallDetectionService": "Fall",
    "OperationalService": "Online", "WiseDeviceService": "Connection",
}


def label_for(service):
    """Human label for a service name (falls back to the raw service name)."""
    return LABELS.get(service, service)


# numbered gateway-backup names -> the canonical service they duplicate
ALIASES = {
    "pir0": "PirService",
    "tamper0": "TamperService",
    "temperature0": "TemperatureService",
    "lightIntensity0": "LightIntensityService",
    "battery0": "BatteryService",
    "outlet0": "OutletService",
    "basic0": "BasicService",
    # HumidityService1 carries the WiseHub's own humidity channel; keep it
}


def canonical_service(service):
    """Canonical name for a service, collapsing gateway-backup aliases."""
    return ALIASES.get(service, service)


def entity_name(dev_name, service):
    """Readable entity name: never repeat a word the device name already has."""
    name = (dev_name or "").strip() or "OBLO"
    label = label_for(service)
    if label.lower() in name.lower():
        return name
    return f"{name} {label}"


def read_prop(svc, prop):
    """Read one property from a service dict, tolerantly.

    The cloud has been observed to change property casing between endpoints, so
    match exactly first, then case-insensitively. If the requested prop is
    absent entirely, fall back to the first property that looks like a value
    (so a renamed property still yields an entity instead of None forever).
    """
    props = dict(svc.get("props") or [])
    if prop is not None and prop in props:
        return props[prop]
    if prop is not None:
        for key, val in props.items():
            if key.lower() == str(prop).lower():
                return val
    for key, val in props.items():
        if key.lower() in ("state", "value", "level", "intensity", "detected",
                           "activated", "status", "category", "type"):
            return val
    for val in props.values():
        return val
    return None


def build_entities(devices):
    """devices: list from cloud [{'id','class','name','services':[{'service','props'}]}].

    Returns [{'device_id','class','name','service','platform','device_class','unit','state'}].
    """
    out = []
    for dev in devices:
        raw = dev.get("services", [])
        present = {s["service"] for s in raw}
        present_canon = {canonical_service(n) for n in present}
        is_relay = "relay" in (dev.get("class") or "").lower()
        seen = set()
        # canonical spellings win over gateway-backup aliases on the same device
        for svc in sorted(raw, key=lambda s: canonical_service(s["service"]) != s["service"]):
            name = svc["service"]
            if name not in SERVICE_MAP:
                continue
            # collapse gateway-backup aliases when the canonical service is also
            # present, and never emit both spellings as two entities
            canon = canonical_service(name)
            if canon in seen:
                continue
            # Basic/BasicService is the Z-Wave Basic command class on sensors;
            # only relay-class devices expose it as a real actuator.
            if canon == "BasicService":
                if not is_relay:
                    continue
                if "OutletService" in present_canon or "LightService" in present_canon:
                    continue
            platform, dclass, unit, prop = SERVICE_MAP[name]
            seen.add(canon)
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