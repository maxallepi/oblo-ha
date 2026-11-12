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
"""
from __future__ import annotations

import logging

import aiohttp

from .const import CLOUD_HOST

_LOGGER = logging.getLogger(__name__)

LOGIN_PATH = "/auth/login"
GATEWAY_LIST_PATH = "/udm/device/list"
GATEWAY_INFO_PATH = "/udm/device/hg/info/list"

ERR_OK = "00000"
ERR_USER_NOT_FOUND = "11020"
ERR_FORCED_LOGOUT = "000622"


class ObloCloudError(Exception):
    """Cloud call failed."""


class ObloAuthError(ObloCloudError):
    """Credentials rejected or session expired."""


def _first_error(data):
    """Return (errorCode, message) from the array envelope, or (None, None)."""
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0].get("errorCode"), data[0].get("message")
    if isinstance(data, dict):
        return data.get("errorCode"), data.get("message")
    return None, None


class ObloCloudApi:
    """Minimal cloud client: login, list gateways, fetch gateway device info."""

    def __init__(self, session: aiohttp.ClientSession, identity: str, secret: str):
        self._session = session
        self._identity = identity
        self._secret = secret
        self.session_id = None
        self.user_id = None
        self.username = None
        self.groups = []

    # -- auth ---------------------------------------------------------------
    async def async_login(self):
        """POST /auth/login; populates session_id/user_id/username/groups."""
        body = {
            "identity": self._identity,
            "secret": self._secret,
            "externalLogin": False,
            "isPrivacyPolicyAccepted": True,
            "areTermsOfServiceAccepted": True,
        }
        data = await self._request("POST", LOGIN_PATH, json=body, auth=False)
        # Success shape: {sessionId, userId, username, groups, ...} either at
        # the top level or nested under "session".
        blob = data
        if isinstance(data, list) and data:
            blob = data[0]
        if isinstance(blob, dict) and isinstance(blob.get("session"), dict):
            blob = blob["session"]
        if not isinstance(blob, dict):
            raise ObloCloudError(f"unexpected login response: {type(blob).__name__}")
        self.session_id = blob.get("sessionId") or blob.get("_id")
        self.user_id = blob.get("userId") or blob.get("userID")
        self.username = blob.get("username")
        self.groups = blob.get("groups") or []
        if not self.session_id:
            raise ObloCloudError("login succeeded but no sessionId in response")
        _LOGGER.debug("cloud login ok user_id=%s groups=%s",
                      self.user_id, self.groups)
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
        url = f"{CLOUD_HOST}{path}"
        try:
            async with self._session.request(method, url, headers=headers,
                                             timeout=aiohttp.ClientTimeout(total=30),
                                             **kwargs) as resp:
                try:
                    data = await resp.json(content_type=None)
                except ValueError:
                    data = await resp.text()
                code, message = _first_error(data)
                if resp.status == 429:
                    raise ObloCloudError("rate limited by cloud (429) - wait and retry")
                if code == ERR_USER_NOT_FOUND:
                    raise ObloAuthError("user does not exist")
                if resp.status in (401, 403) or code == ERR_FORCED_LOGOUT:
                    raise ObloAuthError(f"session rejected ({code or resp.status})")
                if resp.status >= 400:
                    raise ObloCloudError(f"HTTP {resp.status}: {message or code}")
                if code not in (None, ERR_OK):
                    raise ObloCloudError(f"cloud error {code}: {message}")
                return data
        except aiohttp.ClientError as err:
            raise ObloCloudError(f"connection to cloud failed: {err}") from err


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
