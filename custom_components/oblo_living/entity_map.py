"""Entity map: OBLO canonical service names -> HA platforms.

Built from the OBLO app's canonical service catalog (oblo_service_property_catalog.json)
which is what /udm/device/hg/info/list returns. Gateway backup numbered variants
(pir0, temperature0, outlet0, etc.) are kept as aliases for local MQTT compatibility.
"""
from .const import DOMAIN

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
    # SimpleAlertService removed: it produces TWO entities (select + button).
    # Defined in MULTI_ENTITY_MAP below.
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


# Friendly suffix per service, so entity names read "Hallway Motion" not
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

    # ── Labels for every remaining SERVICE_MAP key ─────────────────────
    # A service with no entry here renders its Python identifier on the
    # device page ("WeatherService", "MalfunctionAlarmEventService"). The
    # test suite asserts every SERVICE_MAP key has a human label, so a new
    # service cannot ship without one.
    "WeatherService": "Weather", "WindService": "Wind",
    "MalfunctionAlarmEventService": "Malfunction Alarm",
    "SafetyAlarmEventService": "Safety Alarm",
    "SecurityAlarmEventService": "Security Alarm",
    "AlarmService": "Alarm",
    "AirQualityService": "Air Quality",
    "PressureService": "Pressure",
    "LightService": "Light", "DimService": "Dimmer", "HueService": "Hue",
    "ColorService": "Color",
    "LightColorTemperatureService": "Color Temperature",
    "DoorLockService": "Lock", "DoorbellService": "Doorbell",
    "DoorbirdRelayService": "Doorbird Relay",
    "SecurityButtonService": "Security Button", "ButtonService": "Button",
    "RollerShutterService": "Shutter",
    "RollerShutterConfigurationService": "Shutter Configuration",
    "ThermostatService": "Thermostat", "ThermostatModeService": "Thermostat Mode",
    "FanService": "Fan",
    "SnapshotService": "Snapshot", "VRecService": "Recording",
    "MediaPlaybackService": "Playback", "VolumeService": "Volume",
    "IRBlasterService": "IR Blaster", "VocoContentService": "TTS",
    "HueDeviceInfoService": "Device Info",
}

# Services whose entities are device health, not device controls. HA keeps
# these off the primary device page under "Diagnostics" -- without the
# category every OBLO device shows an "Online" row competing with the
# switch/sensor the user actually came for.
DIAGNOSTIC_SERVICES = frozenset({
    "OperationalService", "WiseDeviceService",
})


# ── SimpleAlert: one service, two entities ──────────────────────────────
# Wire format from app.js:
#   PropAlertType:      {0:"NOTIFICATION", 1:"ALARM", 2:"WARNING", 3:"EMERGENCY"}
#   PropAlertStartStop: {true:"ON", false:"OFF"}
# Both properties absent from ohm.db → initial state unknown.
# PropAlertStartStop is an EDGE, not a persistent state: the app writes true
# then false in scenes. A switch would lie by staying "on" after the siren stops.
# Model it as a button: press = write type, write start, wait, write stop.
# Write order matters: set type FIRST, then start.
ALERT_TYPE_LABELS = ("Notification", "Alarm", "Warning", "Emergency")
ALERT_TYPE_TO_WIRE = {lbl.lower(): str(i) for i, lbl in enumerate(ALERT_TYPE_LABELS)}
WIRE_TO_ALERT_TYPE = {str(i): lbl for i, lbl in enumerate(ALERT_TYPE_LABELS)}
ALERT_START_TOKEN = "true"
ALERT_STOP_TOKEN = "false"
ALERT_SOUND_S = 10  # how long the siren sounds on a button press


def alert_type_label(value):
    """Wire value -> UI label. Accepts '2', '2', 'warning', 'WARNING'."""
    v = str(value).strip().lower()
    if v in WIRE_TO_ALERT_TYPE:
        return WIRE_TO_ALERT_TYPE[v]
    for i, lbl in enumerate(ALERT_TYPE_LABELS):
        if v == lbl.lower():
            return lbl
    return value  # unknown -> pass through


def alert_type_wire(label):
    """UI label -> wire value ('2')."""
    v = str(label).strip().lower()
    if v in ALERT_TYPE_TO_WIRE:
        return ALERT_TYPE_TO_WIRE[v]
    # Also accept the wire value itself if user passed it directly
    if v in WIRE_TO_ALERT_TYPE:
        return v
    raise ValueError(f"unknown alert type: {label!r}")


# Services that emit more than one HA entity. Each spec:
#   platform: "select" | "button"
#   value_prop: property name in OHM model
#   suffix: appended to device name for the entity ("Alert Type", "Sound Alert")
#   options: list of UI option strings (for select only)
MULTI_ENTITY_MAP = {
    "SimpleAlertService": [
        {
            "platform": "select",
            "value_prop": "PropAlertType",
            "suffix": "Type",
            "options": ALERT_TYPE_LABELS,
        },
        {
            "platform": "button",
            "value_prop": "PropAlertStartStop",
            "suffix": "Sound",
        },
        {
            "platform": "button",
            "value_prop": "PropAlertStartStop",
            "suffix": "Silence",
            "action": "stop",
        },
    ],
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
    return ALIASES.get(service, service) or service


def entity_name(dev_name, service):
    """Readable entity name: never repeat a word the device name already has."""
    name = (dev_name or "").strip() or "OBLO"
    label = label_for(service)
    if label.lower() in name.lower():
        return name
    return f"{name} {label}"


def entity_label_attr(ent):
    """_attr_name for a has_entity_name=True entity: the short label.

    Returns None when the device name already contains the label, because HA
    then prefixes the device name itself and the user would read
    "Hallway Lights Outlet Outlet". None means "use the device name as the
    entity name", which is exactly the old de-duplicated display.
    """
    label = ent.get("label") or entity_label(ent)
    dev_name = (ent.get("name") or "").strip()
    if dev_name and (label or "").lower() in dev_name.lower():
        return None
    return label


def entity_category_attr(ent):
    """EntityCategory name ("diagnostic") for one entity dict, or None.

    Returns the raw string: entity_map stays free of homeassistant imports so
    it can be unit-tested without HA. The entity classes wrap it in
    EntityCategory.
    """
    return ent.get("entity_category") or None


def entity_name_multi(dev_name, suffix):
    """Name for a multi-entity entry: device name + suffix (deduped)."""
    name = (dev_name or "").strip() or "OBLO"
    words = suffix.split()
    # drop suffix words already present at the end of the device name
    while words and words[0].lower() in name.lower().split():
        words.pop(0)
    rest = " ".join(words)
    return f"{name} {rest}".strip() if rest else name


# Per-ENTITY labels for has_entity_name devices: what the entity row reads
# under the device header. Keyed on the multi-entity suffix, NOT on the
# service, because one service can emit several entities -- SimpleAlertService
# yields select/Type, button/Sound and button/Silence, and a per-service label
# would render three identical "Alert" rows the user cannot tell apart.
MULTI_ENTITY_LABELS = {
    "Type": "Alert Type",
    "Sound": "Sound",
    "Silence": "Silence",
}


def entity_label(ent):
    """Short label for one entity under a has_entity_name device page.

    Never returns a raw Python identifier: multi-entity rows get the
    per-entity table, everything else gets the service label table, and any
    service still missing a label falls back to a spaced-out form of its own
    name ("MalfunctionAlarmEventService" -> "Malfunction Alarm Event")
    rather than shipping CamelCase to the UI.
    """
    suffix = ent.get("name_suffix")
    if suffix:
        return MULTI_ENTITY_LABELS.get(suffix, suffix)
    label = LABELS.get(ent.get("service"))
    if label:
        return label
    label = LABELS.get(canonical_service(ent.get("service") or ""))
    if label:
        return label
    raw = str(ent.get("service") or "").strip()
    for tail in ("Service", "EventService"):
        if raw.endswith(tail):
            raw = raw[: -len(tail)]
            break
    # split CamelCase into words, keep existing acronyms ("IR") intact
    words, cur = [], ""
    for ch in raw:
        if ch.isupper() and cur and not cur[-1].isupper():
            words.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        words.append(cur)
    return " ".join(words).strip() or "OBLO"


def read_prop(svc, prop):
    """Read one property from a service dict, tolerantly.

    The cloud has been observed to change property casing between endpoints, so
    match exactly first, then case-insensitively. If the requested prop is
    absent entirely, fall back to the first property that looks like a value
    so a renamed property still yields an entity instead of None forever).
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


# ── Device registry metadata ────────────────────────────────────────────
# HA core only shows an integration's device list when entities declare
# DeviceInfo. Without it every entity is orphaned: it appears in the entity
# list but belongs to no device, so the integration page has nothing to group
# and shows an empty device section (this is what the Tuya Local page shows
# and what OBLO was missing).
MANUFACTURER = "OBLO Living"

# Device model as shown on the device page. Derived from the services the
# device actually exposes -- OHM-sourced devices report class "ohm", which is
# a source marker, not hardware.
MODEL_BY_SERVICE = {
    "OutletService": "Smart Relay",
    "outlet0": "Smart Relay",
    "BasicService": "Smart Relay",
    "basic0": "Smart Relay",
    "SimpleAlertService": "Alert Siren",
    "PirService": "Motion Sensor",
    "pir0": "Motion Sensor",
    "ContactService": "Door / Window Sensor",
    "TemperatureService": "Temperature Sensor",
    "temperature0": "Temperature Sensor",
    "HumidityService": "Humidity Sensor",
    "LightIntensityService": "Light Sensor",
    "lightIntensity0": "Light Sensor",
    "PowerConsumptionService": "Power Meter",
    "power0": "Power Meter",
    "SmokeService": "Smoke Detector",
    "FloodService": "Flood Detector",
    "GasService": "Gas Detector",
    "DoorLockService": "Door Lock",
    "WeatherService": "Weather Station",
}


# Z-Wave manufacturer IDs -> vendor name. Only vendors actually observed in
# OBLO payloads plus the common ones; unknown IDs fall back to OBLO Living.
# Format: decimal or hex integer key -> string.
# Andrew's Hallway Lights (id 1052) has ManufacturerSpecific.ManufacturerID=345 (0x0159) = Qubino.
ZWAVE_VENDORS = {
    345: "Qubino",          # 0x0159 - Flush 1 relay family (verified)
    134: "Aeotec",          # 0x0086
    271: "Fibaro",          # 0x010F
    96: "Everspring",       # 0x0060
}


# OBLO app class->name table (verbatim marketing names from app.js).
# Hand-curated subset covering Andrew's devices + obvious ones.
# Unknown class falls back to service-derived model -> class -> "OBLO Device".
APP_CLASS_NAMES = {
    "Qubino On/Off Relay": "On/Off Relay",
    "SimpleAlert": "Internal Siren",
    "Everspring Door Window Mini": "Door/Window Detector MINI",
    "Qubino Flush 1D Relay": "Flush 1D Relay",
    "Qubino Flush 2 Relay": "Flush 2 Relay",
    "Fibaro Wall Plug": "Wall Plug",
    "Aeotec Smart Switch 6": "Smart Switch 6",
}


def device_manufacturer(services):
    """Z-Wave vendor from ManufacturerSpecific.ManufacturerID, else OBLO Living."""
    for svc in services or []:
        name = str(svc.get("service") or svc.get("name") or "")
        if canonical_service(name) != "ManufacturerSpecific":
            continue
        raw = read_prop_strict(svc, "ManufacturerID")
        if raw is None:
            continue
        try:
            # Accept decimal "345" or hex "0x0159"
            mfr_id = int(raw, 0)
            return ZWAVE_VENDORS.get(mfr_id, MANUFACTURER)
        except ValueError:
            continue
    return MANUFACTURER


def device_model(dev_class, service_names):
    """Best hardware description for a device.

    Priority: app class name -> primary service -> auxiliary service ->
    class (if real name) -> fallback.

    The primary/aux split fixes a payload-order defect: without it, a motion
    sensor that also reports temperature was labelled "Temperature Sensor"
    (or "Smart Relay", or "Light Sensor") depending on which service appeared
    first in the gateway's payload. By ranking services by what DEFINES the
    device, the label is stable regardless of payload order.
    """
    # 1) OBLO app class->name table (real marketing names)
    if dev_class:
        app_name = APP_CLASS_NAMES.get(str(dev_class))
        if app_name:
            return app_name

    # 2) Service-derived model, ranked by primacy (what defines the device)
    #    Primary = device-class-defining services; Aux = auxiliary channels
    #    that many device types include but don't define them.
    names = [str(n) for n in (service_names or [])]

    # Instance aliases that canonical_service() does not know (it maps gateway
    # backup spellings, not every instance suffix). Kept local to the model
    # lookup so service addressing elsewhere is untouched.
    _INSTANCE_ALIASES = {
        "outlet0": "OutletService", "light0": "LightService",
        "power0": "PowerConsumptionService", "pir0": "PirService",
        "temperature0": "TemperatureService", "basic0": "BasicService",
        "battery0": "BatteryService",
        "lightIntensity0": "LightIntensityService",
    }

    def _canon(name):
        return _INSTANCE_ALIASES.get(name) or canonical_service(name)

    def _first_in(family):
        for fam in family:
            for name in names:
                if name == fam or _canon(name) == fam:
                    model = MODEL_BY_SERVICE.get(name) or MODEL_BY_SERVICE.get(
                        _canon(name))
                    if model:
                        return model
        return None

    PRIMARY_SERVICES = [
        "OutletService", "LightService", "DoorLockService", "SmokeService",
        "FloodService", "GasService", "PirService", "ContactService",
        "SimpleAlertService", "WeatherService",
    ]
    AUX_SERVICES = [
        "TemperatureService", "HumidityService", "LightIntensityService",
        "PowerConsumptionService", "BatteryService", "BasicService",
    ]

    model = _first_in(PRIMARY_SERVICES) or _first_in(AUX_SERVICES)
    if model:
        return model

    # 3) Class itself, unless it's a source marker like "ohm" or "hg"
    #    or a bare numeric string (looks like an id, not a model name).
    cls = str(dev_class or "").strip()
    if cls and cls.lower() not in ("ohm", "hg", "sensor", "pir", "relay") and not cls.isdigit():
        return cls

    return "OBLO Device"


def device_identifier(entry_id, device_id):
    """Registry identifier for one OBLO device.

    Entry-scoped on purpose: OHM object ids are small integers ("1052") and
    two gateways on the same account can legitimately both have a "1052".
    A bare id as identifier would merge two physical devices into one.
    """
    return f"{entry_id or 'unknown'}_{device_id}"


def ohm_id_from_identifier(entry_id, identifier):
    """Inverse of device_identifier(): the bare OHM object id, or None.

    HA's device registry stores the full `device_identifier()` string.
    Diagnostics and other code need the raw OHM object id (numeric string).

    Returns None on mismatch so the caller can log "unrecognised identifier"
    instead of silently returning live: [] forever. Identifiers are immutable
    once registered — a mismatch means this device was created with a falsy
    entry_id and will never heal without manual removal.
    """
    if identifier is None:
        return None
    s = str(identifier)
    prefix = f"{entry_id or 'unknown'}_"
    if s.startswith(prefix):
        return s[len(prefix):]
    return None


def gateway_identifier(entry_id):
    """Registry identifier for the gateway device itself."""
    return f"{entry_id or 'unknown'}_gateway"


def device_info_fields(ent, entry_id=None, gateway_serial=None):
    """Plain-dict DeviceInfo payload for one entity's device.

    Returned as a dict so the mapping layer stays free of HA imports (tests
    drive entity_map without HA installed); entity classes build DeviceInfo
    from it. Every entity of one device gets the same identifiers set, which
    is what groups them onto a single device page.
    """
    did = ent.get("device_id")
    fields = {
        "identifiers": {(DOMAIN, device_identifier(entry_id, did))},
        "name": (ent.get("name") or str(did) or "OBLO").strip(),
        # Z-Wave vendor when the device reports ManufacturerSpecific, else OBLO.
        "manufacturer": ent.get("manufacturer") or MANUFACTURER,
        "model": ent.get("model") or "OBLO Device",
    }
    serial = str(ent.get("gateway") or gateway_serial or "").strip()
    if serial:
        # Shows as "Connected via <gateway>" on the device page. The gateway
        # device is registered during setup; without that this would dangle.
        fields["via_device"] = (DOMAIN, gateway_identifier(entry_id))
    return fields


def read_prop_strict(svc, prop):
    """Strict read: returns the property only if it is genuinely present.

    No fallbacks, no fuzzy key search. Returns None if the exact key (or
    case-insensitive match) is absent. Use for writable/state-critical
    properties where a fallback would produce garbage (e.g. DeviceType=16
    being mistaken for State or PropAlertType).
    """
    props = dict(svc.get("props") or [])
    if prop is None:
        return None
    if prop in props:
        return props[prop]
    for key, val in props.items():
        if key.lower() == str(prop).lower():
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
        # Device-page metadata, computed once per device: every entity of this
        # device must carry the same model/manufacturer or HA shows a split
        # device record.
        model = device_model(dev.get("class"), [s["service"] for s in raw])
        manufacturer = device_manufacturer(raw)
        seen = set()
        # canonical spellings win over gateway-backup aliases on the same device
        for svc in sorted(raw, key=lambda s: canonical_service(s["service"]) != s["service"]):
            name = svc["service"]
            canon = canonical_service(name)
            # services that expand into several entities (e.g. SimpleAlert:
            # select + button) are handled before the single-entity map
            multi = MULTI_ENTITY_MAP.get(name) or MULTI_ENTITY_MAP.get(canon)
            if multi:
                if canon in seen:
                    continue
                seen.add(canon)
                props = dict(svc.get("props") or [])
                for spec in multi:
                    prop = str(spec.get("value_prop") or "")
                    out.append({
                        "device_id": dev["id"],
                        "class": dev["class"],
                        "name": dev["name"],
                        "service": name,
                        "platform": spec["platform"],
                        "device_class": None,
                        "unit": None,
                        "value_prop": prop,
                        "state": props.get(prop),
                        "service_type": svc.get("service_type"),
                        # multi-entity: suffix keeps names and registry ids apart
                        "name_suffix": spec.get("suffix"),
                        "options": spec.get("options"),
                        "action": spec.get("action") or "press",
                        "unique_id": (f"oblo_{dev['id']}_{name}_"
                                      f"{spec['platform']}_{spec.get('suffix')}"),
                        "model": model,
                        "manufacturer": manufacturer,
                        "label": entity_label({"service": name,
                                               "name_suffix": spec.get("suffix")}),
                        "entity_category": ("diagnostic"
                                            if canon in DIAGNOSTIC_SERVICES
                                            or name in DIAGNOSTIC_SERVICES
                                            else None),
                    })
                continue
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
                # canonical OHM type (e.g. "OutletService") when the source had
                # one. Write commands send it alongside the instance alias so
                # the gateway can address the service by class as well. None
                # for REST-sourced devices, which never expose a type.
                "service_type": svc.get("service_type"),
                "unique_id": f"oblo_{dev['id']}_{name}",
                "model": model,
                "manufacturer": manufacturer,
                "label": entity_label({"service": name}),
                "entity_category": ("diagnostic"
                                    if canon in DIAGNOSTIC_SERVICES
                                    or name in DIAGNOSTIC_SERVICES
                                    else None),
            })
    return out