"""The Smartschool BE integration."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_AGENDA,
    CONF_BASE_URL,
    CONF_BIRTHDAY,
    CONF_MESSAGES,
    CONF_PLANNER,
    CONF_RESULTS,
    CONF_STUDENTS,
    CONF_TOTP_SECRET,
    DOMAIN,
)
from .coordinator import (
    SmartschoolAgendaCoordinator,
    SmartschoolCoordinator,
    SmartschoolMessagesCoordinator,
)
from .services import async_setup_services
from .smartschool_api import SmartschoolAPI, SmartschoolAuthError, SmartschoolError

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CALENDAR, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class SmartschoolData:
    """Runtime data of one Smartschool account; a disabled module is None."""

    api: SmartschoolAPI
    results: SmartschoolCoordinator | None
    messages: SmartschoolMessagesCoordinator | None
    agenda: SmartschoolAgendaCoordinator | None


type SmartschoolConfigEntry = ConfigEntry[SmartschoolData]


def module_enabled(entry: ConfigEntry, option: str) -> bool:
    """Whether a module is switched on in the integration options (default: on)."""
    return bool(entry.options.get(option, True))


# unique_id fragments of the entities that belong to each module
_MODULE_UNIQUE_IDS = {
    CONF_RESULTS: ("_latest_result", "_course_"),
    CONF_MESSAGES: ("_unread_messages",),
    CONF_STUDENTS: ("_student_",),
}


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
        entry.data.get(CONF_TOTP_SECRET, ""),
    )

    def _login() -> None:
        with api.lock:
            api.login()

    try:
        await hass.async_add_executor_job(_login)
    except SmartschoolAuthError as err:
        await hass.async_add_executor_job(api.close)
        raise ConfigEntryAuthFailed(str(err)) from err
    except SmartschoolError as err:
        await hass.async_add_executor_job(api.close)
        raise ConfigEntryNotReady(str(err)) from err

    results_on = module_enabled(entry, CONF_RESULTS)
    students_on = module_enabled(entry, CONF_STUDENTS)
    agenda_on = module_enabled(entry, CONF_AGENDA)
    planner_on = module_enabled(entry, CONF_PLANNER)

    results = (
        SmartschoolCoordinator(hass, entry, api, results=results_on, students=students_on)
        if results_on or students_on
        else None
    )
    messages = (
        SmartschoolMessagesCoordinator(hass, entry, api)
        if module_enabled(entry, CONF_MESSAGES)
        else None
    )
    agenda = (
        SmartschoolAgendaCoordinator(hass, entry, api, agenda=agenda_on, planner=planner_on)
        if agenda_on or planner_on
        else None
    )

    # A module that fails (or that the school switched off) only makes its own
    # entities unavailable; it never blocks the rest of the integration.
    for coordinator in (results, messages, agenda):
        if coordinator is not None:
            await coordinator.async_refresh()

    _remove_disabled_entities(hass, entry, agenda_on or planner_on)

    entry.runtime_data = SmartschoolData(api=api, results=results, messages=messages, agenda=agenda)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _remove_disabled_entities(hass: HomeAssistant, entry: ConfigEntry, agenda_on: bool) -> None:
    """Drop entities of modules that were switched off in the options."""
    fragments: list[str] = []
    for option, ids in _MODULE_UNIQUE_IDS.items():
        if not module_enabled(entry, option):
            fragments.extend(ids)
    if not agenda_on:
        fragments.extend(("_agenda", "_upcoming_tasks"))
    if not fragments:
        return

    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if any(fragment in reg_entry.unique_id for fragment in fragments):
            registry.async_remove(reg_entry.entity_id)


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload so the module choice takes effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SmartschoolConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await hass.async_add_executor_job(entry.runtime_data.api.close)

    return unload_ok
