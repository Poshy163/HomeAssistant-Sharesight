"""Diagnostics support for the Sharesight integration.

Two things changed here relative to a naive dump of ``coordinator.data``.

**Size.** A real portfolio's payload can exceed 1.4 MB. What a maintainer
actually needs is which endpoint families returned, how old the data is, and
the shape of each block. Every block is therefore reduced to counts, schema
field names and byte size; no portfolio values or rows are reproduced.

**Privacy.**  Home Assistant's diagnostics UI presents redaction as a
guarantee, and users attach these files to public issues.  The account holder's
legal name and exact holdings can reach several API blocks, while the config
entry title often embeds the owner name. None of those payloads are copied;
entry identifiers and credentials are explicitly redacted as a second layer.
"""

from __future__ import annotations

import json
from time import monotonic
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    ACCOUNT_STANDARD,
    API_URL_BASE,
    CONF_ACCOUNT_TYPE,
    CONF_AUTO_REMOVE_STALE_DEVICES,
    CONF_ENABLE_EXTENDED_PERFORMANCE,
    CONF_ENABLE_HOLDING_ENTITIES,
    CONF_ENABLE_LTS_BACKFILL,
    CONF_SCAN_INTERVAL,
    DEFAULT_ACCOUNT_TYPE,
    DEFAULT_AUTO_REMOVE_STALE_DEVICES,
    DEFAULT_ENABLE_EXTENDED_PERFORMANCE,
    DEFAULT_ENABLE_HOLDING_ENTITIES,
    DEFAULT_ENABLE_LTS_BACKFILL,
    DEFAULT_SCAN_INTERVAL,
)
from .data import SharesightConfigEntry

# Credentials and account identifiers on the config entry.
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
    "portfolio_id",
    "auth_implementation",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SharesightConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    # runtime_data is absent when the entry failed to set up or is unloaded
    # (Home Assistant deletes it on unload), so read it defensively.
    runtime_data = getattr(entry, "runtime_data", None)
    coordinator = runtime_data.coordinator if runtime_data is not None else None
    account_type = entry.data.get(CONF_ACCOUNT_TYPE, DEFAULT_ACCOUNT_TYPE)

    diagnostics: dict[str, Any] = {
        "entry": {
            # The title embeds the portfolio name, which is the account
            # holder's name for a default Sharesight portfolio.
            "title": "Sharesight portfolio [redacted]",
            "version": entry.version,
            "minor_version": getattr(entry, "minor_version", None),
            "data": async_redact_data(dict(entry.data), _REDACT_ENTRY),
            "source": entry.source,
            "unique_id": "[redacted]",
            "state": str(entry.state),
        },
        "auth": _auth_state(entry, account_type),
        "options_effective": {
            CONF_SCAN_INTERVAL: entry.options.get(
                CONF_SCAN_INTERVAL, int(DEFAULT_SCAN_INTERVAL.total_seconds())
            ),
            CONF_ENABLE_LTS_BACKFILL: entry.options.get(
                CONF_ENABLE_LTS_BACKFILL, DEFAULT_ENABLE_LTS_BACKFILL
            ),
            CONF_AUTO_REMOVE_STALE_DEVICES: entry.options.get(
                CONF_AUTO_REMOVE_STALE_DEVICES, DEFAULT_AUTO_REMOVE_STALE_DEVICES
            ),
            CONF_ENABLE_HOLDING_ENTITIES: entry.options.get(
                CONF_ENABLE_HOLDING_ENTITIES, DEFAULT_ENABLE_HOLDING_ENTITIES
            ),
            CONF_ENABLE_EXTENDED_PERFORMANCE: entry.options.get(
                CONF_ENABLE_EXTENDED_PERFORMANCE,
                DEFAULT_ENABLE_EXTENDED_PERFORMANCE,
            ),
        },
        "entities": _entity_summary(hass, entry),
    }

    if coordinator is None:
        diagnostics["coordinator"] = {"loaded": False}
        return diagnostics

    data = coordinator.data or {}
    diagnostics["coordinator"] = {
        "loaded": True,
        "last_update_success": coordinator.last_update_success,
        # When the payload was really fetched, as opposed to when a poll last
        # returned without raising - which includes every degraded, serve-the-
        # previous-payload cycle.
        "data_fetched_at": _iso(coordinator.data_timestamp),
        "data_age_seconds": _seconds(coordinator.data_age),
        "degraded": coordinator.is_degraded,
        "degraded_reason": _redacted_reason(coordinator.degraded_reason, coordinator.portfolio_id),
        "last_update_success_time": _iso(coordinator.last_update_success_time),
        "update_interval_seconds": _seconds(coordinator.update_interval),
        "portfolio_id": "[redacted]",
        "portfolio_currency": coordinator.portfolio_currency,
        "setup_complete": bool(coordinator._portfolio_detail),
        "financial_year": {
            "start": coordinator.start_financial_year,
            "end": coordinator.end_financial_year,
        },
        "poll_count": coordinator._poll_count,
    }
    diagnostics["api"] = {
        "base_url": API_URL_BASE.get(account_type),
        "versions_in_use": ["v2", "v3"],
        "lockout_active": coordinator.lockout_seconds_remaining > 0,
        "lockout_seconds_remaining": coordinator.lockout_seconds_remaining,
        "lockout_reason_present": coordinator._lockout_reason is not None,
        # SharesightAPI 1.4 returns response-local metadata, so concurrent
        # requests can safely feed the shared application-level gate.
        "documented_requests_per_minute": 360,
        "documented_concurrent_report_limit": 3,
        "observed_requests_per_minute_limit": getattr(
            coordinator._request_gate, "minute_limit", None
        ),
        "observed_requests_remaining": getattr(coordinator._request_gate, "minute_remaining", None),
    }
    diagnostics["endpoints"] = {
        "parked_count": len(_active_cooldowns(coordinator._optional_endpoint_cooldowns)),
        "cash_accounts_parked_count": len(
            _active_cooldowns(coordinator._cash_tx_account_cooldowns)
        ),
        "unsupported_count": len(coordinator._unsupported_endpoints),
        "carried_forward": _carry_forward_ages(coordinator),
        "logged_failure_count": len(coordinator._logged_failures),
    }
    diagnostics["data_summary"] = {key: _summarise(value) for key, value in sorted(data.items())}
    return diagnostics


def _auth_state(entry: SharesightConfigEntry, account_type: str) -> dict[str, Any]:
    """What can be said about the token without revealing any of it."""
    token = entry.data.get("token")
    token = token if isinstance(token, dict) else {}
    return {
        "account_type": account_type,
        "is_developer_sandbox": account_type != ACCOUNT_STANDARD,
        "has_auth_implementation": bool(entry.data.get("auth_implementation")),
        "has_access_token": bool(token.get("access_token")),
        "has_refresh_token": bool(token.get("refresh_token")),
        "token_type": token.get("token_type") or token.get("type"),
        "expires_at": token.get("expires_at"),
        "expires_in": token.get("expires_in"),
        "scope": token.get("scope"),
    }


def _entity_summary(hass: HomeAssistant, entry: SharesightConfigEntry) -> dict[str, Any]:
    """Counts per platform, plus how many the user has disabled."""
    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    per_platform: dict[str, int] = {}
    disabled = 0
    for registry_entry in entries:
        per_platform[registry_entry.domain] = per_platform.get(registry_entry.domain, 0) + 1
        if registry_entry.disabled_by is not None:
            disabled += 1
    return {"total": len(entries), "by_platform": per_platform, "disabled": disabled}


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _seconds(value: Any) -> float | None:
    return value.total_seconds() if value is not None else None


def _redacted_reason(reason: Any, portfolio_id: Any) -> str | None:
    """Keep an actionable failure reason without exposing the portfolio id."""
    if reason is None:
        return None
    return str(reason).replace(str(portfolio_id), "[redacted]")


def _active_cooldowns(cooldowns: Any) -> dict[str, int]:
    """``{key: seconds of backoff left}`` for cooldowns that are still active.

    A cooldown entry is only cleared when the endpoint next succeeds, so the
    raw map accumulates every endpoint that has ever failed.  Filtering on the
    deadline keeps this in step with the "Endpoints on Cooldown" sensor and
    shows how long until the next retry.
    """
    if not isinstance(cooldowns, dict):
        return {}
    now = monotonic()
    return {
        str(key): int(info["next_retry"] - now)
        for key, info in cooldowns.items()
        if isinstance(info, dict)
        and isinstance(info.get("next_retry"), (int, float))
        and info["next_retry"] > now
    }


def _carry_forward_ages(coordinator: Any) -> dict[str, int]:
    """``{key: seconds since that payload was last fetched}``."""
    cache = getattr(coordinator, "_carry_forward", None)
    if not isinstance(cache, dict):
        return {}
    now = monotonic()
    return {
        str(key): int(now - when)
        for key, (_payload, when) in cache.items()
        if isinstance(when, (int, float))
    }


def _summarise(value: Any) -> Any:
    """A compact description of one coordinator payload key.

    Containers are described by shape and size so a maintainer can see
    "trades returned 292 rows with these fields" without the rows themselves.
    Arbitrary mapping keys can be holding symbols or account ids, and scalar
    values can be exact financial figures, so neither is reproduced.
    """
    if isinstance(value, dict):
        summary: dict[str, Any] = {
            "type": "dict",
            "field_count": len(value),
        }
        for name, inner in value.items():
            if isinstance(inner, list):
                summary.setdefault("lists", {})[name] = {
                    "count": len(inner),
                    "item_keys": sorted(inner[0])[:40]
                    if inner and isinstance(inner[0], dict)
                    else None,
                }
        summary["bytes"] = _payload_bytes(value)
        return summary
    if isinstance(value, list):
        return {
            "type": "list",
            "count": len(value),
            "item_keys": sorted(value[0])[:40] if value and isinstance(value[0], dict) else None,
            "bytes": _payload_bytes(value),
        }
    return {"type": type(value).__name__, "present": value is not None}


def _payload_bytes(value: Any) -> int | None:
    try:
        return len(json.dumps(value, default=str))
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None
