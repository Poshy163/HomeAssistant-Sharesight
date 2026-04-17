"""Diagnostics support for the Sharesight integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

# Fields to redact from any dumped data.
_REDACT_ENTRY = {
    "token",
    "access_token",
    "refresh_token",
    "client_id",
    "client_secret",
    "authorization_code",
    "redirect_uri",
    "email",
    "login_email",
    "id_token",
}

# Fields to redact from coordinator.data (personal info, account numbers, etc).
_REDACT_DATA = {
    "email",
    "login_email",
    "cash_account_number",
    "account_number",
    "bsb",
    "iban",
    "swift",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator = data.get("coordinator")

    diagnostics: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), _REDACT_ENTRY),
            "options": dict(entry.options),
            "source": entry.source,
            "unique_id": entry.unique_id,
            "state": str(entry.state),
        },
        "coordinator": {},
    }

    if coordinator is not None:
        diagnostics["coordinator"] = {
            "last_update_success": coordinator.last_update_success,
            "update_interval_seconds": coordinator.update_interval.total_seconds()
            if coordinator.update_interval
            else None,
            "portfolio_id": getattr(coordinator, "portfolio_id", None),
            "started_up": getattr(coordinator, "started_up", None),
            "start_financial_year": getattr(coordinator, "start_financial_year", None),
            "end_financial_year": getattr(coordinator, "end_financial_year", None),
            "optional_endpoints_on_cooldown": list(
                getattr(coordinator, "_optional_endpoint_cooldowns", {}).keys()
            ),
            "cash_accounts_on_cooldown": list(
                getattr(coordinator, "_cash_tx_account_cooldowns", {}).keys()
            ),
            "data_keys": sorted(list((coordinator.data or {}).keys())),
            "data": async_redact_data(coordinator.data or {}, _REDACT_DATA),
        }

    return diagnostics
