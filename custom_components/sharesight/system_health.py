"""System health info for the Sharesight integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components import system_health
from homeassistant.core import HomeAssistant, callback

from .const import API_URL_BASE, DOMAIN, EDGE_API_URL_BASE


@callback
def async_register(
    hass: HomeAssistant, register: system_health.SystemHealthRegistration
) -> None:
    """Register system health info."""
    register.async_register_info(_system_health_info)


async def _system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    """Return system health info for Sharesight."""
    entries = hass.data.get(DOMAIN, {})
    info: dict[str, Any] = {
        "configured_portfolios": len(entries),
        "can_reach_api": system_health.async_check_can_reach_url(hass, API_URL_BASE),
        "can_reach_edge_api": system_health.async_check_can_reach_url(
            hass, EDGE_API_URL_BASE
        ),
    }

    last_update: float | None = None
    for entry_data in entries.values():
        coordinator = entry_data.get("coordinator")
        if coordinator is None:
            continue
        dt = coordinator.last_update_success_time
        if dt is None:
            continue
        ts = dt.timestamp()
        if last_update is None or ts > last_update:
            last_update = ts

    if last_update is not None:
        from homeassistant.util import dt as dt_util

        info["last_successful_update"] = dt_util.utc_from_timestamp(last_update)
    return info
