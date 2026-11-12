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
from .cloud_api import ObloCloudApi, ObloAuthError, ObloCloudError, md5_hex

_LOGGER = logging.getLogger(__name__)

_CREDENTIALS = vol.Schema({
    vol.Required(CONF_IDENTITY): str,
    vol.Required(CONF_SECRET): str,
})


class ObloConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._entry = None

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

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors = {}
        if user_input is not None:
            identity = user_input[CONF_IDENTITY].strip()
            try:
                api = await self._async_login(identity, user_input[CONF_SECRET])
                # success -> create entry with identity + md5(password)
                # session_id/user_id cached for fast startup
                return self.async_create_entry(
                    title=f"OBLO Cloud ({identity})",
                    data={
                        CONF_IDENTITY: identity,
                        CONF_SECRET: md5_hex(user_input[CONF_SECRET]),
                        CONF_SESSION_ID: api.session_id,
                        CONF_USER_ID: api.user_id,
                    },
                )
            except ObloAuthError:
                errors["base"] = "invalid_auth"
            except ObloCloudError as err:
                _LOGGER.warning("cloud validation failed: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("unexpected config flow error")
                errors["base"] = "unknown"

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
        identity = self._entry.data[CONF_IDENTITY]

        if user_input is not None:
            try:
                api = await self._async_login(identity, user_input[CONF_SECRET])
            except ObloAuthError:
                errors["base"] = "invalid_auth"
            except ObloCloudError as err:
                _LOGGER.warning("reauth: cloud unreachable: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("reauth: unexpected error")
                errors["base"] = "unknown"
            else:
                new_data = dict(self._entry.data)
                new_data.update({
                    CONF_SECRET: md5_hex(user_input[CONF_SECRET]),
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
