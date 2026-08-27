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
