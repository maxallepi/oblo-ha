#!/usr/bin/env python3
"""Live test: ObloCloudApi.async_login against cloud.oblo.rs with bogus creds.
Proves the request shape/path/parsing work against the real server.
Expected: ObloAuthError("user does not exist") for unknown identity.
"""
import asyncio, sys, types
from unittest.mock import MagicMock
import ha_stub; ha_stub.install()
sys.modules['homeassistant'] = MagicMock()
sys.modules['homeassistant.config_entries'] = MagicMock()
sys.modules['homeassistant.core'] = MagicMock()
sys.modules['homeassistant.helpers'] = MagicMock()
sys.modules['homeassistant.helpers.aiohttp_client'] = MagicMock()

import aiohttp
from custom_components.oblo_living.cloud_api import ObloCloudApi, ObloAuthError, ObloCloudError

async def main():
    async with aiohttp.ClientSession() as s:
        api = ObloCloudApi(s, "morpheus-probe-does-not-exist@example.com", "x")
        try:
            blob = await api.async_login()
            print("UNEXPECTED success:", blob)
        except ObloAuthError as e:
            print("PASS ObloAuthError:", e)
        except ObloCloudError as e:
            print("OTHER ObloCloudError:", e)
        # also verify the gateway list rejects an empty session
        try:
            await api.async_list_gateways()
            print("UNEXPECTED gateway list success with no session")
        except ObloAuthError as e:
            print("PASS gateway list rejects no-session:", e)
        except ObloCloudError as e:
            print("gateway list error (accepted):", e)

asyncio.run(main())
