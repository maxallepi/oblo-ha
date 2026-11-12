"""OBLO cloud MQTT client — OHM over WSS.

Provides device inventory (object_list with service_list) and live events
via the cloud MQTT broker at wss://cloud.oblo.rs:5222.
"""
import asyncio
import json
import logging
import random
import ssl
import string
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

import paho.mqtt.client as mqtt

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
        """
        self._session_id = session_id
        self._user_id = user_id
        self._username = username
        self._gateway_serial = gateway_serial
        self._loop = hass_loop or asyncio.get_event_loop()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="oblo_mqtt")

        self._client: Optional[mqtt.Client] = None
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

    async def async_connect(self) -> None:
        """Open WSS, MQTT CONNECT, subscribe response topic."""
        if self._client is not None:
            return

        def _connect_blocking():
            cid = "w-" + self._session_id + "-" + "".join(
                random.choices(string.ascii_lowercase, k=6)
            )
            c = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION1,
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
        """Subscribe to ohm/evt for live device property changes."""
        if self._evt_cb is not None:
            return
        self._evt_cb = callback

        if not self._connected.is_set():
            await self.async_connect()

        client = self._client
        if client is None:
            raise ObloAuthError("MQTT client not connected")
        evt_topic = f"oblo/{self._user_id}/gtw/+/ohm/evt"
        def _sub(c=client):
            c.subscribe(evt_topic, 1)
        await self._loop.run_in_executor(self._executor, _sub)
        _LOGGER.debug("Subscribed to live events: %s", evt_topic)

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
        _LOGGER.debug("MQTT CONNACK rc=%s", rc)
        client = client or self._client
        if rc == 0 and client is None:
            self._conn_error = ObloAuthError("no MQTT client to subscribe with")
        elif rc == 0:
            # subscribe response topic (admin path uses sessionId)
            rsp_topic = f"oblo/{self._user_id}/cli/{self._session_id}/rsp"
            client.subscribe(rsp_topic, 1)
            _LOGGER.debug("SUB %s", rsp_topic)
            # also non-admin / sharing path (userId)
            if self._user_id != self._session_id:
                rsp_alt = f"oblo/{self._user_id}/cli/{self._user_id}/rsp"
                client.subscribe(rsp_alt, 1)
                _LOGGER.debug("SUB %s", rsp_alt)
            self._conn_error = None
        else:
            self._conn_error = ObloAuthError(f"MQTT CONNACK {rc}")
        self._connected.set()

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

        # Response to our OHM request? Topic contains cli/<sessionId>/rsp or cli/<userId>/rsp
        if topic.endswith("/rsp"):
            uid = payload.get("uid")
            if isinstance(uid, int):
                fut = self._futures.pop(uid, None)
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
        # Live event
        elif self._evt_cb and "/evt" in topic:
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
        with self._uid_lock:
            self._uid_counter += 1
            return self._uid_counter