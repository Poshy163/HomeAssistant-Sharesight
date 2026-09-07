"""Regression cases confirmed during the September portfolio audit."""

from datetime import date

import pytest

from custom_components.sharesight import analytics
from custom_components.sharesight.enum import ALL_HOLDING_DESCRIPTIONS, TOTALS_SENSOR_DESCRIPTIONS

from .test_coordinator import make_coordinator, post_processed
from .test_entity_regressions import (
    _coordinator,
    _description,
    _entry,
    _sensor,
    _source_description,
    _weight_data,
)
from .test_entity_regressions import stub_device_info as stub_device_info

pytestmark = pytest.mark.usefixtures("stub_device_info")


@pytest.mark.parametrize("quantity", [0.000001, -0.000001])
@pytest.mark.parametrize("valid", [True, None])
def test_small_valid_fractional_positions_are_preserved(quantity, valid):
    assert analytics.is_open_position(
        {"quantity": quantity, "value": 0.001, "valid_position": valid}
    )


@pytest.mark.parametrize("value", [None, "bad", float("nan")])
def test_missing_holding_value_does_not_become_zero_weight(value):
    data = _weight_data(1000.0)
    data["holdings"]["holdings"][0]["value"] = value
    sensor = _sensor(
        _source_description(ALL_HOLDING_DESCRIPTIONS, "holdings_list", "weight_percent"),
        _entry(),
        _coordinator(data=data),
        local_name="AAA",
        display_name="AAA portfolio weight",
    )
    assert sensor.native_value is None


@pytest.mark.parametrize("gain", [None, "bad", float("inf")])
def test_incomplete_closed_returns_are_unknown(gain):
    sensor = _sensor(
        _description(TOTALS_SENSOR_DESCRIPTIONS, "closed_positions_total_gain"),
        _entry(),
        _coordinator(
            data={
                "all_time": {
                    "holdings": [
                        {"quantity": 0, "value": 0, "total_gain": 12},
                        {"quantity": 0, "value": 0, "total_gain": gain},
                    ]
                }
            }
        ),
        display_name="Closed positions return",
    )
    assert sensor.native_value is None


def test_all_performance_windows_share_limited_holding_scope():
    coordinator = make_coordinator()
    today = date(2024, 3, 4)
    for endpoint in coordinator._required_endpoints(today) + coordinator._slow_endpoints(today):
        if "performance" in endpoint.path:
            assert endpoint.params["include_limited"] == "true"


def test_monday_day_and_week_requests_have_identical_dates():
    coordinator = make_coordinator()
    endpoints = {e.key: e for e in coordinator._required_endpoints(date(2024, 3, 4))}
    assert endpoints["one-day"].params == endpoints["one-week"].params


@pytest.mark.parametrize("inception", ["not-a-date", "2024-02-30", 42, None])
def test_unknown_inception_uses_api_lifetime_default_not_ytd(inception):
    coordinator = make_coordinator()
    coordinator._portfolio_detail = coordinator._normalise_portfolio_detail(
        {"inception_date": inception}
    )
    assert "inception_date" not in coordinator._portfolio_detail
    endpoint = next(e for e in coordinator._slow_endpoints(date(2024, 3, 4)) if e.key == "all_time")
    assert "start_date" not in endpoint.params


@pytest.mark.parametrize(
    ("payouts", "expected"),
    [
        ({"payouts": []}, 0),
        ({}, None),
        (None, None),
        ({"payouts": [{"amount": None}]}, None),
        ({"payouts": [{"amount": 10, "currency": "USD"}]}, None),
        ({"payouts": [{"amount": 10, "currency": "USD", "exchange_rate": 0.5}]}, 20),
    ],
)
def test_income_preserves_missing_foreign_and_empty_payloads(payouts, expected):
    from custom_components.sharesight.sensor import _get_income_summary

    data = post_processed(payouts=payouts)
    income = data["income_report"]
    assert income["total_income"] == expected
    assert _get_income_summary(income, data["report"])["total_income"] == expected
