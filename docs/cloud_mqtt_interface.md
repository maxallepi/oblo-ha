# cloud_mqtt.py — Cloud MQTT/OHM client interface contract

## Purpose
Provides the OBLO cloud device inventory (object_list with service_list) via OHM
commands over WSS `wss://cloud.oblo.rs:5222`. The REST endpoints only ever return
the gateway object; the real child devices and their service properties live behind
MQTT.

## Required public API (async)

```python
class ObloCloudMqtt:
    def __init__(self, session_id, user_id, username, gateway_serial, hass_loop=None):
        # session_id: from /auth/login response header 'sessionid'
        #             (also available from /auth/retrieveSSOTokens)
        # user_id:    from /auth/retrieveSSOTokens -> userId
        # username:   from /auth/retrieveSSOTokens -> username
        #             (used as the MQTT client id suffix: cli/<username>)
        # gateway_serial: from /udm/device/list -> serial (SN)
        # hass_loop:  optional asyncio loop for HA thread-safety (None = current loop)
        ...

    async def async_connect(self):
        # Open WSS, send MQTT CONNECT, subscribe to the response topic.
        # Raise ObloAuthError on CONNACK != 0.
        ...

    async def async_object_list(self):
        # Publish OHM command to oblo/<userId>/gtw/<gatewaySerial>/ohm/req:
        #   {"name": "get_object_list_by_type_detailed",
        #    "params": {"type": "device"},
        #    "type": "command",
        #    "uid": <incrementing int>,
        #    "sender": "cli/<sessionId>"}   # REQUIRED, see below
        # Wait for the response on oblo/<userId>/cli/<sessionId>/rsp
        # Return params.object_list (list of OHM objects).
        # Raise on timeout / parse error / auth failure.
        ...

    async def async_subscribe_events(self, callback):
        # Optional: subscribe to oblo/<userId>/gtw/<SN>/+/evt for live updates,
        # calling callback(ohm_event_dict) for each event.
        ...

    async def async_close(self):
        # Clean disconnect.
        ...
```

## MQTT protocol details (from app.js)
- **WSS endpoint**: `wss://cloud.oblo.rs:5222`
- **MQTT username**: `cli/<username>` (username from retrieveSSOTokens) — this *is* the
  credential; the broker rejects bogus usernames at CONNECT with rc=5
- **MQTT password**: literal string `password` (shared by all clients)
- **Request topic**: `oblo/{userId}/gtw/{gatewaySerial}/ohm/req`
- **Response topic**: `oblo/{userId}/<sender>/rsp` **and** `.../<sender>/req`
  (subscribe to both; the gateway answers some message classes on `/req`)

### `sender` field — required on every publish
The gateway echoes `sender` back to build the reply topic, so a payload
without it is accepted and then answered into nowhere (no error, no NACK,
just a timeout):

| account | `sender` |
|---|---|
| in `groups: ["admin"]` | `cli/<sessionId>` |
| non-admin with `parentsIds` | `cli/<gateway-owner userId>` |
| otherwise | `cli/<logged-in userId>` |

Established from `app.js`, which sets `sender` unconditionally on both of
its publish paths (`tryPublish` and `tmp_tryPubSub`), and from the 15s
`setTimeout` rejection that matches our own request timeout.
- **Event topic**: `oblo/{userId}/gtw/{SN}/+/evt`
- **Command UID**: incrementing integer per request
- **Response parsing**: JSON payload -> `params.object_list`

## OHM object shape (what normalise_ohm_objects expects)

```json
{
  "id": "abc123",
  "device_name": "Living Room Motion",
  "service_list": [
    {"name": "PirService", "category": "binary_sensor",
     "property_list": [{"name": "State", "value": "1"}]},
    {"name": "TemperatureService", "category": "sensor",
     "property_list": [{"name": "Value", "value": "21.5"}]},
    {"name": "BatteryService", "category": "sensor",
     "property_list": [{"name": "Level", "value": "85"}]}
  ]
}
```

## Integration wiring (already implemented in coordinator.py)

```python
coord = ObloDataCoordinator(hass, api)
coord.set_ohm_client(mqtt_client)   # attach the optional OHM source
# on unload:
if coord.ohm is not None:
    await coord.ohm.async_close()
```

Merge semantics: REST devices and OHM devices are merged by `id`, keeping the record
with the most services/props. OHM records always win because REST carries no services.
An OHM failure is logged and the poll continues with REST data; `ObloAuthError` and
`ObloRateLimitError` propagate unchanged so the coordinator's existing handling applies.

## Testing
`python3 custom_components/oblo_living/tests/ohm_coordinator_test.py` uses a `FakeOhm`
client matching this interface and asserts the merge behaviour (11 checks).
