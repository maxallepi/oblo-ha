"""Constants for the OBLO Living integration."""

DOMAIN = "oblo_living"

# -- cloud mode (primary, per user decision) --------------------------------
CLOUD_HOST = "https://cloud.oblo.rs:443"
CLOUD_MQTT_WS = "wss://cloud.oblo.rs:5222"
CONF_IDENTITY = "identity"      # cloud email / username
CONF_SECRET = "secret"          # cloud password
CONF_SESSION_ID = "session_id"  # cached, refreshed at setup
CONF_USER_ID = "user_id"
SCAN_INTERVAL_S = 60

# -- local mode (paused; kept for fallback) ---------------------------------
CONF_GATEWAY_SN = "gateway_sn"
CONF_CLIENT_TAG = "client_tag"
CONF_ACCOUNT_ID = "account_id"
CONF_PROFILE_ID = "profile_id"
DEFAULT_PORT = 8883
TOPIC_PREFIX = "oblo/gateway"

# Device service types from APK: com.rtrk.smarthome... / Oblo*Service
DEVICE_SERVICES = [
    "dim", "rgbw", "roller_shutter", "smart_outlet",
    "weather", "led_indication", "decoration_led",
]
