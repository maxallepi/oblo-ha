#!/usr/bin/env python3
"""Live probe against cloud.oblo.rs — opt-in, not part of the CI suite.

Usage:
  python3 tests/live_probe.py --identity andrew.theocharides@gmail.com --password 'yourpassword'
  python3 tests/live_probe.py --identity andrew.theocharides@gmail.com --wrong-password

Flags:
  --identity        OBLO cloud email/username (required)
  --password        Plaintext password to test (optional; omit for wrong-password probe)
  --wrong-password  Use a deliberately wrong password to verify 406/00012 path
  --verbose         Print full request/response

Exit codes:
  0 = login succeeded (session_id captured)
  1 = invalid credentials (406/00012 or 409/11020)
  2 = network / TLS / DNS / 5xx / unexpected response
  3 = usage error
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys

import aiohttp

CLOUD_HOST = "https://cloud.oblo.rs:443"
LOGIN_PATH = "/auth/login"

ERR_USER_NOT_FOUND = "11020"
ERR_BAD_CREDENTIALS = "00012"


def md5_hex(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


async def probe(identity: str, secret: str, verbose: bool) -> int:
    body = {
        "identity": identity.strip().lower(),
        "secret": md5_hex(secret),
        "externalLogin": False,
        "isPrivacyPolicyAccepted": True,
        "areTermsOfServiceAccepted": True,
    }
    if verbose:
        print(">>> POST", CLOUD_HOST + LOGIN_PATH)
        print(">>> body:", json.dumps(body))
        print(">>> headers: Content-Type: application/json")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                CLOUD_HOST + LOGIN_PATH,
                json=body,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                text = await resp.text()
                headers = {k.lower(): v for k, v in resp.headers.items()}
                if verbose:
                    print(f"<<< HTTP {resp.status}")
                    print("<<< headers:", json.dumps(headers))
                    print("<<< body:", text[:800])

                # Parse error envelope (array of objects with errorCode)
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    data = None

                code = None
                if isinstance(data, list) and data and isinstance(data[0], dict):
                    code = data[0].get("errorCode")

                # Success
                if resp.status == 200 and code in (None, "00000"):
                    sid = headers.get("sessionid")
                    if not sid and isinstance(data, dict):
                        user = data.get("user") or {}
                        sid = user.get("sessionId") or user.get("_id")
                    print(f"SUCCESS  sessionid={sid or 'in-body'}")
                    return 0

                # Auth failures we recognise
                if code == ERR_USER_NOT_FOUND:
                    print(f"FAIL  invalid_identity  {code}  User does not exist")
                    return 1
                if code == ERR_BAD_CREDENTIALS:
                    print(f"FAIL  invalid_credentials  {code}  Invalid credentials")
                    return 1

                # Other 4xx/5xx
                if 400 <= resp.status < 500:
                    print(f"FAIL  cloud_error  HTTP {resp.status}  code={code}  body={text[:200]}")
                    return 2
                if 500 <= resp.status < 600:
                    print(f"FAIL  server_error  HTTP {resp.status}  body={text[:200]}")
                    return 2

                print(f"FAIL  unexpected  HTTP {resp.status}  code={code}  body={text[:200]}")
                return 2

    except aiohttp.ClientConnectorError as e:
        print(f"FAIL  network  {type(e).__name__}: {e}")
        return 2
    except asyncio.TimeoutError:
        print("FAIL  network  timeout")
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"FAIL  unexpected  {type(e).__name__}: {e}")
        return 2


def main() -> int:
    ap = argparse.ArgumentParser(description="Live probe cloud.oblo.rs login")
    ap.add_argument("--identity", required=True, help="OBLO cloud email/username")
    ap.add_argument("--password", help="Plaintext password to test")
    ap.add_argument("--wrong-password", action="store_true",
                    help="Use a deliberately wrong password (tests 406/00012)")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    if args.wrong_password:
        secret = "definitely-not-the-right-password-123"
    elif args.password is not None:
        secret = args.password
    else:
        ap.error("one of --password or --wrong-password is required")
        return 3

    return asyncio.run(probe(args.identity, secret, args.verbose))


if __name__ == "__main__":
    sys.exit(main())