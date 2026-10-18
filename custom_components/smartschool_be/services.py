"""Actions (services) for the Smartschool BE integration."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_FLAG,
    ATTR_MESSAGE,
    ATTR_MESSAGE_ID,
    ATTR_RECIPIENT,
    ATTR_REPLY_ALL,
    ATTR_SUBJECT,
    DOMAIN,
)
from .smartschool_api import MESSAGE_FLAGS, SmartschoolError

if TYPE_CHECKING:
    from . import SmartschoolData

SERVICE_GET_MESSAGE = "get_message"
SERVICE_REPLY_MESSAGE = "reply_message"
SERVICE_SEND_MESSAGE = "send_message"
SERVICE_MARK_UNREAD = "mark_message_unread"
SERVICE_SET_FLAG = "set_message_flag"
SERVICE_ARCHIVE = "archive_message"
SERVICE_TRASH = "trash_message"

_ENTRY = {vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string}
_MESSAGE = {**_ENTRY, vol.Required(ATTR_MESSAGE_ID): vol.Coerce(int)}

SCHEMA_MESSAGE = vol.Schema(_MESSAGE)
SCHEMA_REPLY = vol.Schema(
    {
        **_MESSAGE,
        vol.Required(ATTR_MESSAGE): cv.string,
        vol.Optional(ATTR_REPLY_ALL, default=False): cv.boolean,
        vol.Optional(ATTR_SUBJECT): cv.string,
    }
)
SCHEMA_SEND = vol.Schema(
    {
        **_ENTRY,
        vol.Required(ATTR_RECIPIENT): cv.string,
        vol.Required(ATTR_SUBJECT): cv.string,
        vol.Required(ATTR_MESSAGE): cv.string,
    }
)
SCHEMA_FLAG = vol.Schema({**_MESSAGE, vol.Required(ATTR_FLAG): vol.In(list(MESSAGE_FLAGS))})


def _get_data(hass: HomeAssistant, call: ServiceCall) -> SmartschoolData:
    """Find the account the call is for (optional when only one is set up)."""
    entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
    entries = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED and (entry_id is None or e.entry_id == entry_id)
    ]
    if not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_not_found"
        )
    if len(entries) > 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="multiple_entries"
        )
    return entries[0].runtime_data


async def _run(hass: HomeAssistant, func: Callable[..., Any], *args: Any) -> Any:
    try:
        return await hass.async_add_executor_job(func, *args)
    except SmartschoolError as err:
        raise HomeAssistantError(str(err)) from err


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Smartschool actions."""

    async def get_message(call: ServiceCall) -> ServiceResponse:
        data = _get_data(hass, call)
        message = await _run(hass, data.api.get_message, call.data[ATTR_MESSAGE_ID])
        # Opening a message marks it read on Smartschool.
        await data.messages.async_request_refresh()
        return message

    async def reply_message(call: ServiceCall) -> None:
        data = _get_data(hass, call)
        await _run(
            hass,
            data.api.reply_message,
            call.data[ATTR_MESSAGE_ID],
            call.data[ATTR_MESSAGE],
            call.data[ATTR_REPLY_ALL],
            call.data.get(ATTR_SUBJECT),
        )
        await data.messages.async_request_refresh()

    async def send_message(call: ServiceCall) -> ServiceResponse:
        data = _get_data(hass, call)
        name = await _run(
            hass,
            data.api.send_message,
            call.data[ATTR_RECIPIENT],
            call.data[ATTR_SUBJECT],
            call.data[ATTR_MESSAGE],
        )
        return {"recipient": name}

    def _simple(method_name: str, *extra_keys: str):
        async def handler(call: ServiceCall) -> None:
            data = _get_data(hass, call)
            method = getattr(data.api, method_name)
            await _run(hass, method, call.data[ATTR_MESSAGE_ID], *(call.data[k] for k in extra_keys))
            await data.messages.async_request_refresh()

        return handler

    hass.services.async_register(
        DOMAIN, SERVICE_GET_MESSAGE, get_message, schema=SCHEMA_MESSAGE,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(DOMAIN, SERVICE_REPLY_MESSAGE, reply_message, schema=SCHEMA_REPLY)
    hass.services.async_register(
        DOMAIN, SERVICE_SEND_MESSAGE, send_message, schema=SCHEMA_SEND,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_MARK_UNREAD, _simple("mark_message_unread"), schema=SCHEMA_MESSAGE
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_FLAG, _simple("set_message_flag", ATTR_FLAG), schema=SCHEMA_FLAG
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ARCHIVE, _simple("archive_message"), schema=SCHEMA_MESSAGE
    )
    hass.services.async_register(
        DOMAIN, SERVICE_TRASH, _simple("trash_message"), schema=SCHEMA_MESSAGE
    )
