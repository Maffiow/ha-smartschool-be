"""Data update coordinators for the Smartschool BE integration."""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any, TypeVar

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
    PLANNER_DAYS_AHEAD,
)
from .smartschool_api import (
    SmartschoolAPI,
    SmartschoolAuthError,
    SmartschoolError,
    SmartschoolModuleUnavailable,
)

_LOGGER = logging.getLogger(__name__)

_MAX_SEEN_IDS = 500
_T = TypeVar("_T")


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
        self.unavailable_modules: set[str] = set()

    def _optional(self, module: str, func: Callable[..., _T], *args: Any, default: _T) -> _T:
        """Call func; a module the school switched off just yields `default`."""
        try:
            result = func(*args)
        except SmartschoolModuleUnavailable as err:
            if module not in self.unavailable_modules:
                _LOGGER.info("Smartschool module '%s' is not available for this account: %s", module, err)
            self.unavailable_modules.add(module)
            return default
        self.unavailable_modules.discard(module)
        return result

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
    """Fetch evaluations and/or students from Smartschool."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: SmartschoolAPI,
        *,
        results: bool = True,
        students: bool = True,
    ) -> None:
        super().__init__(hass, entry, api, "results", DEFAULT_SCAN_INTERVAL_MINUTES)
        self.include_results = results
        self.include_students = students

    def _fetch(self) -> dict[str, Any]:
        evaluations = (
            self._optional("results", self.api.get_all_evaluations, default=None)
            if self.include_results
            else None
        )
        students = (
            self._optional("students", self.api.get_students, default=[])
            if self.include_students
            else []
        )
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
        inbox = self.api.get_messages("inbox")
        # The message list holds one page only; the counters cover the whole inbox.
        counters = self._optional("message_counters", self.api.get_message_counters, default=None)
        return {"inbox": inbox, "counters": counters}

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
    """Fetch the classic agenda (tasks, lessons) and/or the planner."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: SmartschoolAPI,
        *,
        agenda: bool = True,
        planner: bool = True,
    ) -> None:
        super().__init__(hass, entry, api, "agenda", AGENDA_SCAN_INTERVAL_MINUTES)
        self.include_agenda = agenda
        self.include_planner = planner
        self._planner_id: str | None = None

    def planner_id(self) -> str:
        """Planner id of the student (looked up once, runs in the executor)."""
        if self._planner_id is None:
            self._planner_id = self.api.get_student_planner_id()
        return self._planner_id

    def _planner(self, start, end) -> list[dict]:
        """Planner items from the start of `start` to the end of `end` (dates)."""
        start_dt = dt_util.start_of_local_day(start)
        end_dt = dt_util.start_of_local_day(end + timedelta(days=1))
        return self._optional(
            "planner",
            lambda: self.api.get_planned_elements(self.planner_id(), start_dt, end_dt),
            default=[],
        )

    def fetch_range(self, start, end) -> dict[str, Any]:
        """Lessons and planner items for a date range (runs in the executor)."""
        with self.api.lock:
            return {
                "lessons": self._optional("agenda/lessons", self.api.get_lessons, start, end, default=[])
                if self.include_agenda
                else [],
                "planner": self._planner(start, end) if self.include_planner else [],
            }

    def _fetch(self) -> dict[str, Any]:
        today = dt_util.now().date()
        data: dict[str, Any] = {"tasks": [], "hours": {}, "lessons": [], "planner": []}

        if self.include_agenda:
            data["tasks"] = self._optional("agenda/tasks", self.api.get_future_tasks, default=[])
            data["hours"] = self._optional("agenda/hours", self.api.get_hours, default={})
            data["lessons"] = self._optional(
                "agenda/lessons",
                self.api.get_lessons,
                today,
                today + timedelta(days=AGENDA_DAYS_AHEAD),
                default=[],
            )
        if self.include_planner:
            data["planner"] = self._planner(today, today + timedelta(days=PLANNER_DAYS_AHEAD))
        return data
