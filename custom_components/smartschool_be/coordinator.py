"""Data update coordinator for the Smartschool BE integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL_MINUTES, DOMAIN
from .smartschool_api import SmartschoolAPI, SmartschoolAuthError, SmartschoolError

_LOGGER = logging.getLogger(__name__)


class SmartschoolCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch evaluations and students from Smartschool."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: SmartschoolAPI) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=DEFAULT_SCAN_INTERVAL_MINUTES),
        )
        self.api = api

    def _fetch(self) -> dict[str, Any]:
        """Fetch all data (runs in the executor)."""
        self.api.login()
        evaluations = self.api.get_all_evaluations()
        students = self.api.get_students()
        return {
            "evaluations": evaluations or {},
            "students": students or [],
        }

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.hass.async_add_executor_job(self._fetch)
        except SmartschoolAuthError as err:
            # Triggers the reauth flow in Home Assistant.
            raise ConfigEntryAuthFailed(str(err)) from err
        except SmartschoolError as err:
            raise UpdateFailed(f"Error communicating with Smartschool: {err}") from err
