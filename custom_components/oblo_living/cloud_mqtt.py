"""OBLO cloud MQTT client — OHM over WSS.

Provides device inventory (object_list with service_list) and live events
via the cloud MQTT broker at wss://cloud.oblo.rs:5222.
"""
import asyncio
import json
import logging
import random
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional


# Lazy paho import so the module loads without the dependency installed.
# The requirement is declared in manifest.json; HA installs it before setup.
def _mqtt():
    import paho.mqtt.client as mqtt
    return mqtt


_LOGGER = logging.getLogger(__name__)

WSS_HOST = "cloud.oblo.rs"
WSS_PORT = 5222
WSS_PATH = "/"
MQTT_PASSWORD = "password"  # literal, per SPA
CONNECT_TIMEOUT = 15
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3
KEEPALIVE = 55


# Reuse the REST client's error types so the coordinator's existing handling
# (auth -> reauth, rate-limit -> cached state) applies to MQTT failures too.
from .cloud_api import ObloAuthError, ObloCloudError, ObloRateLimitError  # noqa: E402


class ObloCloudMqttError(ObloCloudError):
    """Base exception for the cloud MQTT client."""


class ObloTimeoutError(ObloCloudMqttError):
    """Request timed out after retries."""


class ObloCloudMqtt:
    """Async wrapper around paho-mqtt for OBLO cloud OHM."""

    def __init__(
        self,
        session_id: str,
        user_id: str,
        username: str,
        gateway_serial: str,
        hass_loop: Optional[asyncio.AbstractEventLoop] = None,
        groups: Optional[List[str]] = None,
    ):
        """
        Args:
            session_id: from /auth/login response header 'sessionid' or retrieveSSOTokens
            user_id: GATEWAY OWNER'S userId (from /udm/device/list -> userId),
                     NOT the logged-in user's ID. The SPA resolves this by picking
                     a gateway and using its userId.
            username: from retrieveSSOTokens -> username (used as cli/<username>)
            gateway_serial: from /udm/device/list -> serialNumber (SN)
            hass_loop: HA event loop for thread-safe callbacks (None = current loop)
            groups: the account's group list from /auth/login. Selects the OHM
                    `sender` field: admin replies on cli/<sessionId>, everyone
                    else on cli/<userId>.
        """
        self._session_id = session_id
        self._user_id = user_id
        self._username = username
        self._gateway_serial = gateway_serial
        self._groups = groups or []
        self._loop = hass_loop or asyncio.get_event_loop()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="oblo_mqtt")

        self._client: Optional[Any] = None
        self._connected = asyncio.Event()
        self._conn_error: Optional[Exception] = None

        # pending futures keyed by UID
        self._futures: Dict[int, asyncio.Future] = {}
        self._uid_counter = 0
        self._uid_lock = threading.Lock()

        # event callback
        self._evt_cb: Optional[Callable[[Dict], None]] = None

    # ------------------------------------------------------------------ #
    # Public async API
    # ------------------------------------------------------------------ #

    @staticmethod
    def _mask(s: str) -> str:
        """Mask a credential-like string for logging."""
        if not s:
            return "<empty>"
        if len(s) <= 8:
            return s[0] + "*" * (len(s) - 1)
        return s[:4] + "..." + s[-4:] + f" (len={len(s)})"

    def _sender(self) -> str:
        """The `sender` routing field the gateway needs to answer us.

        app.js sets this on EVERY OHM publish (two independent code paths,
        both agree):

            admin            -> "cli/" + sessionId
            non-admin w/parents -> "cli/" + <gateway owner userId>
            otherwise        -> "cli/" + <logged-in userId>

        It is not optional metadata: the gateway echoes it back as the reply
        topic (`oblo/<gtwUserId>/<sender>/rsp`). Omit it and the reply is
        unroutable -- the request is accepted and then silently dropped, which
        is indistinguishable from a dead uplink (3 x 15s timeouts, no error).
        """
        if "admin" in self._groups:
            return f"cli/{self._session_id}"
        # Non-admin: the gateway owner's id is what the SPA uses when the
        # account has parentsIds, which is the shared-access case.
        return f"cli/{self._user_id}"

    async def async_connect(self) -> None:
        """Open WSS, MQTT CONNECT, subscribe response topic."""
        if self._client is not None:
            return

        def _connect_blocking():
            # clientId shape copied from the SPA: "w-" + sessionId + "-" + 1..1000
            # (app.js: Math.floor(1e3*Math.random())+1). Some brokers ACL on the
            # clientId prefix, so match it exactly rather than inventing a format.
            cid = "w-" + self._session_id + "-" + str(random.randint(1, 1000))
            mqtt = _mqtt()
            # paho 2.x takes a CallbackAPIVersion positionally; 1.x does not.
            # manifest pins >=2.0.0, this keeps an unexpected 1.6 working too.
            api_ver = getattr(mqtt, "CallbackAPIVersion", None)
            if api_ver is not None:
                c = mqtt.Client(
                    api_ver.VERSION1,
                    client_id=cid,
                    transport="websockets",
                    protocol=mqtt.MQTTv311,
                )
            else:
                c = mqtt.Client(
                    client_id=cid,
                    transport="websockets",
                    protocol=mqtt.MQTTv311,
                )
            self._client = c
            c.username_pw_set("cli/" + self._username, MQTT_PASSWORD)
            c.ws_set_options(path=WSS_PATH)
            c.tls_set(cert_reqs=ssl.CERT_NONE)
            c.tls_insecure_set(True)

            c.on_connect = self._on_connect
            c.on_message = self._on_message
            c.on_disconnect = self._on_disconnect
            # paho 1.x has no on_subscribe_v2; the 1-arg form works on both.
            try:
                c.on_subscribe = self._on_subscribe
            except Exception:  # pragma: no cover - never block connect on logging
                pass

            try:
                c.connect(WSS_HOST, WSS_PORT, KEEPALIVE)
                c.loop_start()
            except Exception as e:
                self._conn_error = e
                self._connected.set()

        await self._loop.run_in_executor(self._executor, _connect_blocking)

        # wait for CONNACK
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=CONNECT_TIMEOUT)
        except asyncio.TimeoutError:
            await self.async_close()
            raise ObloTimeoutError("MQTT connect timed out")

        if self._conn_error:
            raise self._conn_error
        if not self._connected.is_set():
            await self.async_close()
            raise ObloAuthError("MQTT CONNACK failed (no error captured)")

        _LOGGER.debug("OBLO cloud MQTT connected (session=%s...)", self._session_id[:8])

    async def async_object_list(self) -> List[Dict[str, Any]]:
        """Publish get_object_list_by_type_detailed, return object_list."""
        if not self._connected.is_set():
            await self.async_connect()

        last_err = None
        for attempt in range(1, MAX_RETRIES + 1):
            uid = self._next_uid()
            payload = {
                "name": "get_object_list_by_type_detailed",
                "params": {"type": "device"},
                "type": "command",
                "uid": uid,
                # Mandatory: the gateway uses this to pick the reply topic.
                "sender": self._sender(),
            }
            req_topic = f"oblo/{self._user_id}/gtw/{self._gateway_serial}/ohm/req"

            _LOGGER.debug(
                "OHM req attempt %d/%d uid=%d topic=%s",
                attempt,
                MAX_RETRIES,
                uid,
                req_topic,
            )

            fut = self._loop.create_future()
            self._futures[uid] = fut

            client = self._client
            if client is None:
                raise ObloAuthError("MQTT client not connected")
            try:
                await self._loop.run_in_executor(
                    self._executor,
                    lambda: client.publish(req_topic, json.dumps(payload), qos=0),
                )
                result = await asyncio.wait_for(fut, timeout=REQUEST_TIMEOUT)
                return result
            except asyncio.TimeoutError:
                last_err = ObloTimeoutError(f"OHM request uid={uid} timed out after {REQUEST_TIMEOUT}s")
                _LOGGER.warning("OHM attempt %d/%d timed out (uid=%d)", attempt, MAX_RETRIES, uid)
            except ObloAuthError:
                raise
            except Exception as e:
                last_err = e
                _LOGGER.warning("OHM attempt %d/%d failed: %s", attempt, MAX_RETRIES, e)
            finally:
                self._futures.pop(uid, None)

            if attempt < MAX_RETRIES:
                await asyncio.sleep(1.0)

        raise last_err or ObloTimeoutError("OHM object_list failed after retries")

    async def async_subscribe_events(self, callback: Callable[[Dict], None]) -> None:
        """Subscribe to gateway events for live device property changes."""
        if self._evt_cb is not None:
            return
        self._evt_cb = callback

        if not self._connected.is_set():
            await self.async_connect()

        client = self._client
        if client is None:
            raise ObloAuthError("MQTT client not connected")
        # SPA: oblo/<gtwUserId>/gtw/<SN>/+/evt  (the + is the device/bus id).
        # The earlier "gtw/+/ohm/evt" shape matched nothing the gateway emits.
        evt_topic = f"oblo/{self._user_id}/gtw/{self._gateway_serial}/+/evt"
        def _sub(c=client):
            c.subscribe(evt_topic, 1)
        await self._loop.run_in_executor(self._executor, _sub)
        _LOGGER.info("OHM events subscribed: %s", evt_topic)

    async def async_close(self) -> None:
        """Clean disconnect."""
        def _close_blocking():
            if self._client is not None:
                try:
                    self._client.loop_stop()
                    self._client.disconnect()
                except Exception:
                    pass
                self._client = None

        await self._loop.run_in_executor(self._executor, _close_blocking)
        self._executor.shutdown(wait=False)
        self._connected.clear()
        _LOGGER.debug("OBLO cloud MQTT closed")

    # ------------------------------------------------------------------ #
    # paho callbacks (run in paho thread)
    # ------------------------------------------------------------------ #

    def _on_connect(self, client: Any, userdata, flags, rc, *extra):
        _LOGGER.info("MQTT CONNACK rc=%s", rc)
        client = client or self._client
        if rc == 0 and client is None:
            self._conn_error = ObloAuthError("no MQTT client to subscribe with")
        elif rc == 0:
            # subscribe response topics: SPA subscribes BOTH /req and /rsp because
            # the gateway replies on /req for some message classes (it's a
            # "request" back to the client). Omitting /req silently drops those.
            for suffix in ("rsp", "req"):
                rsp_topic = f"oblo/{self._user_id}/cli/{self._session_id}/{suffix}"
                client.subscribe(rsp_topic, 1)
                _LOGGER.info("SUB %s", rsp_topic)
            # also non-admin / sharing path (userId)
            if self._user_id != self._session_id:
                for suffix in ("rsp", "req"):
                    rsp_alt = f"oblo/{self._user_id}/cli/{self._user_id}/{suffix}"
                    client.subscribe(rsp_alt, 1)
                    _LOGGER.info("SUB %s", rsp_alt)
            self._conn_error = None
        else:
            # CONNACK 5 (Not authorized) means the broker rejected the identity
            # itself, not the payload `sender` — with a shared literal password
            # the username IS the credential, so log its shape, not its value.
            self._conn_error = ObloAuthError(
                f"MQTT CONNACK {rc} for username {self._mask(self._username)} "
                f"(serial {self._gateway_serial})")
        self._connected.set()

    def _on_subscribe(self, client, userdata, mid, granted_qos, *extra):
        _LOGGER.info("SUBACK mid=%s granted_qos=%s", mid, granted_qos)

    # alias for tests that invoke the method directly
    def on_connect(self, client, userdata, flags, rc, *extra):
        self._on_connect(client, userdata, flags, rc, *extra)

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode(errors="replace"))
        except Exception as e:
            _LOGGER.warning("MQTT parse error on %s: %s", topic, e)
            return

        # Response to our OHM request? The SPA subscribes BOTH cli/<id>/req and
        # cli/<id>/rsp and routes purely on uid, so accept either suffix: the
        # gateway answers some message classes on /req (it is a "request" back
        # to the client). Matching only /rsp silently drops those.
        uid = payload.get("uid")
        if uid is not None:
            try:
                uid_key = int(uid)
            except (TypeError, ValueError):
                uid_key = None
            if uid_key is not None and uid_key in self._futures:
                fut = self._futures.pop(uid_key, None)
                if fut and not fut.done():
                    code = payload.get("code", -1)
                    if code == 0 or str(code) == "0":
                        params = payload.get("params") or {}
                        obj_list = params.get("object_list") or params.get("objectList") or []
                        self._loop.call_soon_threadsafe(fut.set_result, obj_list)
                    else:
                        msg_text = payload.get("message") or f"OHM error code {code}"
                        self._loop.call_soon_threadsafe(
                            fut.set_exception, ObloAuthError(msg_text)
                        )
                return

        # Live event
        if "/evt" in topic:
            # gtw_online_status_changed is the unambiguous cloud->gateway
            # contact signal: fired only on online<->offline transitions with
            # {isOnline, serialNumber}. A frozen lastTimeStatusUpdated is NOT
            # proof of a dead tunnel -- it is a *transition* timestamp, not a
            # heartbeat -- so this event is the real liveness evidence.
            # Log it unconditionally, even before a callback is attached.
            if payload.get("name") == "gtw_online_status_changed":
                params = payload.get("params") or {}
                _LOGGER.warning(
                    "ONLINE_EVENT serial=%s isOnline=%s",
                    params.get("serialNumber") or self._gateway_serial,
                    params.get("isOnline"),
                )
            if self._evt_cb:
                self._loop.call_soon_threadsafe(self._evt_cb, payload)

    def _on_disconnect(self, client, userdata, rc, *extra):
        _LOGGER.debug("MQTT disconnected rc=%s", rc)
        # fail any pending futures
        for fut in self._futures.values():
            if not fut.done():
                self._loop.call_soon_threadsafe(
                    fut.set_exception, ObloAuthError("MQTT disconnected")
                )
        self._futures.clear()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _next_uid(self) -> int:
        """Request uid: a random int in 1..1001, exactly like the SPA.

        The SPA derives it as `Math.floor(1000*Math.random())+1`, so the range
        is 1..1001 inclusive, and the router drops any message whose uid is
        falsy -- never return 0 and never return a string. Random rather than
        sequential so a late reply from a timed-out request cannot resolve a
        fresh future on a long-lived connection.
        """
        with self._uid_lock:
            for _ in range(50):
                uid = random.randint(1, 1001)
                if uid not in self._futures:
                    return uid
            # 50 collisions against a 1001-slot space means something is off --
            # fall back to a monotonic value outside the random range so we can
            # never silently reuse an in-flight uid.
            self._uid_counter += 1
            return 1001 + self._uid_counter