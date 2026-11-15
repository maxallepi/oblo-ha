"""Platform setup must return truthy even when the platform has 0 entities.

Regression test for the v0.2.39-v0.2.52 defect where every platform's
async_setup_entry returned `async_setup_dynamic_platform(...)`, whose result was
`len(first)` -- the number of entities created on the first pass. When a
platform legitimately had zero entities (e.g. button, when no siren is
enrolled), that returned 0.

HA's entity component does:

    if not await component.async_setup_entry(entry):
        raise ConfigEntryError("Failed to set up the <platform> platform.")

(see homeassistant/components/button/__init__.py). A falsy return from ANY one
platform tears the WHOLE integration entry down -- every other platform's
entities vanish and the user sees "Failed to set up the oblo_living button
platform", plus the registry shows old entities as "no longer being provided".

Setup succeeding is independent of how many entities exist, so the helper must
return True. This test locks that contract for every platform and for the
zero-entity case specifically.

Run: python3 platform_setup_return_test.py
"""
import asyncio
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(COMPONENT))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import ha_stub  # noqa: E402,F401

from custom_components.oblo_living.const import DOMAIN  # noqa: E402

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {label}"
          + (f"  <- {detail}" if detail and not cond else ""))


class FakeCoordinator:
    """Empty device tree -- every platform will produce zero entities."""
    def __init__(self):
        self.data = []
        self.devices = []
        self._listeners = []

    def async_add_listener(self, cb):
        self._listeners.append(cb)
        return lambda: None


class FakeEntry:
    entry_id = "test_entry"

    def async_on_unload(self, fn):
        pass


class FakeHass:
    def __init__(self):
        self.data = {DOMAIN: {"test_entry": {"coordinator": FakeCoordinator()}}}


def run_platform(module_name):
    """Run a platform's async_setup_entry the way HA does; return its result."""
    mod = importlib.import_module(f"custom_components.oblo_living.{module_name}")
    added = []
    ret = asyncio.run(mod.async_setup_entry(
        FakeHass(), FakeEntry(), lambda ents: added.extend(ents)))
    return ret, added


def main():
    init = importlib.import_module("custom_components.oblo_living")

    # Zero entities in the tree -> every platform must still report success.
    for p in init.PLATFORMS:
        ret, added = run_platform(p)
        check(f"platform '{p}' returns truthy with 0 entities",
              bool(ret), f"returned {ret!r} (HA would raise ConfigEntryError)")
        check(f"platform '{p}' added 0 entities (tree empty)",
              len(added) == 0, f"added {len(added)}")

    # The specific real-world failure: button with no siren -> 0 entities.
    ret, added = run_platform("button")
    check("button platform is the documented failure mode", bool(ret),
          f"returned {ret!r}; this is the exact v0.2.39 crash")
    check("button added nothing but setup succeeded", len(added) == 0)

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())