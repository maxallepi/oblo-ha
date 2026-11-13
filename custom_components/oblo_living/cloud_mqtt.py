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


class ObloCommandError(ObloCloudMqttError):
    """The gateway answered, but with a non-zero code.

    Deliberately NOT an ObloAuthError. The coordinator treats ObloAuthError as
    "session expired" -> re-login -> and if the re-login is also rejected it
    raises ConfigEntryAuthFailed, which makes Home Assistant show the reauth
    form. A device-side rejection ("property not writable", bad value type,
    unknown service) is not an auth failure, and on the write path that would
    mean toggling one unsupported switch asks the user for their password.
    Keep command failures in their own class so they degrade to a warning.
    """

    def __init__(self, message: str, code: Any = None):
        super().__init__(message)
        self.code = code


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
        self._executor: Optional[ThreadPoolExecutor] = None
        self._ensure_executor()

        self._client: Optional[Any] = None
        self._connected = asyncio.Event()
        self._conn_error: Optional[Exception] = None

        # pending futures keyed by UID
        self._futures: Dict[int, asyncio.Future] = {}
        self._uid_counter = 0
        self._uid_lock = threading.Lock()

        # event callback
        self._evt_cb: Optional[Callable[[Dict], None]] = None

        # SUBACK correlation: mid -> topic. on_subscribe hands us a mid and
        # the granted codes but never the topic, so without this map the log
        # says "granted (1,)" without saying what was granted -- and a SUBACK
        # of 0x80 is the one line that tells a user their silence is a refusal.
        self._sub_mid_topic: Dict[int, str] = {}
        self._sub_granted: Dict[str, Any] = {}

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

    def _ensure_executor(self) -> ThreadPoolExecutor:
        """Return a live single-thread executor, rebuilding it if it was shut down.

        async_close() shuts the pool down, and async_connect()/async_close() are
        called repeatedly over a long-lived config entry. Without this, the first
        close made every later run_in_executor raise
        "RuntimeError: cannot schedule new futures after shutdown", so the
        integration could never reconnect -- it needed an HA restart.
        """
        ex = self._executor
        if ex is None or getattr(ex, "_shutdown", False):
            ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="oblo_mqtt")
            self._executor = ex
        return ex

    async def async_connect(self) -> None:
        """Open WSS, MQTT CONNECT, subscribe response topic."""
        # Only a *connected* client may short-circuit. A client object left
        # behind by a failed attempt (paho Client is constructed before
        # connect() is called) used to satisfy `self._client is not None` and
        # make every later attempt a silent no-op: the integration would then
        # publish into a dead client and time out forever until HA restarted.
        if self._client is not None:
            if self._connected.is_set() and self._conn_error is None:
                return
            await self.async_close()
            self._connected.clear()
            self._conn_error = None

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

        await self._loop.run_in_executor(self._ensure_executor(), _connect_blocking)

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

    async def _async_command(
        self,
        name: str,
        params: Dict[str, Any],
        timeout: Optional[float] = None,
        msg_type: str = "command",
        describe: str = "",
    ) -> Dict[str, Any]:
        """Publish one OHM command and await its reply.

        Transport only: builds the 5-field SPA envelope (name, params, type,
        uid, sender), publishes to the gateway's ohm/req topic with QoS 0 and
        awaits the matching `uid` on the response topic. Returns the reply's
        `params` dict.

        Extracted from async_object_list so the write path rides the exact
        envelope Andrew's working read path already proves. `sender` is
        mandatory -- without it the gateway has no return address and the
        reply is dropped silently, with no NACK and no error.
        """
        if not self._connected.is_set():
            await self.async_connect()

        timeout = REQUEST_TIMEOUT if timeout is None else timeout
        label = describe or name
        last_err: Optional[Exception] = None

        for attempt in range(1, MAX_RETRIES + 1):
            uid = self._next_uid()
            payload = {
                "name": name,
                "params": params,
                "type": msg_type,
                "uid": uid,
                "sender": self._sender(),
            }
            req_topic = self._req_topic()

            _LOGGER.debug(
                "OHM req (%s) attempt %d/%d uid=%d type=%s topic=%s",
                label, attempt, MAX_RETRIES, uid, msg_type, req_topic,
            )

            fut = self._loop.create_future()
            self._futures[uid] = fut

            client = self._client
            if client is None:
                raise ObloAuthError("MQTT client not connected")
            try:
                await self._loop.run_in_executor(
                    self._ensure_executor(),
                    lambda: client.publish(req_topic, json.dumps(payload), qos=0),
                )
                return await asyncio.wait_for(fut, timeout=timeout)
            except asyncio.TimeoutError:
                last_err = ObloTimeoutError(
                    f"{label} uid={uid} timed out after {timeout}s")
                _LOGGER.warning("%s attempt %d/%d timed out (uid=%d)",
                                label, attempt, MAX_RETRIES, uid)
            except (ObloAuthError, ObloCommandError):
                # Not transient. An auth failure will not fix itself by
                # republishing, and a non-zero reply code means the gateway
                # ANSWERED and refused -- retrying a refused write both wastes
                # 30s and risks actuating twice. Surface it on the first answer.
                raise
            except Exception as e:
                last_err = e
                _LOGGER.warning("%s attempt %d/%d failed: %s",
                                label, attempt, MAX_RETRIES, e)
            finally:
                self._futures.pop(uid, None)

            if attempt < MAX_RETRIES:
                await asyncio.sleep(1.0)

        raise last_err or ObloTimeoutError(f"{label} failed after retries")

    def _req_topic(self) -> str:
        """Nothing here may be assumed: the envelope shape was proved against
        the SPA, not by guessing topic parts."""
        return f"oblo/{self._user_id}/gtw/{self._gateway_serial}/ohm/req"

    async def async_object_list(self) -> List[Dict[str, Any]]:
        """Publish get_object_list_by_type_detailed, return object_list."""
        params = await self._async_command(
            "get_object_list_by_type_detailed",
            {"type": "device"},
            describe="OHM object_list",
        )
        return params.get("object_list") or params.get("objectList") or []

    async def async_get_property(
        self,
        service_id: Any,
        service_name: str,
        property_name: str,
        service_type: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Read one property. The only honest write-confirmation primitive.

        PUBACK proves nothing on this broker (unconditional ACKs, silent ACL
        filtering), so a set is confirmed by reading the value back, never by
        the absence of an error.
        """
        params: Dict[str, Any] = {
            "id": service_id,
            "service_name": service_name,
            "property_name": property_name,
        }
        if service_type:
            params["service_type"] = service_type
        reply = await self._async_command(
            "get_service_property_value", params,
            timeout=timeout, describe="OHM get_property",
        )
        for key in ("property_value", "propertyValue", "value"):
            if key in reply:
                return reply[key]
        return reply

    async def async_set_property(
        self,
        service_id: Any,
        service_name: str,
        property_name: str,
        value: Any,
        service_type: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Write one property. Returns the reply params, not a verdict.

        A zero code means the command was accepted, NOT that the device moved.
        Callers must confirm with async_get_property() or an evt state change
        before reporting success to a user -- a silently no-op switch is worse
        than no switch.
        """
        params: Dict[str, Any] = {
            "id": service_id,
            "service_name": service_name,
            "property_name": property_name,
            "property_value": value,
        }
        if service_type:
            params["service_type"] = service_type
        return await self._async_command(
            "set_service_property_value", params,
            timeout=timeout, describe="OHM set_property",
        )

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
            # _subscribe records the mid so the SUBACK line names this topic.
            return self._subscribe(c, evt_topic, 1)

        result = await self._loop.run_in_executor(self._ensure_executor(), _sub)
        # The SUBACK is the line that makes silence interpretable: a clean
        # grant means "subscribed and quiet = healthy steady state", 0x80
        # means we were never allowed to hear the stream.
        mid = result[1] if isinstance(result, (tuple, list)) and len(result) == 2 else None
        _LOGGER.info("EVT subscribe topic=%s mid=%s (SUBACK follows)", evt_topic, mid)

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

        await self._loop.run_in_executor(self._ensure_executor(), _close_blocking)
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None
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
                self._subscribe(client, rsp_topic, 1)
            # also non-admin / sharing path (userId)
            if self._user_id != self._session_id:
                for suffix in ("rsp", "req"):
                    rsp_alt = f"oblo/{self._user_id}/cli/{self._user_id}/{suffix}"
                    self._subscribe(client, rsp_alt, 1)
            self._conn_error = None
        else:
            # CONNACK 5 (Not authorized) means the broker rejected the identity
            # itself, not the payload `sender` — with a shared literal password
            # the username IS the credential, so log its shape, not its value.
            self._conn_error = ObloAuthError(
                f"MQTT CONNACK {rc} for username {self._mask(self._username)} "
                f"(serial {self._gateway_serial})")
        self._connected.set()

    def _subscribe(self, client: Any, topic: str, qos: int = 1) -> Any:
        """Subscribe and remember which mid belongs to which topic.

        paho's on_subscribe never gives the topic back, so without this the
        SUBACK log can only say "something was granted". Silence + a clean
        grant is a healthy steady state; silence + 0x80 is a refused
        subscription. Those two must not look the same in the log.
        """
        try:
            result = client.subscribe(topic, qos)
        except Exception as e:  # pragma: no cover - never block connect on this
            _LOGGER.warning("SUB failed topic=%s err=%s", topic, e)
            return None
        mid = None
        if isinstance(result, (tuple, list)) and len(result) == 2:
            mid = result[1]
        if mid is not None:
            self._sub_mid_topic[mid] = topic
        _LOGGER.info("SUB %s qos=%s mid=%s", topic, qos, mid)
        return result

    def _on_subscribe(self, client, userdata, mid, granted_qos, *extra):
        """Log the granted codes against the topic they belong to."""
        topic = self._sub_mid_topic.pop(mid, None) or f"<mid {mid}>"
        try:
            codes = tuple(int(c) for c in (granted_qos or ()))
        except Exception:  # pragma: no cover
            codes = granted_qos
        self._sub_granted[topic] = codes
        refused = [c for c in (codes if isinstance(codes, tuple) else ()) if c == 128]
        if refused:
            # 0x80 = the broker refused this subscription. This is the line
            # that converts "quiet" into "we are not allowed to hear it".
            _LOGGER.warning("SUBACK REFUSED topic=%s granted=%s", topic, codes)
        else:
            _LOGGER.info("SUBACK topic=%s granted=%s", topic, codes)

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
                        # Hand back the whole params dict; the caller knows which
                        # key it asked for. Keeps _async_command generic.
                        self._loop.call_soon_threadsafe(fut.set_result, params)
                    else:
                        # Non-zero reply code = the gateway answered and said no.
                        # That is a command failure, not an auth failure -- see
                        # ObloCommandError. Mapping it to ObloAuthError made the
                        # coordinator re-login (and, on repeat, ask the user for
                        # a password) because a device refused a value.
                        msg_text = payload.get("message") or f"OHM error code {code}"
                        self._loop.call_soon_threadsafe(
                            fut.set_exception, ObloCommandError(msg_text, code)
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