# OBLO Living — Home Assistant Integration

Home Assistant custom integration for **OBLO Living** gateways and devices.

It signs in to your OBLO account, discovers every gateway and device on it, and exposes them as standard Home Assistant entities. Gateway events arrive over cloud MQTT; a periodic poll is the safety net behind them.

## Features

- **Cloud sign-in** with your OBLO account (email + password). The session is cached and re-authenticated automatically when it expires.
- **Discovery** across all gateways on the account — new devices appear without a restart.
- **Live updates** from the gateway over cloud MQTT, with a configurable poll as fallback.
- **Entities**:

  | Platform | What it covers |
  |---|---|
  | `binary_sensor` | motion, door/window, flood, smoke and similar on/off states |
  | `sensor` | power, temperature, humidity, battery, **weather (temp/humidity/pressure/summary)**, **wind speed** and other reported values |
  | `switch` | relays and outlets |
  | `select` | multi-state controls, such as alert modes |
  | `button` | one-shot device actions |

- **Weather sensors have units and device classes** — Temperature (°C), Humidity (%), Pressure (hPa), Summary, Wind Speed (m/s). They are created only when the property exists on the device, so no phantom `unknown` rows.
- **Configurable poll interval** — 15 to 3600 seconds, default 60.
- **Hidden device IDs** — devices deleted from Home Assistant are recorded here; they are never re-created by the poll. Clear an id and save to restore the device.
- **No secrets in logs** — credentials, session IDs and hashes are redacted from all log output.

## Install

### HACS (recommended)

1. **HACS → Integrations → ⋮ → Custom repositories**
   - Repository: `https://github.com/maxallepi/oblo-ha`
   - Category: **Integration**
2. **Install**, then restart Home Assistant.
3. **Settings → Devices & services → Add integration → OBLO Living**
4. Enter your **OBLO account email** and **password**.

### Manual

Copy the `custom_components/oblo_living` folder into your Home Assistant `config/custom_components/` directory and restart.

## Configuration

| Option | Default | Range |
|---|---|---|
| Scan interval | 60 s | 15–3600 s |
| Hidden device IDs | — | comma-separated cloud device ids |

Change it under **Settings → Devices & services → OBLO Living → Configure**.

### Removing a device

Two ways to delete an OBLO device and all its entities from Home Assistant:

1. **Device page** → click **Delete** (HA only shows this button because the integration implements `async_remove_config_entry_device`). The cloud device id is added to the ignore list automatically so it does not reappear.
2. **Service call** `oblo_living.remove_device`:
   - `device_id` (HA device registry id, visible in the device page URL) **or**
   - `entity_id` (any entity that belongs to the device)
   - `permanent` (optional, default `false`) — also adds the cloud device id to the integration's ignore list so it survives a restart.

To restore a deleted device, go to the integration **Configure** page, remove the id from **Hidden device IDs**, and save. The integration reloads and re-creates the device and its entities.

## Services

| Service | Purpose |
|---|---|
| `oblo_living.set_property` | Write one property to the gateway and **confirm it by reading the value back**. Raises if the gateway accepts the command but the value does not change — a successful call means the device actually moved. |
| `oblo_living.get_property` | Read one property straight from the gateway, bypassing the cached coordinator state. Use it to verify a write independently. |
| `oblo_living.remove_device` | Delete a device and all its entities from Home Assistant. See **Removing a device** above. |

## Requirements

- Home Assistant **2024.1** or newer.
- An OBLO Living account with at least one gateway online.
- The integration's brand icon and logo are read by Home Assistant **2026.3** or newer; on older versions the integration simply shows a generic icon.

## Notes

- **Cloud mode.** The integration talks to the OBLO cloud API. The gateway's local MQTT endpoint (port 8883) exists in the code but is not exposed in the config flow yet.
- **Writes are confirmed, not assumed.** A command is only treated as applied once the device reports it back; the broker acknowledges unconditionally, so a lack of errors is never taken as success.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## Issues

Bug reports and feature requests: <https://github.com/maxallepi/oblo-ha/issues>