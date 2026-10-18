"""Data update coordinators for the Smartschool BE integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    AGENDA_DAYS_AHEAD,
    AGENDA_SCAN_INTERVAL_MINUTES,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    EVENT_NEW_MESSAGE,
    MESSAGES_SCAN_INTERVAL_MINUTES,
)
from .smartschool_api import SmartschoolAPI, SmartschoolAuthError, SmartschoolError

_LOGGER = logging.getLogger(__name__)

_MAX_SEEN_IDS = 500


class _SmartschoolBaseCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Shared error handling: auth errors trigger reauth, the rest UpdateFailed."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: SmartschoolAPI,
        name: str,
        interval_minutes: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{name}",
            update_interval=timedelta(minutes=interval_minutes),
        )
        self.api = api

    def _fetch(self) -> dict[str, Any]:
        raise NotImplementedError

    def _locked_fetch(self) -> dict[str, Any]:
        with self.api.lock:
            self.api.login()
            return self._fetch()

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.hass.async_add_executor_job(self._locked_fetch)
        except SmartschoolAuthError as err:
            # Triggers the reauth flow in Home Assistant.
            raise ConfigEntryAuthFailed(str(err)) from err
        except SmartschoolError as err:
            raise UpdateFailed(f"Error communicating with Smartschool: {err}") from err


class SmartschoolCoordinator(_SmartschoolBaseCoordinator):
    """Fetch evaluations and students from Smartschool."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: SmartschoolAPI) -> None:
        super().__init__(hass, entry, api, "results", DEFAULT_SCAN_INTERVAL_MINUTES)

    def _fetch(self) -> dict[str, Any]:
        evaluations = self.api.get_all_evaluations()
        students = self.api.get_students()
        return {
            "evaluations": evaluations or {},
            "students": students or [],
        }


class SmartschoolMessagesCoordinator(_SmartschoolBaseCoordinator):
    """Poll the inbox and fire an event for every new message."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: SmartschoolAPI) -> None:
        super().__init__(hass, entry, api, "messages", MESSAGES_SCAN_INTERVAL_MINUTES)
        self._store: Store[dict[str, list[int]]] = Store(
            hass, 1, f"{DOMAIN}.{entry.entry_id}.messages"
        )
        self._seen_ids: set[int] | None = None

    def _fetch(self) -> dict[str, Any]:
        return {"inbox": self.api.get_messages("inbox")}

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()

        if self._seen_ids is None:
            stored = await self._store.async_load()
            self._seen_ids = set(stored["seen_ids"]) if stored else None

        inbox = data["inbox"]
        current_ids = {m["id"] for m in inbox}

        if self._seen_ids is None:
            # Very first run: everything already in the inbox counts as seen.
            self._seen_ids = current_ids
        else:
            entry = self.config_entry
            for message in reversed(inbox):  # oldest first
                if message["id"] in self._seen_ids:
                    continue
                self._seen_ids.add(message["id"])
                self.hass.bus.async_fire(
                    EVENT_NEW_MESSAGE,
                    {
                        "config_entry_id": entry.entry_id,
                        "account": entry.title,
                        "message_id": message["id"],
                        "from": message["from"],
                        "subject": message["subject"],
                        "date": message["date"],
                        "attachments": message["attachments"],
                    },
                )

        # Keep the stored set bounded; ids still in the inbox always stay.
        if len(self._seen_ids) > _MAX_SEEN_IDS:
            keep = max(_MAX_SEEN_IDS - len(current_ids), 0)
            extra = sorted(self._seen_ids - current_ids)[-keep:] if keep else []
            self._seen_ids = current_ids | set(extra)
        self._store.async_delay_save(
            lambda: {"seen_ids": sorted(self._seen_ids or ())}, 10
        )
        return data


class SmartschoolAgendaCoordinator(_SmartschoolBaseCoordinator):
    """Fetch upcoming tasks/tests and the lessons of the coming days."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: SmartschoolAPI) -> None:
        super().__init__(hass, entry, api, "agenda", AGENDA_SCAN_INTERVAL_MINUTES)

    def _fetch(self) -> dict[str, Any]:
        today = dt_util.now().date()
        return {
            "tasks": self.api.get_future_tasks(),
            "hours": self.api.get_hours(),
            "lessons": self.api.get_lessons(today, today + timedelta(days=AGENDA_DAYS_AHEAD)),
        }
