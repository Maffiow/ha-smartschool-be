"""Smoke tests for the Smartschool BE integration."""
from datetime import timedelta
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.smartschool_be.const import (
    CONF_BASE_URL,
    CONF_BIRTHDAY,
    DOMAIN,
    EVENT_NEW_MESSAGE,
)
from custom_components.smartschool_be.smartschool_api import (
    SmartschoolAuthError,
    SmartschoolConnectionError,
    SmartschoolError,
    SmartschoolModuleUnavailable,
    SmartschoolTwoFactorRequired,
)

API = "custom_components.smartschool_be.smartschool_api.SmartschoolAPI"

DATA = {
    CONF_BASE_URL: "https://school.smartschool.be",
    CONF_USERNAME: "jan.peeters",
    CONF_PASSWORD: "secret",
    CONF_BIRTHDAY: "2010-03-15",
}


def _ev(name, course, value, desc, date):
    return {
        "name": name,
        "course": course,
        "score_value": value,
        "score_description": desc,
        "date": date,
        "availability_date": date,
        "feedback": None,
    }


WISK_1 = _ev("H1", "Wiskunde", 80.0, "8/10", "2026-10-01")
WISK_2 = _ev("H2", "Wiskunde", None, "goed", "2026-10-08")
FRANS = _ev("Voc", "Frans", 50.0, "5/10", "2026-10-05")

EVALUATIONS = {
    "by_course": {
        "Wiskunde": {"latest": WISK_2, "history": [WISK_1, WISK_2]},
        "Frans": {"latest": FRANS, "history": [FRANS]},
    },
    "latest": WISK_2,
    "recent": [WISK_2, FRANS, WISK_1],
}

STUDENTS = [
    {
        "userID": 42,
        "fullName": "Jan Peeters",
        "class": "3A ",
        "photoUrl": "https://userpicture.example/jan",
        "titu": [{"fullName": "Juf An"}],
    }
]
COUNTERS = {"unread": 278, "total": 480}


def _planner(*_args):
    day = dt_util.now().date().isoformat()
    return [
        {"id": "p1", "kind": "task", "type": "Toets", "name": "Breuken", "course": "Wiskunde",
         "location": None, "start": f"{day}T08:25:00+02:00", "end": f"{day}T09:15:00+02:00",
         "whole_day": False, "deadline": True, "status": None},
    ]


def _msg(msg_id, unread=True, subject="Uitstap"):
    return {
        "id": msg_id,
        "from": "Lotte Peeters",
        "subject": subject,
        "date": "2026-10-18 08:24",
        "unread": unread,
        "attachments": 0,
        "flag": "none",
        "can_reply": True,
    }


INBOX = [_msg(2), _msg(1, unread=False)]
HOURS = {"318": {"start": "08:25", "end": "09:15", "title": "1"}}


def _today() -> str:
    return dt_util.now().date().isoformat()


def _tasks():
    return [
        {"date": _today(), "type": "Toets", "description": "H3 p42", "course": "AAR1",
         "course_title": "AAR1", "hour_id": "318", "warning": False},
        {"date": "2000-01-01", "type": "Taak", "description": "oud", "course": "FR",
         "course_title": "FR", "hour_id": "318", "warning": False},
    ]


def _lessons(*_args):
    return [
        {"moment_id": "1", "date": _today(), "hour_id": "318", "course": "FR4",
         "subject": "Prépositions", "classroom": "E.3.12", "teacher": "Ghysen H.", "note": None}
    ]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def mock_api():
    """Patch every network-facing API method with happy-path data."""
    with patch(f"{API}.login"), patch(
        f"{API}.get_all_evaluations", return_value=EVALUATIONS
    ), patch(f"{API}.get_students", return_value=STUDENTS), patch(
        f"{API}.get_messages", return_value=list(INBOX)
    ) as get_messages, patch(f"{API}.get_future_tasks", side_effect=_tasks), patch(
        f"{API}.get_hours", return_value=HOURS
    ), patch(f"{API}.get_lessons", side_effect=_lessons), patch(
        f"{API}.get_message_counters", return_value=COUNTERS
    ), patch(f"{API}.get_student_planner_id", return_value="1_42_0"), patch(
        f"{API}.get_planned_elements", side_effect=_planner
    ):
        yield get_messages


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="x", title="Smartschool (jan.peeters)")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_and_sensors(hass: HomeAssistant, mock_api) -> None:
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED

    # Latest numeric-less score must give unknown, not a text state.
    wisk = hass.states.get("sensor.smartschool_wiskunde_jan_peeters")
    assert wisk.state == "unknown"
    assert wisk.attributes["score"] == "goed"
    assert wisk.attributes["unit_of_measurement"] == "%"
    assert wisk.attributes["gemiddelde_score"] == 80.0

    frans = hass.states.get("sensor.smartschool_frans_jan_peeters")
    assert float(frans.state) == 50.0

    latest = hass.states.get("sensor.smartschool_laatste_resultaat_jan_peeters")
    assert latest.state == "H2"

    unread = hass.states.get("sensor.smartschool_ongelezen_berichten_jan_peeters")
    assert unread.state == "278"  # counters cover the whole inbox, not one page
    assert unread.attributes["totaal_in_inbox"] == 480
    assert unread.attributes["berichten"][0]["id"] == 2

    tasks = hass.states.get("sensor.smartschool_komende_taken_jan_peeters")
    # agenda task + planner test; the task in the past is not counted
    assert tasks.state == "2"
    assert tasks.attributes["aantal_toetsen"] == 2

    assert hass.states.get("sensor.jan_peeters_klas").state == "3A"
    assert hass.states.get("sensor.jan_peeters_titularis").state == "Juf An"
    info = hass.states.get("sensor.jan_peeters_info")
    assert info.attributes["entity_picture"] == "https://userpicture.example/jan"

    with patch(f"{API}.close") as close:
        assert await hass.config_entries.async_unload(entry.entry_id)
    close.assert_called_once()


async def test_new_message_event(hass: HomeAssistant, mock_api) -> None:
    events = []
    hass.bus.async_listen(EVENT_NEW_MESSAGE, events.append)
    entry = await _setup(hass)
    # Messages already in the inbox at the first poll are not "new".
    assert events == []

    mock_api.return_value = [_msg(3, subject="Oudercontact"), *INBOX]
    await entry.runtime_data.messages.async_refresh()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["message_id"] == 3
    assert events[0].data["subject"] == "Oudercontact"


async def test_calendar(hass: HomeAssistant, mock_api) -> None:
    await _setup(hass)
    state = hass.states.get("calendar.smartschool_agenda_jan_peeters")
    assert state is not None

    start = dt_util.start_of_local_day()
    result = await hass.services.async_call(
        "calendar",
        "get_events",
        {"entity_id": state.entity_id, "start_date_time": start, "end_date_time": start + timedelta(days=1)},
        blocking=True,
        return_response=True,
    )
    events = result[state.entity_id]["events"]
    summaries = {e["summary"] for e in events}
    assert "FR4" in summaries
    assert "Toets: AAR1" in summaries
    assert "Toets: Wiskunde" in summaries  # from the planner
    lesson = next(e for e in events if e["summary"] == "FR4")
    assert lesson["location"] == "E.3.12"
    assert "08:25" in lesson["start"]


async def test_message_actions(hass: HomeAssistant, mock_api) -> None:
    await _setup(hass)

    full = {"id": 2, "subject": "Uitstap", "text": "Beste ouders", "attachments": []}
    with patch(f"{API}.get_message", return_value=full):
        response = await hass.services.async_call(
            DOMAIN, "get_message", {"message_id": 2}, blocking=True, return_response=True
        )
    assert response["text"] == "Beste ouders"

    with patch(f"{API}.reply_message") as reply:
        await hass.services.async_call(
            DOMAIN, "reply_message", {"message_id": 2, "message": "Bedankt!"}, blocking=True
        )
    reply.assert_called_once_with(2, "Bedankt!", False, None)

    with patch(f"{API}.set_message_flag") as flag:
        await hass.services.async_call(
            DOMAIN, "set_message_flag", {"message_id": 2, "flag": "red"}, blocking=True
        )
    flag.assert_called_once_with(2, "red")

    with patch(f"{API}.send_message", side_effect=SmartschoolError("Meerdere ontvangers")):
        with pytest.raises(HomeAssistantError, match="Meerdere ontvangers"):
            await hass.services.async_call(
                DOMAIN, "send_message",
                {"recipient": "Peeters", "subject": "Vraag", "message": "Hallo"},
                blocking=True,
            )


async def test_messages_failure_does_not_block_setup(hass: HomeAssistant, mock_api) -> None:
    mock_api.side_effect = SmartschoolError("module uitgeschakeld")
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    state = hass.states.get("sensor.smartschool_ongelezen_berichten_jan_peeters")
    assert state.state == "unavailable"


async def test_setup_retry_on_connection_error(hass: HomeAssistant) -> None:
    with patch(f"{API}.login", side_effect=SmartschoolConnectionError("down")):
        entry = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_auth_error_starts_reauth(hass: HomeAssistant) -> None:
    with patch(f"{API}.login", side_effect=SmartschoolAuthError("bad")):
        entry = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_ERROR

    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == config_entries.SOURCE_REAUTH

    with patch(f"{API}.login"), patch(f"{API}.close"), patch(
        "custom_components.smartschool_be.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            flows[0]["flow_id"], {CONF_PASSWORD: "new", CONF_BIRTHDAY: "2010-03-15"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new"


async def test_user_flow(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    with patch(f"{API}.login", side_effect=SmartschoolAuthError("bad")):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["errors"] == {"base": "invalid_auth"}

    with patch(f"{API}.login"), patch(
        "custom_components.smartschool_be.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {**DATA, "totp_secret": ""}


async def test_user_flow_asks_for_2fa_key(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(f"{API}.login", side_effect=SmartschoolTwoFactorRequired("2fa")):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["errors"] == {"totp_secret": "totp_required"}

    # The 6-digit code instead of the key is refused before any login.
    with patch(f"{API}.login") as login:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**DATA, "totp_secret": "123456"}
        )
    assert result["errors"] == {"totp_secret": "invalid_totp_secret"}
    login.assert_not_called()

    with patch(f"{API}.login"), patch(
        "custom_components.smartschool_be.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**DATA, "totp_secret": "gezd gnbv gy3t qojq"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["totp_secret"] == "GEZDGNBVGY3TQOJQ"


async def test_disabled_module_does_not_trigger_reauth(hass: HomeAssistant, mock_api) -> None:
    """A module the school switched off (redirect to the homepage) is just empty."""
    with patch(
        f"{API}.get_future_tasks", side_effect=SmartschoolModuleUnavailable("uit")
    ), patch(f"{API}.get_all_evaluations", side_effect=SmartschoolModuleUnavailable("403")):
        entry = await _setup(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert hass.config_entries.flow.async_progress() == []
    assert entry.runtime_data.agenda.unavailable_modules == {"agenda/tasks"}
    tasks = hass.states.get("sensor.smartschool_komende_taken_jan_peeters")
    assert tasks.state == "1"  # the planner still works


async def test_options_switch_modules_off(hass: HomeAssistant, mock_api) -> None:
    entry = await _setup(hass)
    assert hass.states.get("sensor.smartschool_ongelezen_berichten_jan_peeters") is not None

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "enable_results": True,
            "enable_messages": False,
            "enable_agenda": False,
            "enable_planner": False,
            "enable_students": True,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.messages is None
    assert entry.runtime_data.agenda is None
    assert hass.states.get("sensor.smartschool_ongelezen_berichten_jan_peeters") is None
    assert hass.states.get("calendar.smartschool_agenda_jan_peeters") is None
    assert hass.states.get("sensor.smartschool_frans_jan_peeters") is not None
    assert hass.states.get("sensor.jan_peeters_klas") is not None

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "get_message", {"message_id": 2}, blocking=True, return_response=True
        )
