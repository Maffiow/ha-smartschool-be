"""Diagnostics support for Smartschool BE."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import SmartschoolConfigEntry
from .const import CONF_BIRTHDAY

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME, CONF_BIRTHDAY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SmartschoolConfigEntry
) -> dict[str, Any]:
    """Return counts and status only: no grades, names or message contents."""
    data = entry.runtime_data
    results = data.results.data or {}
    evaluations = results.get("evaluations") or {}
    messages = data.messages.data or {}
    agenda = data.agenda.data or {}

    def _status(coordinator) -> dict[str, Any]:
        return {
            "last_update_success": coordinator.last_update_success,
            "last_exception": repr(coordinator.last_exception) if coordinator.last_exception else None,
        }

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "results": {
            **_status(data.results),
            "courses": len(evaluations.get("by_course") or {}),
            "evaluations": sum(
                len(c.get("history") or []) for c in (evaluations.get("by_course") or {}).values()
            ),
            "students": len(results.get("students") or []),
        },
        "messages": {
            **_status(data.messages),
            "inbox": len(messages.get("inbox") or []),
            "unread": sum(1 for m in messages.get("inbox") or [] if m.get("unread")),
        },
        "agenda": {
            **_status(data.agenda),
            "tasks": len(agenda.get("tasks") or []),
            "lessons": len(agenda.get("lessons") or []),
            "hours": len(agenda.get("hours") or {}),
        },
    }
