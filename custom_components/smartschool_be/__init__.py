"""The Smartschool BE integration."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import CONF_BASE_URL, CONF_BIRTHDAY, DOMAIN
from .coordinator import (
    SmartschoolAgendaCoordinator,
    SmartschoolCoordinator,
    SmartschoolMessagesCoordinator,
)
from .services import async_setup_services
from .smartschool_api import SmartschoolAPI

PLATFORMS = [Platform.CALENDAR, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class SmartschoolData:
    """Runtime data of one Smartschool account."""

    api: SmartschoolAPI
    results: SmartschoolCoordinator
    messages: SmartschoolMessagesCoordinator
    agenda: SmartschoolAgendaCoordinator


type SmartschoolConfigEntry = ConfigEntry[SmartschoolData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration-wide actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SmartschoolConfigEntry) -> bool:
    """Set up Smartschool BE from a config entry."""
    api = SmartschoolAPI(
        entry.data[CONF_BASE_URL],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_BIRTHDAY, ""),
    )
    results = SmartschoolCoordinator(hass, entry, api)

    try:
        # Raises ConfigEntryNotReady / ConfigEntryAuthFailed so HA retries or asks to reauth.
        await results.async_config_entry_first_refresh()
    except Exception:
        await hass.async_add_executor_job(api.close)
        raise

    # Messages and agenda are optional modules (a school can disable them):
    # a failure there only makes those entities unavailable.
    messages = SmartschoolMessagesCoordinator(hass, entry, api)
    agenda = SmartschoolAgendaCoordinator(hass, entry, api)
    await messages.async_refresh()
    await agenda.async_refresh()

    entry.runtime_data = SmartschoolData(api=api, results=results, messages=messages, agenda=agenda)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SmartschoolConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await hass.async_add_executor_job(entry.runtime_data.api.close)

    return unload_ok
