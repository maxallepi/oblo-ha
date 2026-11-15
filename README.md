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
  | `sensor` | power, temperature, humidity, battery and other reported values |
  | `switch` | relays and outlets |
  | `select` | multi-state controls, such as alert modes |
  | `button` | one-shot device actions |

- **Configurable poll interval** — 15 to 3600 seconds, default 60.
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

Change it under **Settings → Devices & services → OBLO Living → Configure**.

## Requirements

- Home Assistant **2024.1** or newer.
- An OBLO Living account with at least one gateway online.
- The integration's brand icon and logo are read by Home Assistant **2026.3** or newer; on older versions the integration simply shows a generic icon.

## Notes

- **Cloud mode.** The integration talks to the OBLO cloud API. The gateway's local MQTT endpoint (port 8883) exists in the code but is not exposed in the config flow yet.
- **Writes are confirmed, not assumed.** A command is only treated as applied once the device reports it back; the broker acknowledges unconditionally, so a lack of errors is never taken as success.

## Issues

Bug reports and feature requests: <https://github.com/maxallepi/oblo-ha/issues>
