"""OBLO Living cloud REST client (aiohttp).

Verified live against https://cloud.oblo.rs:443:
  POST /auth/login  body {identity, secret, externalLogin,
                          isPrivacyPolicyAccepted, areTermsOfServiceAccepted}
       -> 409 [{"message":"User does not exist","errorCode":"11020",...}] for
          an unknown identity, which proves the endpoint and body shape.
  Every authenticated call carries the header `sessionId: <session.sessionId>`
  (the SPA's sessionInterceptor attaches exactly that header).

Response envelope is a JSON array; success uses errorCode "00000", errors
carry errorCode/httpCode/message. Special codes handled: "000622" (forced
logout), "881792".

Two behaviours copied from the SPA (cloud.oblo.rs/app.js), both of which
break a naive client:

1. `LoginController.login` sends the password as **plaintext** in the
   request body -- `r.login(identity.toLowerCase(), e.user.secret)` and
   `AuthService.login` posts `{identity, secret, ...}` with NO hashing
   (verified 2026-11-12 against the live app.js; `md5.createHash` appears
   only in GatewayAuthService, i.e. the LOCAL gateway login, never the
   cloud path). The cloud hashes server-side, so a client that pre-hashes
   gets md5(md5(pw)) compared against md5(pw) -> 00012 "Invalid
   credentials" even with a correct password. That was v0.2.0-0.2.9's
   bug; the secret must go on the wire exactly as the user typed it.
2. The session id does NOT come back in the body. It arrives in the
   **`sessionid` response header**:
   `createSession(e.data.user.username, r, e.data.user, e.headers("sessionid"))`.
   The body holds `{user: {_id, username, groups, devices, ...}}`.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging

import aiohttp

from .const import CLOUD_HOST
from .entity_map import canonical_prop

_LOGGER = logging.getLogger(__name__)

LOGIN_PATH = "/auth/login"
GATEWAY_LIST_PATH = "/udm/device/list"
GATEWAY_INFO_PATH = "/udm/device/hg/info/list"

ERR_OK = "00000"
ERR_USER_NOT_FOUND = "11020"
ERR_BAD_CREDENTIALS = "00012"
ERR_FORCED_LOGOUT = "000622"
ERR_SESSION_REJECTED = "00003"
# Returned as HTTP 429 (or 4xx) after repeated failed logins: the ACCOUNT is
# temporarily blocked, so even a CORRECT password fails until the block lifts.
# Observed live: {"httpCode":"429","errorCode":"000016","message":"Too many
# failed requests made! This account has been temporarily blocked..."}
ERR_TOO_MANY_ATTEMPTS = "000016"

# Codes that mean "the credentials/session are the problem", not the network.
# The cloud answers a wrong password with HTTP 406 + 00012, and an unknown
# identity with HTTP 409 + 11020 -- both are auth failures and must surface as
# invalid_auth, never as cannot_connect.
AUTH_ERROR_CODES = {
    ERR_USER_NOT_FOUND, ERR_BAD_CREDENTIALS, ERR_FORCED_LOGOUT,
    ERR_SESSION_REJECTED, "11021", "881792",
}
# HTTP statuses the cloud uses for auth problems (406 = invalid credentials)
AUTH_HTTP_STATUSES = (401, 403, 406, 409)


def md5_hex(text: str) -> str:
    """Lower-case hex MD5 of text.

    LOCAL GATEWAY LOGIN ONLY (`GatewayAuthService.login` in the SPA hashes the
    password this way against the gateway's own HTTP API). The CLOUD login must
    send the plaintext password -- see the module docstring.
    """
    return hashlib.md5(text.encode("utf-8")).hexdigest()


class ObloCloudError(Exception):
    """Cloud call failed."""


class ObloAuthError(ObloCloudError):
    """Credentials rejected or session expired."""


class ObloResponseError(ObloCloudError):
    """Cloud responded but body/header shape was unexpected (5xx, parse fail, missing fields)."""


class ObloRateLimitError(ObloCloudError):
    """Account temporarily blocked after too many failed attempts (429/000016)."""


def _first_error(data):
    """Return (errorCode, message) from the array envelope, or (None, None)."""
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0].get("errorCode"), data[0].get("message")
    if isinstance(data, dict):
        return data.get("errorCode"), data.get("message")
    return None, None


class ObloCloudApi:
    """Minimal cloud client: login, list gateways, fetch gateway device info."""

    def __init__(self, session: aiohttp.ClientSession, identity: str, secret: str,
                 host: str | None = None):
        """`secret` is the PLAINTEXT password -- the cloud hashes server-side.

        (The old `secret_hashed` kwarg is gone: v0.2.0-0.2.9 pre-hashed the
        password with md5, which the cloud never wanted -- it produced 00012
        even for correct credentials.)
        """
        self._session = session
        self._identity = identity
        self._secret = secret
        self._host: str = host or CLOUD_HOST
        self.last_headers: dict[str, str] = {}
        self.session_id = None
        self.user_id = None
        self.username = None
        self.groups = []

    # -- auth ---------------------------------------------------------------
    async def async_login(self):
        """POST /auth/login; populates session_id/user_id/username/groups.

        Matches the SPA exactly: identity lower-cased, secret sent as PLAINTEXT
        (the cloud hashes server-side). The session id is read from the
        `sessionid` response header; the user object from body["user"].
        """
        body = {
            "identity": self._identity.strip().lower(),
            "secret": self._secret,          # PLAINTEXT -- do NOT md5
            "externalLogin": False,
            "isPrivacyPolicyAccepted": True,
            "areTermsOfServiceAccepted": True,
        }
        data = await self._request("POST", LOGIN_PATH, json=body, auth=False)
        headers = dict(self.last_headers)
        # Body shape per SPA: {user: {_id, username, groups, devices, ...}}
        blob = data
        if isinstance(data, dict) and isinstance(data.get("user"), dict):
            blob = data["user"]
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            blob = data[0]  # error envelope path (shouldn't reach here)
        if not isinstance(blob, dict):
            raise ObloCloudError(f"unexpected login response: {type(blob).__name__}")
        # session id: header first, body fallbacks afterwards
        self.session_id = (headers.get("sessionid")
                           or blob.get("sessionId")
                           or blob.get("session")
                           or blob.get("_id"))
        self.user_id = blob.get("_id") or blob.get("userId") or blob.get("userID")
        self.username = blob.get("username")
        self.groups = blob.get("groups") or []
        if not self.session_id:
            _LOGGER.error("login 200 but no session id; body keys=%s", sorted(blob))
            raise ObloResponseError(
                "login succeeded but no sessionid header/body id in response")
        # The cloud MQTT broker authenticates on the USERNAME alone (password is
        # the shared literal "password"), so `blob["username"]` is a credential.
        # Log only its shape: presence, length and a masked form. Also log the
        # field NAMES of the user object -- never values -- so a missing
        # `username` key is obvious in one paste instead of another round trip.
        uname = self.username
        _LOGGER.info(
            "cloud login ok user_id=%s groups=%s username=%s present=%s user_keys=%s",
            self.user_id, self.groups,
            (f"{uname[:4]}...{uname[-4:]} (len={len(uname)})"
             if isinstance(uname, str) and len(uname) > 8
             else (f"{uname!r} (len={len(uname)})" if isinstance(uname, str)
                   else repr(uname))),
            bool(uname), sorted(blob.keys()))
        return blob

    # -- data ---------------------------------------------------------------
    async def async_list_gateways(self):
        """GET /udm/device/list -> list of gateway dicts."""
        data = await self._request(
            "GET", GATEWAY_LIST_PATH,
            params={"owner": "true", "page": 1, "limit": 100,
                    "orderBy": "connectionStatus"})
        if isinstance(data, dict):
            data = data.get("data") or []
        return data if isinstance(data, list) else []

    async def async_gateway_devices(self):
        """GET /udm/device/hg/info/list -> gateway(s) with device details."""
        data = await self._request(
            "GET", GATEWAY_INFO_PATH,
            params={"details": "true", "skip": "", "limit": "",
                    "userId": self.user_id or ""})
        if isinstance(data, dict):
            data = data.get("data") or []
        return data if isinstance(data, list) else []

    # -- transport ----------------------------------------------------------
    async def _request(self, method, path, auth=True, **kwargs):
        headers = {"Accept": "application/json"}
        if auth:
            headers["sessionId"] = self.session_id or ""
        url = f"{self._host}{path}"
        try:
            async with self._session.request(method, url, headers=headers,
                                             timeout=aiohttp.ClientTimeout(total=30),
                                             **kwargs) as resp:
                try:
                    data = await resp.json(content_type=None)
                except ValueError:
                    data = await resp.text()
                code, message = _first_error(data)
                if resp.status == 429 or code == ERR_TOO_MANY_ATTEMPTS:
                    # The account is blocked, not the network. Callers must
                    # report this separately: a correct password also fails here.
                    _LOGGER.warning("cloud blocked the account: status=%s code=%s msg=%s",
                                    resp.status, code, message)
                    raise ObloRateLimitError(
                        message or "too many failed attempts - account temporarily blocked")
                if code in AUTH_ERROR_CODES or resp.status in AUTH_HTTP_STATUSES:
                    _LOGGER.info("cloud auth failure: HTTP %s code=%s message=%s",
                                 resp.status, code, message)
                    raise ObloAuthError(
                        f"{message or 'credentials rejected'} ({code or resp.status})")
                if resp.status >= 500:
                    _LOGGER.warning("cloud server error: status=%s code=%s body=%s",
                                    resp.status, code, str(data)[:200])
                    raise ObloResponseError(f"HTTP {resp.status}: {message or code}")
                if resp.status >= 400:
                    _LOGGER.warning("cloud HTTP error: status=%s code=%s body=%s",
                                    resp.status, code, str(data)[:200])
                    raise ObloCloudError(f"HTTP {resp.status}: {message or code}")
                if code not in (None, ERR_OK):
                    raise ObloCloudError(f"cloud error {code}: {message}")
                self.last_headers = {k.lower(): v for k, v in resp.headers.items()}
                return data
        except aiohttp.ClientError as err:
            raise ObloCloudError(f"connection to cloud failed: {err}") from err
        except asyncio.TimeoutError as err:
            raise ObloCloudError("connection to cloud timed out") from err


def normalise_devices(gateways):
    """Flatten gateway/device payloads into the internal device dict shape.

    Accepts the several shapes the cloud uses (gateway list, hg/info list,
    embedded `devices` arrays) and returns [{id,name,class,services}].
    Gateway objects (have `serialNumber` + `devices` list) are skipped;
    only their embedded devices are yielded.
    Unknown fields are ignored rather than fatal.
    """
    out = []
    seen = set()

    def is_gateway(obj):
        if not isinstance(obj, dict):
            return False
        # Cloud gateway container: type/class == "hg" (home gateway).
        # The live payload for Andrew's install has NO `devices` key at all,
        # so this check must come first or the gateway is emitted as a device.
        if obj.get("type") == "hg" or obj.get("class") == "hg":
            return True
        devs = obj.get("devices")
        if not isinstance(devs, list):
            return False
        if not devs:
            # gateway containers with an empty device list carry serialNumber
            return "serialNumber" in obj
        # gateway containers have a `devices` array whose elements carry `deviceId`
        return all(isinstance(d, dict) and d.get("deviceId") is not None for d in devs)

    def add(obj):
        if not isinstance(obj, dict):
            return
        did = (obj.get("deviceId") or obj.get("id") or obj.get("_id")
               or obj.get("deviceID"))
        if did is None or did in seen:
            return
        seen.add(did)
        services = []
        for svc in (obj.get("services") or []):
            if isinstance(svc, str):
                services.append({"service": svc, "props": []})
            elif isinstance(svc, dict):
                name = svc.get("service") or svc.get("name") or svc.get("serviceName")
                props = svc.get("props") or svc.get("properties") or []
                if isinstance(props, dict):
                    props = list(props.items())
                # REST payloads use the same wire spellings as OHM
                # ("PlugState"), so normalise here too -- otherwise a value
                # from this path lands under a key the entity never reads.
                props = [[canonical_prop(p[0]), p[1]] if isinstance(p, (list, tuple))
                         and len(p) >= 2 else p for p in props]
                services.append({"service": name, "props": list(props)})
        out.append({
            "id": did,
            "name": obj.get("deviceName") or obj.get("name") or str(did),
            "class": obj.get("type") or obj.get("deviceType") or obj.get("class") or "",
            "services": services,
        })

    for gw in gateways or []:
        if not isinstance(gw, dict):
            continue
        if is_gateway(gw):
            for dev in gw.get("devices") or []:
                add(dev)
        else:
            add(gw)
            for dev in (gw.get("devices") or []):
                add(dev)
    return out
