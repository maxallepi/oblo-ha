# Changelog

All notable changes to the OBLO Living Home Assistant integration.

Versions before 0.2.55 are documented in the git history
(`git log --oneline`).

## 0.2.56

### Added — delete a device from Home Assistant

Home Assistant only offers **Delete** on a device page when the integration
implements `async_remove_config_entry_device`. It did not, so the only option
was enable/disable. It does now.

- **Device menu → Delete** removes the device and every entity that belongs to it.
- The cloud device id is written to the entry's ignore list, so the next poll
  does not silently re-create the device and make Delete look like it did nothing.
- New service **`oblo_living.remove_device`** does the same from an automation:
  pass `device_id` (the HA device registry id) or `entity_id` (any entity of the
  device), plus optional `permanent` to add it to the ignore list in one step.
- **Restoring** a deleted device: the integration's **Configure** page now lists
  the hidden device ids. Clear the id, save, and the integration reloads and
  re-creates the device and its entities.

### Added — typed weather and wind sensors

Weather and wind values used to arrive as bare numbers with no unit and no
device class, so Home Assistant had no idea what they were.

- `WeatherService` now expands into separate typed sensors:
  **Temperature** (°C), **Humidity** (%), **Pressure** (hPa) and **Summary**.
- `WindService` now exposes **Wind Speed** (m/s).
- Each carries the matching `device_class`, so history graphs, unit conversion
  and statistics work as expected.
- An entity is only created when that property actually exists on the service
  (`require_prop`). A weather feed with no pressure reading produces no
  permanently-`unknown` Pressure row.
- Weather and wind reads are **strict**: a missing property returns `unknown`
  instead of falling back to the first property on the service, which could
  show a temperature as a humidity.

### Fixed

- Options flow no longer wipes unrelated options when saving the poll interval.

## 0.2.55

- Brand icon and logo (`brand/`) — read by Home Assistant 2026.3 and newer.
- README rewritten for the current cloud-only integration.
- Accept a non-string `ManufacturerID` (the gateway sends an integer).
- Test fixtures scrubbed of real account, device and serial identifiers.

## 0.2.53

- Platform setup returns `True` even when a platform produces zero entities, so
  an integration with no `switch` devices no longer fails to load.
