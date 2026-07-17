"""Diagnostics support for the Sharesight integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .data import SharesightConfigEntry

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
    # Personal-name fields off the my_user profile.  These keys are specific
    # enough that they don't collide with non-personal data elsewhere in the
    # dump.  The generic "name" key is NOT listed here because
    # async_redact_data matches a key wherever it appears in the nested
    # structure, and coordinator.data carries many non-personal "name" values
    # (instruments, cash accounts, sectors, benchmarks, ...).  The personal
    # account-holder "name" is redacted by scoping to the my_user subtree
    # below instead.
    "first_name",
    "last_name",
    "full_name",
}

# The my_user profile carries the account holder's personal name under a bare
# "name" key, which would over-redact if added to the global set above.
_REDACT_MY_USER = {"name"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SharesightConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    # runtime_data is absent when the entry failed to set up or is unloaded
    # (Home Assistant deletes it on unload), so read it defensively.
    runtime_data = getattr(entry, "runtime_data", None)
    coordinator = runtime_data.coordinator if runtime_data is not None else None

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
            "setup_complete": bool(getattr(coordinator, "_portfolio_detail", None)),
            "start_financial_year": getattr(coordinator, "start_financial_year", None),
            "end_financial_year": getattr(coordinator, "end_financial_year", None),
            "optional_endpoints_on_cooldown": list(
                getattr(coordinator, "_optional_endpoint_cooldowns", {}).keys()
            ),
            "cash_accounts_on_cooldown": list(
                getattr(coordinator, "_cash_tx_account_cooldowns", {}).keys()
            ),
            "data_keys": sorted(list((coordinator.data or {}).keys())),
            "data": _redact_coordinator_data(coordinator.data or {}),
        }

    return diagnostics


def _redact_coordinator_data(data: dict[str, Any]) -> dict[str, Any]:
    """Redact coordinator.data, scoping personal-name masking to my_user.

    async_redact_data matches keys anywhere in the nested structure, so the
    account holder's personal "name" is redacted only within the my_user
    subtree to avoid masking non-personal name-keyed values elsewhere.
    """
    redacted = async_redact_data(data, _REDACT_DATA)
    my_user = redacted.get("my_user")
    if isinstance(my_user, dict):
        redacted["my_user"] = async_redact_data(my_user, _REDACT_MY_USER)
    return redacted
