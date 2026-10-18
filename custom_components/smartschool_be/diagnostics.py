"""Diagnostics support for Smartschool BE."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import SmartschoolConfigEntry
from .const import CONF_BIRTHDAY

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME, CONF_BIRTHDAY}


def _status(coordinator) -> dict[str, Any] | None:
    if coordinator is None:
        return None
    return {
        "last_update_success": coordinator.last_update_success,
        "last_exception": repr(coordinator.last_exception) if coordinator.last_exception else None,
        "unavailable_modules": sorted(coordinator.unavailable_modules),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SmartschoolConfigEntry
) -> dict[str, Any]:
    """Return counts and status only: no grades, names or message contents."""
    data = entry.runtime_data
    out: dict[str, Any] = {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
    }

    if data.results is not None:
        results = data.results.data or {}
        by_course = (results.get("evaluations") or {}).get("by_course") or {}
        out["results"] = {
            **_status(data.results),
            "courses": len(by_course),
            "evaluations": sum(len(c.get("history") or []) for c in by_course.values()),
            "students": len(results.get("students") or []),
        }
    if data.messages is not None:
        messages = data.messages.data or {}
        out["messages"] = {
            **_status(data.messages),
            "inbox_page": len(messages.get("inbox") or []),
            "counters": messages.get("counters"),
        }
    if data.agenda is not None:
        agenda = data.agenda.data or {}
        out["agenda"] = {
            **_status(data.agenda),
            "tasks": len(agenda.get("tasks") or []),
            "lessons": len(agenda.get("lessons") or []),
            "hours": len(agenda.get("hours") or {}),
            "planner_items": len(agenda.get("planner") or []),
        }
    return out
