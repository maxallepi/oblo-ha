"""OBLO Living hub: one MQTT connection, event stream -> device registry.

Protocol (from APK + cloud SPA + gateway backup ohm.db):
- All gateway events arrive on the single wildcard topic oblo/<uid>/gtw/<MAC>/ohm/evt.
- Each payload carries a `type` token; per-device updates are
  DevicePropertyChangedEvt {deviceId, serviceName, propertyName, propertyValue}
  and DeviceStatusChangedEvt {deviceId, status, statusInfo}.
- Device identity/names come from the REST device list or ohm.db backup.
"""
import json

from .const import DEVICE_SERVICES
from .entity_map import build_entities


class ObloDevice:
    """One physical device; entities keyed by serviceName."""

    def __init__(self, device_id, name="", device_type=""):
        self.device_id = device_id
        self.name = name
        self.device_type = device_type
        self.properties = {}   # serviceName -> {propertyName: value}
        self.status = None

    def apply_property(self, service, prop, value):
        self.properties.setdefault(service, {})[prop] = value


class ObloHub:
    """Holds the device registry; feeds from any MQTT client's on_message."""

    def __init__(self, devices=None):
        self.devices = devices or {}  # deviceId -> ObloDevice
        self.listeners = []           # callbacks(device_id) after each update

    def load_device_list(self, rest_devices):
        """Seed registry from the REST /udm/device/list payload."""
        for d in rest_devices or []:
            did = d.get("id") or d.get("deviceId")
            if did is not None:
                self.devices[did] = ObloDevice(did, d.get("name", ""),
                                               d.get("type", ""))

    def load_from_backup(self, backup_devices):
        """Seed registry from ohm.db dump (list of dicts with id,name,class,services)."""
        for d in backup_devices or []:
            did = d.get("id")
            if did is not None:
                self.devices[did] = ObloDevice(did, d.get("name", ""),
                                               d.get("class", ""))

    def load_from_normalised(self, devices):
        """Seed registry from cloud_api.normalise_devices output."""
        self.load_from_backup(devices)

    def build_entity_map(self, backup_devices):
        """Return entity map from the real ohm.db dump."""
        return build_entities(backup_devices)

    def handle_message(self, topic, payload_bytes):
        try:
            payload = json.loads(payload_bytes)
        except (ValueError, TypeError):
            return None
        etype = payload.get("type") or payload.get("eventType")
        if not etype:
            return None
        did = payload.get("deviceId")
        changed = []
        if did is not None and did in self.devices:
            dev = self.devices[did]
            if etype.endswith("DevicePropertyChangedEvt") or etype.endswith("PropertyChangedEvt"):
                dev.apply_property(payload.get("serviceName", ""),
                                   payload.get("propertyName"),
                                   payload.get("propertyValue"))
                changed.append(did)
            elif etype.endswith("DeviceStatusChangedEvt"):
                dev.status = {"status": payload.get("status"),
                              "info": payload.get("statusInfo")}
                changed.append(did)
            elif etype.endswith("DeviceRenamedEvt"):
                dev.name = payload.get("deviceName", dev.name)
                changed.append(did)
        for cb in self.listeners:
            for d in changed:
                cb(d)
        return etype
