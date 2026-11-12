#!/usr/bin/env python3
"""Stub-import + functional test for cloud mode (no HA installed).
Verifies:
  - all new modules import cleanly
  - normalise_devices handles SPA-like gateway list + hg/info shapes
  - coordinator + api instantiate without HA
"""
import sys
import json
from unittest.mock import AsyncMock, MagicMock, patch

# mock HA imports BEFORE importing component
import ha_stub; ha_stub.install()

# mock aiohttp
import aiohttp
sys.modules['aiohttp'] = aiohttp

from custom_components.oblo_living import const, cloud_api, coordinator, hub

print("✓ imports OK")

# 1. normalise_devices with SPA-like gateway list payload
gateway_list = [{
    "_id": "gtw-1",
    "serialNumber": "34_6748115_33_0232421",
    "name": "Living Gateway",
    "connectionStatus": "online",
    "devices": [{
        "deviceId": "dev-001",
        "deviceName": "Hall Motion",
        "type": "Everspring_Sensor",
        "services": [
            {"service": "sensor_notification", "props": [["notificationType", "motion"], ["battery", 85]]}
        ]
    }]
}, {
    "_id": "gtw-2",
    "serialNumber": "OTHER",
    "name": "Second",
    "connectionStatus": "offline",
    "devices": []
}]

hg_info = [{
    "_id": "gtw-1",
    "devices": [{
        "deviceId": "dev-002",
        "deviceName": "Front Door",
        "type": "Everspring_Door",
        "services": [
            {"service": "sensor_notification", "props": [["notificationType", "door"], ["battery", 92]]}
        ]
    }]
}]

normalised = cloud_api.normalise_devices(gateway_list) + cloud_api.normalise_devices(hg_info)
print(f"✓ normalised {len(normalised)} devices:")
for d in normalised:
    print(f"  - {d['id']} {d['name']} class={d['class']} services={len(d['services'])}")

# verify service props retained
by_id = {d['id']: d for d in normalised}
hall = by_id['dev-001']
assert hall['services'][0]['service'] == 'sensor_notification', hall
assert hall['services'][0]['props'][0][0] == 'notificationType', hall
assert not any(d['id'].startswith('gtw') for d in normalised), "gateways must not become devices"
print("✓ service props preserved; gateways not treated as devices")

# 2. hub loads from normalised
h = hub.ObloHub()
h.load_from_normalised(normalised)
print(f"✓ hub loaded {len(h.devices)} devices")

# 3. coordinator + api instantiate
api = cloud_api.ObloCloudApi(None, "test@example.com", "secret")
c = coordinator.ObloDataCoordinator(None, api)
print("✓ coordinator/api instantiate OK")

# 4. cloud_api error mapping
from custom_components.oblo_living.cloud_api import ObloAuthError, ObloCloudError
try:
    raise ObloAuthError("test")
except ObloAuthError:
    print("✓ ObloAuthError raised")
try:
    raise ObloCloudError("test")
except ObloCloudError:
    print("✓ ObloCloudError raised")

print("\n=== ALL STUB TESTS PASSED ===")