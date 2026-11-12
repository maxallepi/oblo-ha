"""Config flow for OBLO Living (cloud mode)."""
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN, CONF_IDENTITY, CONF_SECRET,
    CONF_SESSION_ID, CONF_USER_ID,
)
from .cloud_api import ObloCloudApi, ObloAuthError, ObloCloudError

_LOGGER = logging.getLogger(__name__)


class ObloConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors = {}
        if user_input is not None:
            identity = user_input[CONF_IDENTITY].strip()
            secret = user_input[CONF_SECRET]

            session = async_get_clientsession(self.hass)
            api = ObloCloudApi(session, identity, secret)

            try:
                await api.async_login()
                # success -> create entry with identity+secret (cloud credentials)
                # session_id/user_id cached for fast startup
                return self.async_create_entry(
                    title=f"OBLO Cloud ({identity})",
                    data={
                        CONF_IDENTITY: identity,
                        CONF_SECRET: secret,
                        CONF_SESSION_ID: api.session_id,
                        CONF_USER_ID: api.user_id,
                    },
                )
            except ObloAuthError:
                errors["base"] = "invalid_auth"
            except ObloCloudError as err:
                _LOGGER.warning("cloud validation failed: %s", err)
                errors["base"] = "cannot_connect"
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.exception("unexpected config flow error")
                errors["base"] = "unknown"

        schema = vol.Schema({
            vol.Required(CONF_IDENTITY): str,
            vol.Required(CONF_SECRET): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
