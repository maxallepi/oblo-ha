"""Thin Paho MQTT client for the OBLO Living gateway.

Two connection modes:

local  — topics are FIXED literals from mosquitto.db (ground truth):
  gtw/<MAC>/ohm/evt, gtw/<MAC>/ohm/azc/+/evt, gtw/<MAC>/osm/evt,
  svc/udm/gtw/<MAC>/evt

cloud  — topics are DATA-DRIVEN: subscribe exactly the literals in
  topic_event_map.json produced by the capture runner. No hardcoded cloud
  patterns: the dump defines them, wrong guesses cost nothing.

Auth: if the password is an SSO token with expiry, the client tracks it and
on CONNACK rc=5 (not authorized) calls the refresh callback once, then
reconnects with the new token before giving up.
"""
import json
import logging
import os
import re
import ssl
import time

import paho.mqtt.client as mqtt

_LOGGER = logging.getLogger(__name__)


class ObloMqttClient:
    """Connects to the OBLO broker (TLS). mode: 'local' or 'cloud'."""

    def __init__(self, host, port, username, password, gateway_mac,
                 ca_path=None, mode="local", topic_map_path=None,
                 client_id="ha-oblo", on_message=None, insecure=False,
                 sso_expires_at=None, on_token_expired=None):
        self._host, self._port = host, port
        self._mac = gateway_mac            # e.g. "C83DD4300D5B"
        self._mode = mode
        self._topic_map_path = topic_map_path
        self._ca_path = ca_path
        self._cli = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)
        self.set_credentials(username, password)
        self._sso_expires_at = sso_expires_at
        self._on_token_expired = on_token_expired
        self._token_refreshed = False      # one refresh attempt per session
        self._configure_tls(insecure)
        if on_message:
            self._cli.on_message = on_message
        # CONNACK 5 -> try one token refresh then reconnect
        self._cli.on_connect_fail = self._on_connect_fail
        self._cli.on_disconnect = self._on_disconnect

    # -- credentials / SSO ------------------------------------------------
    def set_credentials(self, username, password):
        self._username, self._password = username, password
        self._cli.username_pw_set(username, password)

    def token_expired(self):
        return (self._sso_expires_at is not None
                and time.time() >= self._sso_expires_at)

    def _on_connect_fail(self, *args):
        _LOGGER.warning("OBLO MQTT connect failed (likely CONNACK 5 / bad token)")

    def _on_disconnect(self, client, userdata, rc):
        if rc == 5 and self._on_token_expired and not self._token_refreshed:
            self._token_refreshed = True
            new = self._on_token_expired()   # callback returns (user, pass, exp)
            if new:
                self.set_credentials(*new[:2])
                if len(new) > 2:
                    self._sso_expires_at = new[2]
                try:
                    self.connect()
                except Exception:  # noqa: BLE001 - reconnect is best-effort
                    _LOGGER.exception("reconnect after token refresh failed")

    def _configure_tls(self, insecure):
        if self._ca_path:
            self._cli.tls_set(ca_certs=self._ca_path,
                              cert_reqs=ssl.CERT_REQUIRED,
                              tls_version=ssl.PROTOCOL_TLS_CLIENT)
        else:
            self._cli.tls_set(tls_version=ssl.PROTOCOL_TLS_CLIENT)
        self._cli.tls_insecure_set(insecure)

    # -- topics -----------------------------------------------------------
    def topics(self):
        """Subscribe set. Local = fixed literals; cloud = from the dump map."""
        if self._mode == "cloud":
            return self._topics_from_map()
        p = f"gtw/{self._mac}"
        return [f"{p}/ohm/evt",
                f"{p}/ohm/azc/+/evt",
                f"{p}/osm/evt",
                f"svc/udm/gtw/{self._mac}/evt"]

    def _topics_from_map(self):
        """Read topic_event_map.json: {"topics": {"<topic>": {...}}} or list."""
        if not self._topic_map_path or not os.path.exists(self._topic_map_path):
            _LOGGER.warning("cloud mode: topic map %s missing, subscribing nothing",
                            self._topic_map_path)
            return []
        with open(self._topic_map_path) as f:
            data = json.load(f)
        topics = data.get("topics") if isinstance(data, dict) else data
        if isinstance(topics, dict):
            return list(topics.keys())
        return list(topics or [])

    # -- lifecycle --------------------------------------------------------
    def connect(self):
        self._cli.connect(self._host, self._port, keepalive=30)

    def subscribe_all(self, qos=1):
        for t in self.topics():
            self._cli.subscribe(t, qos)

    def subscribe(self, topic, qos=1):
        self._cli.subscribe(topic, qos)

    def publish(self, topic, payload, qos=1):
        self._cli.publish(topic, payload, qos)

    def loop_start(self):
        self._cli.loop_start()

    def disconnect(self):
        self._cli.disconnect()


# --- identity format (from gateway backup oblomb.db UserDetails) -----------
# cli/<24-hex accountId>-<64-hex session suffix>. The suffix rotates on every
# app registration; the 24-hex prefix is the stable account id. Old 2019-era
# rows use cli/<15-digit IMEI>. Credentials table only holds user/user and
# client/tmpclient, so cli/... passwords are validated externally (SSO token).
CLI_USER_RE = re.compile(r"^cli/[0-9a-f]{24}-[0-9a-f]{64}$")


def validate_identity(username):
    """True if username matches the live cli/ identity format."""
    return bool(CLI_USER_RE.match(username or ""))
