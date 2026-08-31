"""Portable response-service contract regressions."""

from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

from custom_components.sharesight import services


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
