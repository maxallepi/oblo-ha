#!/usr/bin/env python3
"""OBLO SimpleAlert PropAlertType read-back probe.

WHAT THIS ANSWERS
-----------------
Does `SimpleAlertService.PropAlertType` actually latch on the gateway, or does
every read return the default "0" regardless of what we wrote?

v0.2.39 makes the select/button trust the *remembered* write instead of the
read-back, because a single "0" read was silently downgrading every alert to
NOTIFICATION. That is the right shape for a write-only property -- but it is
only *honest* if the write latches. If it does not, the UI now displays a type
the siren never uses: the same false-negative class this release exists to
delete, pointing the other way.

The probe distinguishes the two without anyone listening to the siren:

    write 3 (EMERGENCY) -> read -> write 1 (ALARM) -> read

  all reads identical (e.g. "0","0","0")  => READ IS A CONSTANT DEFAULT.
                                             Property is write-only; the
                                             read-back primitive is useless
                                             and the fix is correct.
  reads follow the writes ("3" then "1")  => WRITE LATCHES. The read-back is
                                             real, so v0.2.32's "sent='1'
                                             readback='0'" means the *write*
                                             was rejected. Investigate the
                                             wire payload, not the read.
  first follows, second does not          => LATCHES THEN REVERTS. Needs the
                                             evt log to see who reverts it.

USAGE (on the Home Assistant box, inside the HA container or venv):

    python3 scratch/oblo_alert_type_probe.py --config-dir /config

Credentials come from Home Assistant's own config entry
(`.storage/core.config_entries`, domain `oblo_living`) so nothing is typed or
printed. They may also come from OBLO_IDENTITY / OBLO_SECRET in the
environment. Secrets are never echoed: only their length is reported.

OFFLINE SELF-TEST (no HA, no creds, no network -- stdlib only):

    python3 scratch/oblo_alert_type_probe.py --fake constant
    python3 scratch/oblo_alert_type_probe.py --fake latched
    python3 scratch/oblo_alert_type_probe.py --fake revert

Exit codes: 0 = probe ran and classified, 1 = could not run, 2 = undetermined.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

PROP = "PropAlertType"
DEFAULT_SERVICE = "SimpleAlertService"
WRITE_SEQUENCE = ("3", "1")  # EMERGENCY, then ALARM -- both differ from "0"
READ_SETTLE_S = 1.5

CONSTANT = "CONSTANT_DEFAULT"
LATCHES = "WRITE_LATCHES"
REVERTS = "LATCHES_THEN_REVERTS"
MIXED = "UNDETERMINED"


def _norm(value) -> str | None:
    """Normalise a wire value to a bare ordinal string, or None if unknown."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip().strip("'\"")
    return text or None


def classify(reads: list) -> tuple[str, str]:
    """Classify (baseline, after_write_3, after_write_1). Pure function."""
    base, after3, after1 = (_norm(r) for r in reads)
    seq = (base, after3, after1)

    if base is None or after3 is None or after1 is None:
        return MIXED, ("a read returned no value at all (%r) -- the property "
                       "may be absent from the reply rather than defaulted; "
                       "re-run and check the reply shape" % (reads,))

    if base == after3 == after1:
        return CONSTANT, (
            "every read returned %r regardless of two different writes. The "
            "read-back is a constant default, not a measurement: "
            "PropAlertType is write-only and the v0.2.39 fix is correct." % base)

    if after3 == "3" and after1 == "1":
        return LATCHES, (
            "reads followed the writes (%s -> %s). The read-back is real, so "
            "v0.2.32's sent='1' readback='0' means the TYPE WRITE ITSELF was "
            "rejected. Inspect the wire payload (value type: string vs int) "
            "before trusting the select." % (after3, after1))

    if after3 == "3" and after1 != "1":
        return REVERTS, (
            "the first write latched (%s) but the second did not (%s). "
            "Something reverts the property between writes -- check the evt "
            "log around the write stamps for a gateway-sourced change."
            % (after3, after1))

    return MIXED, "reads did not follow the writes in any consistent pattern: %r" % (seq,)


# --------------------------------------------------------------------------
# Fake client: exercises the probe + classifier with no network at all.
# --------------------------------------------------------------------------
class FakeClient:
    def __init__(self, behaviour: str):
        self.behaviour = behaviour
        self.stored = "0"
        self.writes = []
        self.reads = []
        self._latched_once = False

    async def async_connect(self):
        return None

    async def async_close(self):
        return None

    async def async_object_list(self):
        return [{
            "id": "15",
            "device_name": "Gateway Simple Alert 15",
            "service_list": [{"name": DEFAULT_SERVICE, "type": "SimpleAlert"}],
        }]

    async def async_set_property(self, service_id, service_name,
                                 property_name, value, service_type=None,
                                 timeout=None):
        self.writes.append((property_name, value))
        if self.behaviour == "latched":
            self.stored = _norm(value)
        elif self.behaviour == "revert":
            # Latch the first write, then lose it: the second write is accepted
            # by the transport and silently ignored by the device.
            self.stored = _norm(value) if not self._latched_once else "0"
            self._latched_once = True
        # constant: ignore the write entirely, like a write-only property
        return {"code": 0}

    async def async_get_property(self, service_id, service_name,
                                 property_name, service_type=None,
                                 timeout=None):
        self.reads.append(self.stored)
        return self.stored


# --------------------------------------------------------------------------
# Real client: the integration's own code, against the live gateway.
# --------------------------------------------------------------------------
def load_entry_credentials(config_dir: Path) -> dict:
    """Read the oblo_living config entry data. Never logs values."""
    storage = config_dir / ".storage" / "core.config_entries"
    if not storage.is_file():
        raise SystemExit(
            "no %s -- run with --config-dir pointing at the HA config "
            "directory, or set OBLO_IDENTITY/OBLO_SECRET" % storage)
    raw = json.loads(storage.read_text())
    entries = (raw.get("data") or {}).get("entries") or []
    for entry in entries:
        if entry.get("domain") == "oblo_living":
            return entry.get("data") or {}
    raise SystemExit("no oblo_living config entry found in %s" % storage)


def _gateway_serial(raw) -> str | None:
    """Pull the gateway serial out of whatever list_gateways returned."""
    items = raw if isinstance(raw, list) else [raw]
    for item in items:
        stack = [item]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for key in ("serialNumber", "serial", "sn", "deviceSN"):
                    val = node.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return None


async def build_real_client(config_dir: Path, identity: str | None,
                            secret: str | None):
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))

    from custom_components.oblo_living.cloud_api import ObloCloudApi
    from custom_components.oblo_living.cloud_mqtt import ObloCloudMqtt

    if not identity or not secret:
        data = load_entry_credentials(config_dir)
        identity = identity or data.get("identity")
        secret = secret or data.get("secret")
    if not identity or not secret:
        raise SystemExit("no cloud credentials: pass --identity/--secret, set "
                         "OBLO_IDENTITY/OBLO_SECRET, or point --config-dir at "
                         "a config dir with an oblo_living entry")

    api = ObloCloudApi(identity, secret)
    await api.async_login()
    serial = _gateway_serial(await api.async_list_gateways())
    if not serial:
        raise SystemExit("logged in but no gateway serial in the gateway list")

    session_id = str(getattr(api, "session_id", "") or "")
    user_id = str(getattr(api, "user_id", "") or "")
    username = str(getattr(api, "username", "") or "")
    missing = [name for name, val in (("session_id", session_id),
                                      ("user_id", user_id),
                                      ("username", username)) if not val]
    if missing:
        raise SystemExit("login succeeded but the session lacks %s -- the OHM "
                         "topic cannot be built" % ", ".join(missing))
    client = ObloCloudMqtt(
        session_id=session_id,
        user_id=user_id,
        username=username,
        gateway_serial=serial,
        groups=list(getattr(api, "groups", []) or []),
    )
    await client.async_connect()
    return client, {
        "identity_len": len(identity or ""),
        "secret_len": len(secret or ""),
        "gateway_serial_suffix": serial[-4:],
    }


def discover_target(objects, service_name: str):
    """Find the device id + service type that carries the alert service."""
    for obj in objects or []:
        did = obj.get("id")
        for svc in obj.get("service_list") or []:
            if svc.get("name") == service_name:
                return str(did), svc.get("type") or svc.get("category")
    return None, None


async def run_probe(client, service_name: str, device_id: str | None,
                    settle: float, progress=print) -> dict:
    reads = []
    baseline = await client.async_get_property(device_id, service_name, PROP)
    reads.append(baseline)
    progress("baseline read: %r" % (_norm(baseline),))

    for value in WRITE_SEQUENCE:
        await client.async_set_property(device_id, service_name, PROP, value)
        progress("wrote %r, settling %ss" % (value, settle))
        await asyncio.sleep(settle)
        got = await client.async_get_property(device_id, service_name, PROP)
        reads.append(got)
        progress("  read back: %r" % (_norm(got),))
    return {"reads": [_norm(r) for r in reads]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe whether SimpleAlert PropAlertType latches.")
    parser.add_argument("--config-dir", default="/config",
                        help="Home Assistant config dir (default /config)")
    parser.add_argument("--identity", default=os.environ.get("OBLO_IDENTITY"))
    parser.add_argument("--secret", default=os.environ.get("OBLO_SECRET"))
    parser.add_argument("--service-name", default=DEFAULT_SERVICE)
    parser.add_argument("--device-id", default=None,
                        help="object id; discovered from object_list if omitted")
    parser.add_argument("--settle", type=float, default=READ_SETTLE_S)
    parser.add_argument("--fake", choices=("constant", "latched", "revert"),
                        help="offline self-test, no network or credentials")
    parser.add_argument("--json", action="store_true", help="machine output")
    args = parser.parse_args(argv)

    def progress(msg):
        if not args.json:
            print(msg)

    async def _run():
        meta = {}
        if args.fake:
            client = FakeClient(args.fake)
            device_id, stype = "15", "SimpleAlert"
        else:
            client, meta = await build_real_client(
                Path(args.config_dir), args.identity, args.secret)
            objects = await client.async_object_list()
            device_id, stype = discover_target(objects, args.service_name)
            if device_id is None:
                device_id, stype = args.device_id, None
            if device_id is None:
                raise SystemExit("no %s service in object_list -- pass "
                                 "--device-id" % args.service_name)
            progress("target: device=%s service=%s type=%s"
                     % (device_id, args.service_name, stype))
        try:
            result = await run_probe(client, args.service_name, device_id,
                                     args.settle, progress)
        finally:
            try:
                await client.async_close()
            except Exception:
                pass
        verdict, reason = classify(result["reads"])
        result.update({"verdict": verdict, "reason": reason,
                       "device_id": device_id, "service_name": args.service_name,
                       "service_type": stype, "meta": meta,
                       "writes": list(getattr(client, "writes", []))})
        return result

    result = asyncio.run(_run())
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("\nreads: %s" % " -> ".join(result["reads"]))
        print("VERDICT: %s" % result["verdict"])
        print(result["reason"])

    return 0 if result["verdict"] != MIXED else 2


if __name__ == "__main__":
    sys.exit(main())
