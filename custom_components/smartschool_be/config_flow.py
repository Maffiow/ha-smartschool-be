"""Config flow for Smartschool BE integration."""
from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .const import CONF_BASE_URL, CONF_BIRTHDAY, DOMAIN
from .smartschool_api import SmartschoolAPI, SmartschoolAuthError, SmartschoolBirthdayError

_LOGGER = logging.getLogger(__name__)

_BIRTHDAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SmartschoolResultsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smartschool BE."""

    VERSION = 1

    async def _async_validate(
        self, base_url: str, username: str, password: str, birthday: str
    ) -> dict[str, str]:
        """Try to log in and return a dict of form errors (empty when OK)."""
        if not _BIRTHDAY_RE.match(birthday):
            return {CONF_BIRTHDAY: "invalid_birthday"}

        api = SmartschoolAPI(base_url, username, password, birthday)
        try:
            await self.hass.async_add_executor_job(api.login)
        except SmartschoolBirthdayError as err:
            _LOGGER.warning("Smartschool birthday verification failed: %s", err)
            return {CONF_BIRTHDAY: "invalid_birthday"}
        except SmartschoolAuthError as err:
            _LOGGER.warning("Smartschool login failed: %s", err)
            return {"base": "invalid_auth"}
        except Exception:
            _LOGGER.exception("Unexpected error during Smartschool login")
            return {"base": "cannot_connect"}
        finally:
            await self.hass.async_add_executor_job(api.close)

        return {}

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        schema = vol.Schema(
            {
                vol.Required(CONF_BASE_URL, default=(user_input or {}).get(CONF_BASE_URL, "")): str,
                vol.Required(CONF_USERNAME, default=(user_input or {}).get(CONF_USERNAME, "")): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(
                    CONF_BIRTHDAY,
                    description={"suggested_value": (user_input or {}).get(CONF_BIRTHDAY, "")},
                ): str,
            }
        )

        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].strip().rstrip("/")
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]
            birthday = user_input[CONF_BIRTHDAY].strip()

            if not base_url.startswith(("http://", "https://")):
                errors["base"] = "invalid_base_url"
            elif not username:
                errors["base"] = "invalid_auth"
            else:
                await self.async_set_unique_id(f"{base_url}|{username}")
                self._abort_if_unique_id_configured()

                errors = await self._async_validate(base_url, username, password, birthday)
                if not errors:
                    return self.async_create_entry(
                        title=f"Smartschool ({username})",
                        data={
                            CONF_BASE_URL: base_url,
                            CONF_USERNAME: username,
                            CONF_PASSWORD: password,
                            CONF_BIRTHDAY: birthday,
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]):
        """Start reauth when Smartschool rejects the stored credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Ask for a new password / birthday and validate them."""
        errors = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            birthday = user_input[CONF_BIRTHDAY].strip()

            errors = await self._async_validate(
                entry.data[CONF_BASE_URL], entry.data[CONF_USERNAME], password, birthday
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_PASSWORD: password, CONF_BIRTHDAY: birthday},
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_PASSWORD): str,
                vol.Required(
                    CONF_BIRTHDAY,
                    description={"suggested_value": entry.data.get(CONF_BIRTHDAY, "")},
                ): str,
            }
        )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={"username": entry.data[CONF_USERNAME]},
        )
