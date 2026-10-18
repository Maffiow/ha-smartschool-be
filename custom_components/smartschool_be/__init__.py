"""The Smartschool BE integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant

from .const import CONF_BASE_URL, CONF_BIRTHDAY
from .coordinator import SmartschoolCoordinator
from .smartschool_api import SmartschoolAPI

PLATFORMS = [Platform.SENSOR]

type SmartschoolConfigEntry = ConfigEntry[SmartschoolCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SmartschoolConfigEntry) -> bool:
    """Set up Smartschool BE from a config entry."""
    api = SmartschoolAPI(
        entry.data[CONF_BASE_URL],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_BIRTHDAY, ""),
    )
    coordinator = SmartschoolCoordinator(hass, entry, api)

    try:
        # Raises ConfigEntryNotReady / ConfigEntryAuthFailed so HA retries or asks to reauth.
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await hass.async_add_executor_job(api.close)
        raise

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SmartschoolConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await hass.async_add_executor_job(entry.runtime_data.api.close)

    return unload_ok
