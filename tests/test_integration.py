"""Smoke tests for the Smartschool BE integration."""
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.smartschool_be.const import CONF_BASE_URL, CONF_BIRTHDAY, DOMAIN
from custom_components.smartschool_be.smartschool_api import (
    SmartschoolAuthError,
    SmartschoolConnectionError,
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

STUDENTS = [{"userID": 42, "fullName": "Jan Peeters", "class": "3A ", "titu": []}]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="x")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_and_sensors(hass: HomeAssistant) -> None:
    with patch(f"{API}.login"), patch(
        f"{API}.get_all_evaluations", return_value=EVALUATIONS
    ), patch(f"{API}.get_students", return_value=STUDENTS):
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

    with patch(f"{API}.close") as close:
        assert await hass.config_entries.async_unload(entry.entry_id)
    close.assert_called_once()


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

    with patch(f"{API}.login"), patch(
        f"{API}.get_all_evaluations", return_value=EVALUATIONS
    ), patch(f"{API}.get_students", return_value=STUDENTS):
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
    assert result["data"] == DATA
