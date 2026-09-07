"""Portable response-service contract regressions."""

from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.sharesight import services


@pytest.fixture
def history_coordinator(monkeypatch):
    coordinator = SimpleNamespace(
        current_date=date(2026, 9, 7),
        is_locked_out=False,
        data={
            "holdings": {
                "holdings": [
                    {"id": 123, "instrument": {"id": 456, "code": "TEST", "currency_code": "USD"}}
                ]
            }
        },
        async_get_holding_value_history=AsyncMock(),
        async_get_instrument_price_history=AsyncMock(),
        async_get_portfolio_value=AsyncMock(return_value={"portfolio_value": {"value": 100}}),
    )
    monkeypatch.setattr(services, "_resolve_coordinator", lambda *_: coordinator)
    return coordinator


def history_call(**overrides):
    return SimpleNamespace(
        data={"symbol": "test", "start_date": "2026-08-01", "end_date": "2026-08-31", **overrides}
    )


@pytest.mark.parametrize(
    "wrapper",
    [
        lambda p: p,
        lambda p: {"chart": {"data": p}},
        lambda p: {"values": p},
        lambda p: {"portfolio_value_data": p},
    ],
)
def test_holding_history_preserves_zero_filters_dates_and_does_not_invent_currency(
    history_coordinator, wrapper
):
    history_coordinator.async_get_holding_value_history.return_value = wrapper(
        [
            {"timestamp": "2026-09-01", "value": 300},
            {"timestamp": "2026-08-03", "value": "12.25"},
            {"timestamp": "2026-08-01", "value": 0},
            {"timestamp": "2026-08-03", "value": 13},
        ]
    )
    result = asyncio.run(services._get_holding_value_history(None, history_call()))
    assert result["points"] == [
        {"date": "2026-08-01", "value": 0},
        {"date": "2026-08-03", "value": 13},
    ]
    assert result["currency"] is None
    assert result["count"] == 2
    history_coordinator.async_get_holding_value_history.assert_awaited_once_with(123, "2026-08-01")


@pytest.mark.parametrize("source_key", ["prices", "instrument_prices"])
def test_price_history_retains_ohlcv_and_signals_more_pages(history_coordinator, source_key):
    history_coordinator.async_get_instrument_price_history.return_value = {
        source_key: [
            {
                "last_traded_on": "2026-08-02",
                "last_traded_value": 0,
                "open": 1,
                "high": 2,
                "low": 0,
                "close": 0,
                "volume": 0,
            },
            {"date": "2026-09-01", "close": 99},
        ],
        "links": {"next": "https://example.test/untrusted-next-page"},
    }
    result = asyncio.run(services._get_instrument_price_history(None, history_call()))
    assert result["count"] == 1
    assert result["prices"][0]["close"] == result["prices"][0]["volume"] == 0
    assert result["currency"] == "USD"
    assert result["has_more"] is True
    history_coordinator.async_get_instrument_price_history.assert_awaited_once_with(
        456, "2026-08-01", "2026-08-31"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"start_date": "2026-09-01"},
        {"start_date": "2025-01-01"},
        {"end_date": "2026-09-08"},
        {"start_date": "2026-02-30"},
        {"symbol": "ABSENT"},
    ],
)
def test_history_rejects_invalid_requests_before_io(history_coordinator, overrides):
    with pytest.raises(ServiceValidationError):
        asyncio.run(services._get_holding_value_history(None, history_call(**overrides)))
    history_coordinator.async_get_holding_value_history.assert_not_awaited()


@pytest.mark.parametrize(
    "handler,method",
    [
        (services._get_holding_value_history, "async_get_holding_value_history"),
        (services._get_instrument_price_history, "async_get_instrument_price_history"),
    ],
)
def test_history_entitlement_failure_is_not_an_empty_history(history_coordinator, handler, method):
    getattr(history_coordinator, method).return_value = {"error": "Not entitled", "status": 403}
    assert asyncio.run(handler(None, history_call())) == {"error": "Not entitled", "status": 403}


def test_current_value_action_uses_one_lightweight_read(history_coordinator):
    assert asyncio.run(services._get_portfolio_value(None, SimpleNamespace(data={}))) == {
        "portfolio_value": {"value": 100}
    }
    history_coordinator.async_get_portfolio_value.assert_awaited_once_with()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"unexpected": []},
        {"chart": {"data": [{"date": "bad", "value": "bad", "in_portfolio_currency": "bad"}]}},
    ],
)
def test_invalid_value_history_is_not_reported_as_empty_success(history_coordinator, payload):
    history_coordinator.async_get_holding_value_history.return_value = payload
    result = asyncio.run(services._get_holding_value_history(None, history_call()))
    assert result["error"] == "Value history unavailable"


def test_explicit_empty_history_remains_a_valid_empty_result(history_coordinator):
    history_coordinator.async_get_holding_value_history.return_value = {
        "holding_value_data": {"chart": {"data": []}}
    }
    result = asyncio.run(services._get_holding_value_history(None, history_call()))
    assert result["points"] == []
    assert "error" not in result


def test_market_qualification_prevents_wrong_instrument_history(history_coordinator):
    first = history_coordinator.data["holdings"]["holdings"][0]
    first["instrument"]["market_code"] = "NYSE"
    history_coordinator.data["holdings"]["holdings"].append(
        {
            "id": 789,
            "instrument": {"id": 999, "code": "TEST", "market_code": "ASX", "currency_code": "AUD"},
        }
    )
    with pytest.raises(ServiceValidationError):
        asyncio.run(services._get_instrument_price_history(None, history_call()))
    history_coordinator.async_get_instrument_price_history.assert_not_awaited()
    history_coordinator.async_get_instrument_price_history.return_value = {"prices": []}
    result = asyncio.run(
        services._get_instrument_price_history(None, history_call(symbol="TEST.ASX"))
    )
    assert result["currency"] == "AUD"
    history_coordinator.async_get_instrument_price_history.assert_awaited_once_with(
        999, "2026-08-01", "2026-08-31"
    )


def test_history_cooldown_prevents_requests(history_coordinator):
    history_coordinator.lockout_seconds_remaining = 60
    with pytest.raises(ServiceValidationError):
        asyncio.run(services._get_holding_value_history(None, history_call()))
    history_coordinator.async_get_holding_value_history.assert_not_awaited()


def test_income_service_labels_native_and_portfolio_payout_amounts(monkeypatch) -> None:
    """Automations must never receive an unlabelled mixed-currency amount."""
    coordinator = SimpleNamespace(
        current_date=date(2026, 8, 27),
        portfolio_currency="AUD",
        data={
            "income_report": {
                "payouts": [],
                "upcoming_payouts": [
                    {
                        "symbol": "US:TEST",
                        "amount": 25.0,
                        "currency": "USD",
                        "exchange_rate": 0.5,
                        "goes_ex_on": "2026-09-01",
                        "paid_on": "2026-09-15",
                    }
                ],
            },
            "holding_income": {},
        },
    )
    monkeypatch.setattr(services, "_resolve_coordinator", lambda _hass, _call: coordinator)

    result = asyncio.run(services._get_income(None, SimpleNamespace(data={})))

    assert result["currency"] == "AUD"
    assert result["upcoming"] == [
        {
            "symbol": "US:TEST",
            "amount": 50.0,
            "currency": "AUD",
            "native_amount": 25.0,
            "native_currency": "USD",
            "exchange_rate": 0.5,
            "ex_date": "2026-09-01",
            "pay_date": "2026-09-15",
        }
    ]


def test_export_raw_snapshot_returns_all_cached_source_responses(monkeypatch) -> None:
    """The troubleshooting export must never issue another API request."""
    value_series = {"data": [{"date": "2026-08-31", "value": 123.45}]}
    benchmark = {"instrument": {"code": "ASX200"}, "total_gain": 4.2}
    trades = {"trades": [{"id": 123}]}
    coordinator = SimpleNamespace(
        data={"value_series": value_series, "benchmark": benchmark, "trades": trades},
        _raw_responses={"value_series": value_series, "benchmark": benchmark, "trades": trades},
    )
    monkeypatch.setattr(services, "_resolve_coordinator", lambda _hass, _call: coordinator)

    result = asyncio.run(services._export_raw_snapshot(None, SimpleNamespace(data={})))

    assert result == {
        "value_series": value_series,
        "benchmark": benchmark,
        "trades": trades,
        "unavailable": [
            key
            for key in services.RAW_SNAPSHOT_SOURCE_KEYS
            if key not in {"value_series", "benchmark", "trades"}
        ],
    }


def test_export_raw_snapshot_falls_back_to_current_data_after_a_hot_reload(monkeypatch) -> None:
    coordinator = SimpleNamespace(data={"value_series": {"data": []}})
    monkeypatch.setattr(services, "_resolve_coordinator", lambda _hass, _call: coordinator)

    result = asyncio.run(services._export_raw_snapshot(None, SimpleNamespace(data={})))

    assert result["value_series"] == {"data": []}
    assert "benchmark" not in result
    assert "benchmark" in result["unavailable"]
