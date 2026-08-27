"""System health info for the Sharesight integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components import system_health
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import ACCOUNT_DEVELOPER, ACCOUNT_STANDARD, API_URL_BASE, DOMAIN


@callback
def async_register(hass: HomeAssistant, register: system_health.SystemHealthRegistration) -> None:
    """Register system health info."""
    register.async_register_info(_system_health_info)


async def _system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    """Return system health info for Sharesight."""
    loaded_entries = [
        entry
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]
    info: dict[str, Any] = {
        "configured_portfolios": len(loaded_entries),
        "can_reach_api": system_health.async_check_can_reach_url(
            hass, API_URL_BASE[ACCOUNT_STANDARD]
        ),
        "can_reach_edge_api": system_health.async_check_can_reach_url(
            hass, API_URL_BASE[ACCOUNT_DEVELOPER]
        ),
    }

    last_update: float | None = None
    for entry in loaded_entries:
        coordinator = entry.runtime_data.coordinator
        if coordinator is None:
            continue
        # data_timestamp, not last_update_success_time: the latter is
        # re-stamped on degraded polls that serve the previous payload.
        dt = coordinator.data_timestamp
        if dt is None:
            continue
        ts = dt.timestamp()
        if last_update is None or ts > last_update:
            last_update = ts

    if last_update is not None:
        info["last_successful_update"] = dt_util.utc_from_timestamp(last_update)
    return info
