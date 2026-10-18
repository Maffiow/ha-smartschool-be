"""Shared entity helpers for Smartschool BE."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo

from .const import CONF_BASE_URL, DOMAIN


def account_device_info(entry: ConfigEntry) -> DeviceInfo:
    """One device per Smartschool account that groups its entities."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Smartschool {entry.data[CONF_USERNAME]}",
        manufacturer="Smartschool",
        model="Account",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=entry.data[CONF_BASE_URL],
    )
