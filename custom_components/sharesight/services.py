"""Response services for the Sharesight integration.

Four SupportsResponse.ONLY services that mine the data the coordinator already
holds (or, for generate_performance_report, make a single on-demand call) and
return it as a structured response for scripts/templates:

- get_portfolio_summary   — headline value / period gains / movers / income.
- get_holdings            — sortable, limitable holdings list.
- get_income              — trailing / YTD / forward dividend income.
- generate_performance_report — arbitrary date-range performance report.

Each service selects the portfolio via an optional config_entry_id or
device_id, falling back to the sole configured portfolio and raising a clear
ServiceValidationError when the target is ambiguous.
"""
from __future__ import annotations

import functools
import logging
from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.const import CONF_DEVICE_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from . import analytics
from .const import DOMAIN
from .sensor import (
    _get_holding_gain,
    _get_holding_gain_percent,
    _get_holding_symbol,
    _get_holding_value,
    _get_top_gain_holding,
    _get_worst_gain_holding,
)

_LOGGER: logging.Logger = logging.getLogger(__package__)

SERVICE_GET_PORTFOLIO_SUMMARY = "get_portfolio_summary"
SERVICE_GET_HOLDINGS = "get_holdings"
SERVICE_GET_INCOME = "get_income"
SERVICE_GENERATE_PERFORMANCE_REPORT = "generate_performance_report"

_SERVICES = (
    SERVICE_GET_PORTFOLIO_SUMMARY,
    SERVICE_GET_HOLDINGS,
    SERVICE_GET_INCOME,
    SERVICE_GENERATE_PERFORMANCE_REPORT,
)

CONF_CONFIG_ENTRY_ID = "config_entry_id"
CONF_SORT_BY = "sort_by"
CONF_LIMIT = "limit"
CONF_START_DATE = "start_date"
CONF_END_DATE = "end_date"
CONF_GROUPING = "grouping"
CONF_CONSOLIDATED = "consolidated"
CONF_INCLUDE_SALES = "include_sales"

# Holdings sort keys map onto the response dict keys, so a single name both
# selects the sort field and matches the returned column.
SORT_KEYS = ("value", "capital_gain", "capital_gain_percent", "symbol")

_TARGET_FIELDS = {
    vol.Optional(CONF_CONFIG_ENTRY_ID): cv.string,
    vol.Optional(CONF_DEVICE_ID): cv.string,
}

GET_PORTFOLIO_SUMMARY_SCHEMA = vol.Schema({**_TARGET_FIELDS})
GET_HOLDINGS_SCHEMA = vol.Schema(
    {
        **_TARGET_FIELDS,
        vol.Optional(CONF_SORT_BY, default="value"): vol.In(SORT_KEYS),
        vol.Optional(CONF_LIMIT): vol.All(vol.Coerce(int), vol.Range(min=1)),
    }
)
GET_INCOME_SCHEMA = vol.Schema({**_TARGET_FIELDS})
GENERATE_PERFORMANCE_REPORT_SCHEMA = vol.Schema(
    {
        **_TARGET_FIELDS,
        vol.Required(CONF_START_DATE): cv.string,
        vol.Required(CONF_END_DATE): cv.string,
        vol.Optional(CONF_GROUPING): cv.string,
        vol.Optional(CONF_CONSOLIDATED): cv.boolean,
        vol.Optional(CONF_INCLUDE_SALES): cv.boolean,
    }
)


def _resolve_coordinator(hass: HomeAssistant, call: ServiceCall) -> Any:
    """Pick the coordinator addressed by config_entry_id / device_id.

    Falls back to the sole configured portfolio; raises a clear
    ServiceValidationError when the target cannot be determined unambiguously.
    """
    domain_data: dict[str, Any] = hass.data.get(DOMAIN, {})
    entries = [d for d in domain_data.values() if isinstance(d, dict) and "coordinator" in d]

    entry_id = call.data.get(CONF_CONFIG_ENTRY_ID)
    if entry_id:
        entry_data = domain_data.get(entry_id)
        if not isinstance(entry_data, dict) or "coordinator" not in entry_data:
            raise ServiceValidationError(
                f"No Sharesight portfolio for config_entry_id {entry_id}"
            )
        return entry_data["coordinator"]

    device_id = call.data.get(CONF_DEVICE_ID)
    if device_id:
        device = dr.async_get(hass).async_get(device_id)
        if device is None:
            raise ServiceValidationError(f"No device with id {device_id}")
        for cfg_entry_id in device.config_entries:
            entry_data = domain_data.get(cfg_entry_id)
            if isinstance(entry_data, dict) and "coordinator" in entry_data:
                return entry_data["coordinator"]
        raise ServiceValidationError(
            f"Device {device_id} is not a Sharesight portfolio"
        )

    if not entries:
        raise ServiceValidationError("No Sharesight portfolios are configured")
    if len(entries) > 1:
        raise ServiceValidationError(
            "Multiple Sharesight portfolios are configured; specify "
            "config_entry_id or device_id"
        )
    return entries[0]["coordinator"]


def _validate_date(value: str, field: str) -> str:
    """Ensure a service date field is a valid YYYY-MM-DD string."""
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except (ValueError, TypeError) as err:
        raise ServiceValidationError(
            f"{field} must be a date in YYYY-MM-DD format, got {value!r}"
        ) from err
    return value


def _portfolio_currency(data: dict[str, Any]) -> str:
    """Best-effort portfolio base currency, mirroring the sensor platform."""
    portfolios = data.get("portfolios", [])
    if portfolios and isinstance(portfolios[0], dict):
        return portfolios[0].get("currency_code", "USD")
    report = data.get("report", {})
    currency = report.get("currency") if isinstance(report, dict) else None
    if isinstance(currency, dict):
        return currency.get("code", "USD")
    return "USD"


def _period_gain(data: dict[str, Any], key: str) -> dict[str, Any]:
    """Extract {gain, percent} from a period performance window."""
    window = data.get(key)
    if not isinstance(window, dict):
        return {"gain": None, "percent": None}
    return {
        "gain": window.get("total_gain"),
        "percent": window.get("total_gain_percent"),
    }


def _total_cash(report: dict[str, Any]) -> float:
    """Sum the report's cash-account balances."""
    total = 0.0
    cash_accounts = report.get("cash_accounts", []) if isinstance(report, dict) else []
    for account in cash_accounts or []:
        if not isinstance(account, dict):
            continue
        try:
            total += float(account.get("value", 0) or 0)
        except (TypeError, ValueError):
            continue
    return round(total, 2)


async def _get_portfolio_summary(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse:
    coordinator = _resolve_coordinator(hass, call)
    data: dict[str, Any] = coordinator.data or {}
    report = data.get("report", {}) if isinstance(data.get("report"), dict) else {}
    holdings_data = data.get("holdings", {}) if isinstance(data.get("holdings"), dict) else {}
    holdings = holdings_data.get("holdings", []) or []
    holding_income = data.get("holding_income", {})

    dividends_ttm = 0.0
    if isinstance(holding_income, dict):
        for entry in holding_income.values():
            if isinstance(entry, dict):
                try:
                    dividends_ttm += float(entry.get("ttm_income", 0) or 0)
                except (TypeError, ValueError):
                    continue
    if not dividends_ttm:
        income_report = data.get("income_report", {})
        if isinstance(income_report, dict) and income_report.get("total_income"):
            try:
                dividends_ttm = float(income_report.get("total_income") or 0)
            except (TypeError, ValueError):
                dividends_ttm = 0.0

    return {
        "value": report.get("value"),
        "day": _period_gain(data, "one-day"),
        "week": _period_gain(data, "one-week"),
        "month": _period_gain(data, "one-month"),
        "ytd": _period_gain(data, "ytd"),
        "fy": _period_gain(data, "financial-year"),
        "holding_count": len(holdings),
        "total_cash": _total_cash(report),
        "top_mover": _get_top_gain_holding(holdings_data),
        "worst_mover": _get_worst_gain_holding(holdings_data),
        "dividends_ttm": round(dividends_ttm, 2),
        "currency": _portfolio_currency(data),
    }


async def _get_holdings(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    coordinator = _resolve_coordinator(hass, call)
    data: dict[str, Any] = coordinator.data or {}
    holdings_data = data.get("holdings", {}) if isinstance(data.get("holdings"), dict) else {}
    holdings = holdings_data.get("holdings", []) or []
    base_currency = _portfolio_currency(data)

    result: list[dict[str, Any]] = []
    for holding in holdings:
        if not isinstance(holding, dict):
            continue
        result.append(
            {
                "symbol": _get_holding_symbol(holding),
                "market": analytics.holding_market(holding),
                "value": _get_holding_value(holding),
                "quantity": holding.get("quantity"),
                "capital_gain": _get_holding_gain(holding),
                "capital_gain_percent": _get_holding_gain_percent(holding),
                "currency": holding.get("currency_code")
                or holding.get("currency")
                or base_currency,
            }
        )

    sort_by = call.data.get(CONF_SORT_BY, "value")
    if sort_by == "symbol":
        result.sort(key=lambda h: (h.get("symbol") or ""))
    else:
        result.sort(
            key=lambda h: h[sort_by] if h.get(sort_by) is not None else float("-inf"),
            reverse=True,
        )

    limit = call.data.get(CONF_LIMIT)
    if limit is not None:
        result = result[:limit]

    return {"holdings": result, "count": len(result)}


async def _get_income(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    coordinator = _resolve_coordinator(hass, call)
    data: dict[str, Any] = coordinator.data or {}
    income_report = data.get("income_report", {})
    if not isinstance(income_report, dict):
        income_report = {}
    holding_income = data.get("holding_income", {})

    ttm = 0.0
    if isinstance(holding_income, dict):
        for entry in holding_income.values():
            if isinstance(entry, dict):
                try:
                    ttm += float(entry.get("ttm_income", 0) or 0)
                except (TypeError, ValueError):
                    continue

    # Calendar-year-to-date received income from the historic payouts list.
    this_year = str(getattr(coordinator, "current_date", None) or "")[:4]
    ytd = 0.0
    for payout in income_report.get("payouts", []) or []:
        if not isinstance(payout, dict):
            continue
        paid_on = payout.get("paid_on") or payout.get("date")
        if this_year and str(paid_on)[:4] != this_year:
            continue
        try:
            ytd += float(payout.get("amount", 0) or 0)
        except (TypeError, ValueError):
            continue

    upcoming: list[dict[str, Any]] = []
    for payout in income_report.get("upcoming_payouts", []) or []:
        if not isinstance(payout, dict):
            continue
        upcoming.append(
            {
                "symbol": payout.get("symbol"),
                "amount": payout.get("amount") or payout.get("gross_amount"),
                "ex_date": payout.get("ex_date"),
                "pay_date": payout.get("pay_date") or payout.get("paid_on"),
            }
        )

    return {
        "ttm": round(ttm, 2),
        "ytd": round(ytd, 2),
        "next_30d": income_report.get("income_30d"),
        "upcoming": upcoming,
    }


async def _generate_performance_report(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse:
    coordinator = _resolve_coordinator(hass, call)
    start_date = _validate_date(call.data[CONF_START_DATE], CONF_START_DATE)
    end_date = _validate_date(call.data[CONF_END_DATE], CONF_END_DATE)

    response = await coordinator.async_generate_performance_report(
        start_date,
        end_date,
        grouping=call.data.get(CONF_GROUPING),
        consolidated=call.data.get(CONF_CONSOLIDATED),
        include_sales=call.data.get(CONF_INCLUDE_SALES),
    )
    # The coordinator surfaces API-level failures as {"error": ...}; pass them
    # through in the response envelope rather than raising.
    if isinstance(response, dict):
        return response
    return {"result": response}


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Sharesight response services (idempotent, domain-global)."""
    definitions = (
        (SERVICE_GET_PORTFOLIO_SUMMARY, _get_portfolio_summary, GET_PORTFOLIO_SUMMARY_SCHEMA),
        (SERVICE_GET_HOLDINGS, _get_holdings, GET_HOLDINGS_SCHEMA),
        (SERVICE_GET_INCOME, _get_income, GET_INCOME_SCHEMA),
        (
            SERVICE_GENERATE_PERFORMANCE_REPORT,
            _generate_performance_report,
            GENERATE_PERFORMANCE_REPORT_SCHEMA,
        ),
    )
    for name, handler, schema in definitions:
        if hass.services.has_service(DOMAIN, name):
            continue
        hass.services.async_register(
            DOMAIN,
            name,
            functools.partial(handler, hass),
            schema=schema,
            supports_response=SupportsResponse.ONLY,
        )


def async_unload_services(hass: HomeAssistant) -> None:
    """Remove the Sharesight response services on the last-entry unload."""
    for name in _SERVICES:
        if hass.services.has_service(DOMAIN, name):
            hass.services.async_remove(DOMAIN, name)
