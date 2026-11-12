"""Config flow for OBLO Living (cloud mode)."""
import logging
from typing import Any, Mapping

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN, CONF_IDENTITY, CONF_SECRET,
    CONF_SESSION_ID, CONF_USER_ID,
)
from .cloud_api import (
    ObloCloudApi, ObloAuthError, ObloCloudError, ObloRateLimitError,
    ObloResponseError, md5_hex,
)

_LOGGER = logging.getLogger(__name__)

_CREDENTIALS = vol.Schema({
    vol.Required(CONF_IDENTITY): str,
    vol.Required(CONF_SECRET): str,
})


class ObloConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._entry = None

    # ---------------------------------------------------------------- helpers
    async def _async_login(self, identity: str, secret: str):
        """Log in with PLAINTEXT credentials; return the authenticated api.

        The cloud wants md5(password) on the wire, and that hash is the only
        thing we persist. A stored hash can therefore never be replayed as a
        credential: every login path takes plaintext from the user.
        """
        session = async_get_clientsession(self.hass)
        api = ObloCloudApi(session, identity, md5_hex(secret), secret_hashed=True)
        await api.async_login()
        return api

    def _login_error_key(self, err: Exception) -> str:
        """Map a cloud exception onto a strings.json error key."""
        if isinstance(err, ObloAuthError):
            # wrong password (406/00012), unknown user (409/11020),
            # forced logout (401/000622), session rejected (403/00003)
            _LOGGER.info("login rejected: %s", err)
            return "invalid_auth"
        if isinstance(err, ObloRateLimitError):
            # the ACCOUNT is temporarily blocked: even a correct password fails
            # until the block lifts. Must be checked before ObloCloudError.
            _LOGGER.warning("cloud account temporarily blocked: %s", err)
            return "rate_limited"
        if isinstance(err, ObloResponseError):
            # reached the cloud, but 5xx / unparseable answer
            _LOGGER.warning("cloud answered unexpectedly: %s", err)
            return "cloud_unavailable" if "HTTP 5" in str(err) else "unexpected_response"
        if isinstance(err, ObloCloudError):
            # DNS/TLS/refused/timeout - a real network-level failure.
            # (429/000016 never reaches here: ObloRateLimitError is caught above.)
            _LOGGER.warning("cloud unreachable: %s", err)
            return "cannot_connect"
        _LOGGER.exception("unexpected config flow error: %s", err)
        return "unknown"

    # ------------------------------------------------------------------- user
    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors = {}
        if user_input is not None:
            # Identity: strip autofill whitespace and normalise case. The cloud
            # treats " me@x.com" as a different (non-existent) account, which
            # came back as 11020 "user does not exist" - a false negative.
            identity = str(user_input[CONF_IDENTITY]).strip()
            # Password is NEVER stripped: leading/trailing spaces can be real.
            secret = str(user_input[CONF_SECRET])
            if not identity or not secret:
                errors["base"] = "missing_credentials"
            else:
                try:
                    api = await self._async_login(identity, secret)
                    # success -> create entry with identity + md5(password)
                    # session_id/user_id cached for fast startup
                    return self.async_create_entry(
                        title=f"OBLO Cloud ({identity})",
                        data={
                            CONF_IDENTITY: identity,
                            CONF_SECRET: md5_hex(secret),
                            CONF_SESSION_ID: api.session_id,
                            CONF_USER_ID: api.user_id,
                        },
                    )
                except Exception as err:  # noqa: BLE001 - mapped below
                    errors["base"] = self._login_error_key(err)

        return self.async_show_form(
            step_id="user", data_schema=_CREDENTIALS, errors=errors)

    # ------------------------------------------------------------------ reauth
    async def async_step_reauth(self, entry_data: Mapping[str, Any]):
        """Entry points here when HA decides the credentials need renewing.

        Only the md5 of the old password is stored, so nothing here may reuse
        it -- the user is prompted for the plaintext password again.
        """
        self._entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"])
        assert self._entry is not None, "reauth started for an unknown entry"
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
            self, user_input: dict[str, Any] | None = None):
        errors = {}
        assert self._entry is not None
        identity = str(self._entry.data[CONF_IDENTITY]).strip()

        if user_input is not None:
            secret = str(user_input[CONF_SECRET])
            if not secret:
                errors["base"] = "missing_credentials"
            else:
                try:
                    api = await self._async_login(identity, secret)
                except Exception as err:  # noqa: BLE001 - mapped below
                    errors["base"] = self._login_error_key(err)
                else:
                    new_data = dict(self._entry.data)
                    new_data.update({
                        CONF_SECRET: md5_hex(secret),
                        CONF_SESSION_ID: api.session_id,
                        CONF_USER_ID: api.user_id,
                    })
                    self.hass.config_entries.async_update_entry(
                        self._entry, data=new_data)
                    return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_SECRET): str}),
            errors=errors,
            description_placeholders={"identity": identity},
        )
