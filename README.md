# OBLO Living — Home Assistant Integration (Cloud Polling)

Controls OBLO Living smart-home devices via the OBLO cloud API (no local MQTT needed).

## Install via HACS (recommended)

1. **HACS → Integrations → ⋮ → Custom repositories**  
   Repository: `https://github.com/maxallepi/oblo-ha`  
   Category: **Integration**

2. **Install** → Restart Home Assistant.

3. **Settings → Devices & Services → Add Integration → OBLO Living**  
   Enter your **OBLO cloud email** and **password**.

## What works (0.2.0)

- Cloud authentication via `/auth/login` (session-based)
- Device discovery via `/udm/device/list` + `/udm/device/hg/info/list`
- Coordinator polling every 60s with auto re-login on session expiry
- Entity platform classes for: binary_sensor, sensor, switch, light
- Service-to-entity mapping driven by the real device payload

## Requirements

- Home Assistant 2024.1+
- HACS (for easy updates)

## Local MQTT fallback (planned)

`client.py`, `hub.py`, `entities/` also contain a local MQTT path that uses the gateway's self-signed TLS on port 8883. It is **disabled** in this release (cloud-only config flow). Requires SSO token or rooted phone for credentials.
