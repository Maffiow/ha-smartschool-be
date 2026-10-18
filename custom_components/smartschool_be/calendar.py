"""Calendar with Smartschool lessons and upcoming tasks/tests."""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.const import CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import SmartschoolConfigEntry
from .const import DOMAIN
from .coordinator import SmartschoolAgendaCoordinator
from .entity import account_device_info
from .smartschool_api import SmartschoolError

_LOGGER = logging.getLogger(__name__)

# Never fetch more than this many days of lessons for one calendar view.
_MAX_FETCH_DAYS = 62


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SmartschoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Smartschool calendar."""
    if config_entry.runtime_data.agenda is None:
        return
    entity = SmartschoolCalendar(
        config_entry.runtime_data.agenda,
        config_entry.data[CONF_USERNAME],
        config_entry.entry_id,
    )
    entity._attr_device_info = account_device_info(config_entry)
    async_add_entities([entity])


def _parse_time(day: date, value: str | None) -> datetime | None:
    try:
        hour, minute = (int(x) for x in (value or "").split(":")[:2])
    except ValueError:
        return None
    return datetime.combine(day, time(hour, minute), tzinfo=dt_util.get_default_time_zone())


def lesson_to_event(lesson: dict, hours: dict) -> CalendarEvent | None:
    try:
        day = date.fromisoformat(lesson.get("date") or "")
    except ValueError:
        return None
    hour = hours.get(lesson.get("hour_id")) or {}
    start = _parse_time(day, hour.get("start"))
    end = _parse_time(day, hour.get("end"))
    if start is None or end is None or end <= start:
        return None

    description = "\n".join(
        part
        for part in (
            lesson.get("subject"),
            f"Leerkracht: {lesson['teacher']}" if lesson.get("teacher") else None,
            lesson.get("note"),
        )
        if part
    )
    return CalendarEvent(
        start=start,
        end=end,
        summary=lesson.get("course") or "Les",
        description=description or None,
        location=lesson.get("classroom"),
        uid=f"lesson-{lesson.get('moment_id')}",
    )


def task_to_event(task: dict) -> CalendarEvent | None:
    try:
        day = date.fromisoformat(task.get("date") or "")
    except ValueError:
        return None
    label = task.get("type") or "Taak"
    course = task.get("course") or task.get("course_title") or ""
    return CalendarEvent(
        start=day,
        end=day + timedelta(days=1),
        summary=f"{label}: {course}".strip(": "),
        description=task.get("description"),
    )


def planner_to_event(item: dict) -> CalendarEvent | None:
    start = dt_util.parse_datetime(item.get("start") or "")
    end = dt_util.parse_datetime(item.get("end") or "") or start
    if start is None:
        return None
    summary = item.get("course") or item.get("name") or "Planner"
    if item.get("kind") == "task":
        summary = f"{item.get('type') or 'Taak'}: {item.get('course') or item.get('name') or ''}".strip(": ")
    description = item.get("name") if item.get("name") and item.get("name") != summary else None
    common = {
        "summary": summary,
        "description": description,
        "location": item.get("location"),
        "uid": f"planner-{item.get('id')}",
    }
    if item.get("whole_day") or item.get("deadline") or end <= start:
        day = dt_util.as_local(start).date()
        last = max(dt_util.as_local(end).date(), day)
        return CalendarEvent(start=day, end=last + timedelta(days=1), **common)
    return CalendarEvent(start=dt_util.as_local(start), end=dt_util.as_local(end), **common)


class SmartschoolCalendar(CoordinatorEntity[SmartschoolAgendaCoordinator], CalendarEntity):
    """Lessons (timed) and tasks/tests (all-day) of the account."""

    _attr_icon = "mdi:calendar-school"

    def __init__(
        self, coordinator: SmartschoolAgendaCoordinator, username: str, entry_id: str
    ) -> None:
        super().__init__(coordinator)
        self._attr_name = f"Smartschool Agenda ({username})"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_agenda"

    def _cached_events(self) -> list[CalendarEvent]:
        data = self.coordinator.data or {}
        hours = data.get("hours") or {}
        events = [lesson_to_event(lesson, hours) for lesson in data.get("lessons") or []]
        events += [task_to_event(task) for task in data.get("tasks") or []]
        events += [planner_to_event(item) for item in data.get("planner") or []]
        return [e for e in events if e is not None]

    @property
    def event(self) -> CalendarEvent | None:
        """The current or next upcoming event."""
        now = dt_util.now()
        upcoming = [e for e in self._cached_events() if e.end_datetime_local > now]
        return min(upcoming, key=lambda e: e.start_datetime_local, default=None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        data = self.coordinator.data or {}
        hours = data.get("hours") or {}
        start_day = dt_util.as_local(start_date).date()
        end_day = min(
            dt_util.as_local(end_date).date(), start_day + timedelta(days=_MAX_FETCH_DAYS)
        )

        try:
            fetched = await hass.async_add_executor_job(
                self.coordinator.fetch_range, start_day, end_day
            )
        except SmartschoolError as err:
            _LOGGER.debug("Could not fetch agenda for %s - %s: %s", start_day, end_day, err)
            fetched = {"lessons": data.get("lessons") or [], "planner": data.get("planner") or []}

        events = [lesson_to_event(lesson, hours) for lesson in fetched["lessons"]]
        events += [task_to_event(task) for task in data.get("tasks") or []]
        events += [planner_to_event(item) for item in fetched["planner"]]
        return [
            e
            for e in events
            if e is not None
            and e.end_datetime_local > start_date
            and e.start_datetime_local < end_date
        ]
