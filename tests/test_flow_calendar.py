"""Config flow error paths and calendar conversion edge cases."""
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util

from custom_components.smartschool_be.calendar import (
    lesson_to_event,
    planner_to_event,
    task_to_event,
)
from custom_components.smartschool_be.const import CONF_BASE_URL, CONF_BIRTHDAY, DOMAIN
from custom_components.smartschool_be.smartschool_api import (
    SmartschoolBirthdayError,
    SmartschoolError,
    SmartschoolTwoFactorFailed,
)

API = "custom_components.smartschool_be.smartschool_api.SmartschoolAPI"

DATA = {
    CONF_BASE_URL: "https://school.smartschool.be",
    CONF_USERNAME: "jan.peeters",
    CONF_PASSWORD: "secret",
    CONF_BIRTHDAY: "2010-03-15",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


async def _submit(hass: HomeAssistant, user_input: dict, login_effect=None):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(f"{API}.login", side_effect=login_effect), patch(f"{API}.close"):
        return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


@pytest.mark.parametrize(
    ("user_input", "login_effect", "errors"),
    [
        ({**DATA, CONF_BASE_URL: "school.smartschool.be"}, None, {"base": "invalid_base_url"}),
        ({**DATA, CONF_USERNAME: "   "}, None, {"base": "invalid_auth"}),
        ({**DATA, CONF_BIRTHDAY: "15/03/2010"}, None, {"birthday": "invalid_birthday"}),
        ({**DATA, "totp_secret": "ABCD"}, None, {"totp_secret": "invalid_totp_secret"}),
        (DATA, SmartschoolBirthdayError("x"), {"birthday": "invalid_birthday"}),
        ({**DATA, "totp_code": "123456"}, SmartschoolTwoFactorFailed("x"), {"totp_code": "totp_failed"}),
        ({**DATA, "totp_secret": "GEZDGNBVGY3TQOJQ"}, SmartschoolTwoFactorFailed("x"), {"totp_secret": "totp_failed"}),
        (DATA, RuntimeError("boom"), {"base": "cannot_connect"}),
    ],
)
async def test_user_flow_errors(hass: HomeAssistant, user_input, login_effect, errors) -> None:
    result = await _submit(hass, user_input, login_effect)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors


async def test_user_flow_rejects_duplicate(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, data=DATA, unique_id=f"{DATA[CONF_BASE_URL]}|{DATA[CONF_USERNAME]}").add_to_hass(hass)
    result = await _submit(hass, DATA)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# --- calendar conversions -------------------------------------------------------

HOURS = {"1": {"start": "08:25", "end": "09:15"}, "bad": {"start": "x", "end": "y"},
         "backwards": {"start": "10:00", "end": "09:00"}}


def test_lesson_to_event_edge_cases():
    good = {"date": "2026-11-02", "hour_id": "1", "course": "FR4", "subject": "Les",
            "teacher": "Ghysen", "note": "Boek mee", "classroom": "E.3", "moment_id": "7"}
    event = lesson_to_event(good, HOURS)
    assert event.summary == "FR4" and event.location == "E.3"
    assert "Leerkracht: Ghysen" in event.description and "Boek mee" in event.description
    assert lesson_to_event({**good, "date": "geen datum"}, HOURS) is None
    assert lesson_to_event({**good, "hour_id": "unknown"}, HOURS) is None
    assert lesson_to_event({**good, "hour_id": "bad"}, HOURS) is None
    assert lesson_to_event({**good, "hour_id": "backwards"}, HOURS) is None


def test_task_to_event_edge_cases():
    event = task_to_event({"date": "2026-11-03", "type": "Toets", "course": "Wiskunde", "description": "H3"})
    assert event.summary == "Toets: Wiskunde"
    assert event.start == date(2026, 11, 3) and event.end == date(2026, 11, 4)
    assert task_to_event({"date": "", "type": "Toets"}) is None


def test_planner_to_event_edge_cases():
    start = dt_util.now().replace(hour=10, minute=0, second=0, microsecond=0)
    timed = planner_to_event({"id": "a", "kind": "lesson", "course": "Frans", "name": "Les 4",
                              "start": start.isoformat(), "end": (start + timedelta(minutes=50)).isoformat()})
    assert timed.summary == "Frans" and timed.description == "Les 4"
    assert timed.end - timed.start == timedelta(minutes=50)

    whole_day = planner_to_event({"id": "b", "kind": "task", "type": "Taak", "name": "Lezen",
                                  "start": start.isoformat(), "end": start.isoformat(), "whole_day": True})
    assert whole_day.summary == "Taak: Lezen" and whole_day.start == start.date()

    assert planner_to_event({"id": "c", "start": "geen tijd"}) is None


async def test_calendar_falls_back_to_cached_data(hass: HomeAssistant) -> None:
    """When Smartschool does not answer, the calendar shows what it already had."""
    today = dt_util.now().date().isoformat()
    lessons = [{"moment_id": "1", "date": today, "hour_id": "1", "course": "FR4",
                "subject": None, "classroom": None, "teacher": None, "note": None}]
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="x",
                            options={"enable_planner": False, "enable_results": False, "enable_students": False})
    entry.add_to_hass(hass)
    with patch(f"{API}.login"), patch(f"{API}.get_messages", return_value=[]), patch(
        f"{API}.get_message_counters", return_value={"unread": 0, "total": 0}
    ), patch(f"{API}.get_future_tasks", return_value=[]), patch(
        f"{API}.get_hours", return_value={"1": {"start": "08:25", "end": "09:15"}}
    ), patch(f"{API}.get_lessons", side_effect=[lessons, SmartschoolError("down")]):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        start = dt_util.start_of_local_day()
        result = await hass.services.async_call(
            "calendar", "get_events",
            {"entity_id": "calendar.smartschool_jan_peeters_agenda",
             "start_date_time": start, "end_date_time": start + timedelta(days=1)},
            blocking=True, return_response=True,
        )
    events = result["calendar.smartschool_jan_peeters_agenda"]["events"]
    assert [e["summary"] for e in events] == ["FR4"]
