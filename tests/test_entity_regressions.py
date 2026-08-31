"""Portable regressions for calendar and sensor entity semantics.

These tests use the real Home Assistant entity classes but not the
pytest-homeassistant plugin, so they remain runnable on Windows with plugin
autoload disabled.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
import re
from types import SimpleNamespace

import pytest

from custom_components.sharesight import coordinator as coordinator_module
from custom_components.sharesight import sensor as sensor_module
from custom_components.sharesight import statistics_import
from custom_components.sharesight.calendar import SharesightDividendCalendar
from custom_components.sharesight.entity import SharesightBaseEntity
from custom_components.sharesight.enum import (
    ALL_HOLDING_DESCRIPTIONS,
    CASH_SENSOR_DESCRIPTIONS,
    LABEL_SENSOR_DESCRIPTIONS,
    MARKET_SENSOR_DESCRIPTIONS,
    SENSOR_DESCRIPTIONS,
    TAX_SENSOR_DESCRIPTIONS,
    TOTALS_SENSOR_DESCRIPTIONS,
    WATCHLIST_INSTRUMENT_SENSOR_DESCRIPTIONS,
)
from custom_components.sharesight.sensor import SharesightSensor

PORTFOLIO_ID = "123"
TODAY = date(2026, 8, 27)


@pytest.fixture(autouse=True)
def stub_device_info(monkeypatch) -> None:
    """Keep constructor tests independent of HA's live device registry."""
    monkeypatch.setattr(
        SharesightBaseEntity,
        "_make_device_info",
        lambda _self, **values: values,
    )


def _description(descriptions, key):
    return next(description for description in descriptions if description.key == key)


def _source_description(descriptions, key, sub_key):
    return next(
        description
        for description in descriptions
        if description.key == key and description.sub_key == sub_key
    )


def _entry():
    return SimpleNamespace(
        entry_id="entry-1",
        runtime_data=SimpleNamespace(claimed_sensor_unique_ids=set()),
    )


def _coordinator(*, data=None, currency="AUD", hass=None):
    return SimpleNamespace(
        data=data or {},
        portfolio_currency=currency,
        current_date=TODAY,
        hass=hass,
        entity_icons={},
        last_update_success=True,
    )


def _sensor(
    description,
    entry,
    coordinator,
    *,
    currency="AUD",
    local_name="",
    display_name="",
):
    return SharesightSensor(
        description,
        entry,
        coordinator,
        currency,
        PORTFOLIO_ID,
        False,
        local_name=local_name,
        display_name=display_name,
    )


def test_report_and_totals_value_sensors_have_distinct_unique_ids() -> None:
    """The all-time value must not claim the live report value's history."""
    entry = _entry()
    coordinator = _coordinator()
    report = _sensor(
        _description(SENSOR_DESCRIPTIONS, "value"),
        entry,
        coordinator,
        display_name="Portfolio value",
    )
    totals = _sensor(
        _description(TOTALS_SENSOR_DESCRIPTIONS, "value"),
        entry,
        coordinator,
        display_name="All-Time Value",
    )

    assert report.unique_id == "123_value_v2"
    assert totals.unique_id == "123_totals_value_v2"
    assert report.unique_id != totals.unique_id
    assert entry.runtime_data.claimed_sensor_unique_ids == {
        report.unique_id,
        totals.unique_id,
    }


@pytest.mark.parametrize(
    ("description", "family"),
    [
        (
            _description(
                WATCHLIST_INSTRUMENT_SENSOR_DESCRIPTIONS,
                "watchlist_instrument_price",
            ),
            "watchlist",
        ),
        (_description(LABEL_SENSOR_DESCRIPTIONS, "label_value"), "label"),
    ],
)
def test_lossy_legacy_slug_is_adopted_by_only_one_entity(monkeypatch, description, family) -> None:
    """Names that shared an old slug still receive one-to-one identities."""
    entry = _entry()
    coordinator = _coordinator(hass=object())
    legacy_unique_id = f"123_{family}_a_b_{description.key}_v2"

    class FakeRegistry:
        def async_get_entity_id(self, domain, platform, unique_id):
            if (domain, platform, unique_id) == (
                "sensor",
                "sharesight",
                legacy_unique_id,
            ):
                return "sensor.existing"
            return None

        def async_get(self, entity_id):
            assert entity_id == "sensor.existing"
            return SimpleNamespace(config_entry_id=entry.entry_id)

    monkeypatch.setattr(sensor_module.er, "async_get", lambda _hass: FakeRegistry())

    first = _sensor(
        description,
        entry,
        coordinator,
        currency="USD",
        local_name="A-B",
        display_name="A-B value",
    )
    second = _sensor(
        description,
        entry,
        coordinator,
        currency="USD",
        local_name="A B",
        display_name="A B value",
    )

    assert first.unique_id == legacy_unique_id
    assert second.unique_id == f"123_{family}_A B_{description.key}_v2"
    assert first.unique_id != second.unique_id


def test_portfolio_currency_unit_follows_coordinator_changes() -> None:
    """Report-currency changes update portfolio units without reloading entities."""
    coordinator = _coordinator(currency="AUD")
    portfolio_value = _sensor(
        _description(SENSOR_DESCRIPTIONS, "value"),
        _entry(),
        coordinator,
        currency="AUD",
        display_name="Portfolio value",
    )

    assert portfolio_value.native_unit_of_measurement == "AUD"
    coordinator.portfolio_currency = "NZD"
    assert portfolio_value.native_unit_of_measurement == "NZD"


def test_previously_niche_sensors_are_enabled_by_default() -> None:
    """Default-disabled descriptions must not hide a supported entity."""
    sensor = _sensor(
        _description(TAX_SENSOR_DESCRIPTIONS, "short_term_losses"),
        _entry(),
        _coordinator(),
        display_name="CGT Short Term Losses",
    )

    assert sensor.entity_registry_enabled_default is True


def test_instrument_currency_unit_does_not_follow_portfolio_currency() -> None:
    """A watched instrument's own price remains in its declared currency."""
    coordinator = _coordinator(currency="AUD")
    price = _sensor(
        _description(
            WATCHLIST_INSTRUMENT_SENSOR_DESCRIPTIONS,
            "watchlist_instrument_price",
        ),
        _entry(),
        coordinator,
        currency="USD",
        local_name="NYSE:TEST",
        display_name="NYSE:TEST price",
    )

    assert price.native_unit_of_measurement == "USD"
    coordinator.portfolio_currency = "NZD"
    assert price.native_unit_of_measurement == "USD"


@pytest.mark.parametrize(
    ("description", "data", "local_name"),
    [
        (ALL_HOLDING_DESCRIPTIONS[0], {"holdings": {"holdings": []}}, "AAA"),
        (MARKET_SENSOR_DESCRIPTIONS[0], {"report": {"sub_totals": []}}, "ASX"),
        (
            CASH_SENSOR_DESCRIPTIONS[0],
            {"report": {"cash_accounts": []}},
            "Broker Cash",
        ),
    ],
)
def test_authoritative_empty_dynamic_source_makes_old_entity_unavailable(
    description, data, local_name
) -> None:
    """A sold final holding/market/cash account must not remain Available/Unknown."""
    entity = _sensor(
        description,
        _entry(),
        _coordinator(data=data),
        local_name=local_name,
        display_name=f"{local_name} value",
    )

    assert entity._dynamic_item_present() is False
    assert entity.available is False


def test_monthly_value_maps_from_its_present_report_block() -> None:
    description = _source_description(SENSOR_DESCRIPTIONS, "total_gain", "one-month")
    entity = _sensor(
        description,
        _entry(),
        _coordinator(data={"one-month": {"total_gain": -332.97}}),
        display_name="Monthly Change Amount",
    )

    assert entity.available is True
    assert entity.native_value == -332.97


def test_monthly_value_is_unavailable_when_its_report_block_is_absent() -> None:
    description = _source_description(SENSOR_DESCRIPTIONS, "total_gain", "one-month")
    entity = _sensor(
        description,
        _entry(),
        _coordinator(data={"report": {"value": 1000.0}}),
        display_name="Monthly Change Amount",
    )

    assert entity.available is False
    assert entity.native_value is None


@pytest.mark.parametrize(
    ("description", "data", "local_name"),
    [
        (
            _source_description(TAX_SENSOR_DESCRIPTIONS, "tax_gain_loss", "capital_gains"),
            {},
            "",
        ),
        (
            _source_description(
                TAX_SENSOR_DESCRIPTIONS,
                "unrealised_tax_gain_loss",
                "unrealised_cgt",
            ),
            {},
            "",
        ),
        (
            _source_description(TAX_SENSOR_DESCRIPTIONS, "claimable_loss", "cgt_analytics"),
            {"cgt_analytics": {"claimable_loss": None}},
            "",
        ),
        (
            _source_description(
                TAX_SENSOR_DESCRIPTIONS,
                "largest_loss_amount",
                "cgt_analytics",
            ),
            {"cgt_analytics": {"largest_loss_amount": None}},
            "",
        ),
        (
            _source_description(ALL_HOLDING_DESCRIPTIONS, "holding_fundamental", "eps"),
            {
                "holdings": {"holdings": [{"instrument": {"code": "CRED"}}]},
                "instrument_lookup": {},
            },
            "CRED",
        ),
        (
            _source_description(ALL_HOLDING_DESCRIPTIONS, "holding_income", "ttm_income"),
            {
                "holdings": {"holdings": [{"instrument": {"code": "CRED"}}]},
                "holding_income": {},
            },
            "CRED",
        ),
        (
            _source_description(ALL_HOLDING_DESCRIPTIONS, "holding_trade", "brokerage_paid"),
            {
                "holdings": {"holdings": [{"instrument": {"code": "CRED"}}]},
                "holding_trades": {},
            },
            "CRED",
        ),
    ],
)
def test_absent_optional_source_makes_its_sensor_unavailable(description, data, local_name) -> None:
    entity = _sensor(
        description,
        _entry(),
        _coordinator(data=data),
        local_name=local_name,
        display_name="Optional value",
    )

    assert entity.available is False
    assert entity.native_value is None


@pytest.mark.parametrize(
    ("description", "data", "local_name"),
    [
        (
            _source_description(TAX_SENSOR_DESCRIPTIONS, "tax_gain_loss", "capital_gains"),
            {"capital_gains": {"tax_gain_loss": None}},
            "",
        ),
        (
            _source_description(
                TAX_SENSOR_DESCRIPTIONS,
                "unrealised_tax_gain_loss",
                "unrealised_cgt",
            ),
            {"unrealised_cgt": {"unrealised_tax_gain_loss": None}},
            "",
        ),
        (
            _source_description(TAX_SENSOR_DESCRIPTIONS, "claimable_loss", "cgt_analytics"),
            {
                "capital_gains": {"claimable_loss": None},
                "cgt_analytics": {"claimable_loss": None},
            },
            "",
        ),
        (
            _source_description(
                TAX_SENSOR_DESCRIPTIONS,
                "largest_loss_amount",
                "cgt_analytics",
            ),
            {
                "unrealised_cgt": {"losses": []},
                "cgt_analytics": {"largest_loss_amount": None},
            },
            "",
        ),
        (
            _source_description(
                ALL_HOLDING_DESCRIPTIONS,
                "holding_fundamental",
                "pe_ratio",
            ),
            {
                "holdings": {"holdings": [{"instrument": {"code": "CRED"}}]},
                "user_instruments": {
                    "instruments": [{"code": "CRED", "market_code": "ASX", "pe_ratio": None}]
                },
                "instrument_lookup": {"CRED": {"pe_ratio": None}},
            },
            "CRED",
        ),
        (
            _source_description(ALL_HOLDING_DESCRIPTIONS, "holding_income", "ttm_income"),
            {
                "holdings": {"holdings": [{"instrument": {"code": "CRED"}}]},
                "payouts": {"payouts": []},
                "holding_income": {},
            },
            "CRED",
        ),
        (
            _source_description(ALL_HOLDING_DESCRIPTIONS, "holding_trade", "brokerage_paid"),
            {
                "holdings": {"holdings": [{"instrument": {"code": "CRED"}}]},
                "trades": {"trades": []},
                "holding_trades": {},
            },
            "CRED",
        ),
    ],
)
def test_present_optional_source_with_null_field_stays_available_unknown(
    description, data, local_name
) -> None:
    entity = _sensor(
        description,
        _entry(),
        _coordinator(data=data),
        local_name=local_name,
        display_name="Optional value",
    )

    assert entity.available is True
    assert entity.native_value is None


@pytest.mark.parametrize(
    ("description", "data", "local_name", "expected"),
    [
        (
            _source_description(TAX_SENSOR_DESCRIPTIONS, "tax_gain_loss", "capital_gains"),
            {"capital_gains": {"tax_gain_loss": -1287.23}},
            "",
            -1287.23,
        ),
        (
            _source_description(
                TAX_SENSOR_DESCRIPTIONS,
                "largest_loss_amount",
                "cgt_analytics",
            ),
            {
                "unrealised_cgt": {"losses": []},
                "cgt_analytics": {"largest_loss_amount": 134.75},
            },
            "",
            134.75,
        ),
        (
            _source_description(ALL_HOLDING_DESCRIPTIONS, "holding_fundamental", "eps"),
            {
                "holdings": {"holdings": [{"instrument": {"code": "CRED"}}]},
                "user_instruments": {"instruments": []},
                "instrument_lookup": {"CRED": {"eps": 0.0}},
            },
            "CRED",
            0.0,
        ),
        (
            _source_description(ALL_HOLDING_DESCRIPTIONS, "holding_income", "ttm_income"),
            {
                "holdings": {"holdings": [{"instrument": {"code": "CRED"}}]},
                "payouts": {"payouts": []},
                "holding_income": {"CRED": {"ttm_income": 357.96}},
            },
            "CRED",
            357.96,
        ),
        (
            _source_description(ALL_HOLDING_DESCRIPTIONS, "holding_trade", "brokerage_paid"),
            {
                "holdings": {"holdings": [{"instrument": {"code": "CRED"}}]},
                "trades": {"trades": []},
                "holding_trades": {"CRED": {"brokerage": 55.94}},
            },
            "CRED",
            55.94,
        ),
    ],
)
def test_optional_source_values_reach_their_entities(
    description, data, local_name, expected
) -> None:
    entity = _sensor(
        description,
        _entry(),
        _coordinator(data=data),
        local_name=local_name,
        display_name="Optional value",
    )

    assert entity.available is True
    assert entity.native_value == expected


def test_holding_currency_does_not_require_user_instruments() -> None:
    description = _source_description(
        ALL_HOLDING_DESCRIPTIONS,
        "holding_fundamental",
        "currency_code",
    )
    entity = _sensor(
        description,
        _entry(),
        _coordinator(
            data={
                "holdings": {
                    "holdings": [
                        {
                            "instrument": {"code": "CRED"},
                            "instrument_currency": {"code": "AUD"},
                        }
                    ]
                }
            }
        ),
        local_name="CRED",
        display_name="CRED currency",
    )

    assert entity.available is True
    assert entity.native_value == "AUD"


def test_non_ascii_entity_name_uses_home_assistant_slugification() -> None:
    sensor = _sensor(
        _description(SENSOR_DESCRIPTIONS, "value"),
        _entry(),
        _coordinator(),
        display_name="Crème brûlée 📈",
    )

    assert sensor.entity_id == "sensor.creme_brulee_123"
    assert re.fullmatch(r"sensor\.[a-z0-9_]+", sensor.entity_id)


def test_emoji_only_entity_name_uses_home_assistant_safe_placeholder() -> None:
    sensor = _sensor(
        _description(SENSOR_DESCRIPTIONS, "value"),
        _entry(),
        _coordinator(),
        display_name="📈",
    )

    assert sensor.entity_id == "sensor.unknown_123"


def test_ex_date_without_pay_date_emits_only_an_ex_dividend_event() -> None:
    payout = {
        "id": 1,
        "symbol": "AAA",
        "goes_ex_on": "2026-09-01",
        "amount": 10,
        "exchange_rate": 1,
    }
    calendar = SharesightDividendCalendar(
        _coordinator(
            data={
                "income_report": {
                    "upcoming_payouts_available": True,
                    "upcoming_payouts": [payout],
                }
            }
        ),
        PORTFOLIO_ID,
        False,
    )

    events = calendar._build_events()

    assert len(events) == 1
    assert events[0].start == date(2026, 9, 1)
    assert events[0].summary.startswith("AAA ex-dividend")
    assert events[0].uid == "123_exdiv_1_2026-09-01"


def test_pay_and_ex_dates_emit_two_distinct_deduplicated_events() -> None:
    payout = {
        "id": 2,
        "symbol": "BBB",
        "company_name": "Example Limited",
        "paid_on": "2026-09-10",
        "goes_ex_on": "2026-09-02",
        "amount": 5,
        "exchange_rate": 1,
    }
    calendar = SharesightDividendCalendar(
        _coordinator(
            data={
                "income_report": {
                    "payouts_available": True,
                    "upcoming_payouts_available": True,
                    "payouts": [payout],
                    "upcoming_payouts": [dict(payout)],
                }
            }
        ),
        PORTFOLIO_ID,
        False,
    )

    events = calendar._build_events()

    assert [(event.start, event.summary.split()[1]) for event in events] == [
        (date(2026, 9, 2), "ex-dividend"),
        (date(2026, 9, 10), "dividend"),
    ]


def test_calendar_uses_live_currency_and_coordinator_current_date() -> None:
    coordinator = _coordinator(
        data={
            "income_report": {
                "payouts_available": True,
                "payouts": [
                    {
                        "id": 3,
                        "symbol": "CCC",
                        "paid_on": "2026-08-28",
                        "amount": 12.5,
                        "exchange_rate": 1,
                    }
                ],
            }
        },
        currency="AUD",
    )
    calendar = SharesightDividendCalendar(coordinator, PORTFOLIO_ID, False)

    assert calendar.event is not None
    assert calendar.event.summary.endswith("12.50 AUD")
    coordinator.portfolio_currency = "NZD"
    assert calendar.event.summary.endswith("12.50 NZD")
    coordinator.current_date = date(2026, 8, 29)
    assert calendar.event is None


def test_portfolio_today_uses_the_portfolio_timezone(monkeypatch) -> None:
    """Calendar boundaries follow Sharesight's timezone, not the host's date."""
    coordinator = object.__new__(coordinator_module.SharesightCoordinator)
    coordinator._portfolio_detail = {"tz_name": "Pacific/Auckland"}
    coordinator.portfolio_id = PORTFOLIO_ID
    monkeypatch.setattr(
        coordinator_module.dt_util,
        "utcnow",
        lambda: datetime(2026, 8, 27, 13, 30, tzinfo=UTC),
    )

    assert coordinator._portfolio_today() == date(2026, 8, 28)


@pytest.mark.parametrize(
    ("account_type", "expected_unique_id"),
    [("standard", "123_value_v2"), ("developer", "developer_123_value_v2")],
)
def test_statistics_backfill_resolves_account_scoped_value_sensor(
    monkeypatch, account_type, expected_unique_id
) -> None:
    """Standard and sandbox portfolios with the same id must not share history."""

    class FakeRegistry:
        def async_get_entity_id(self, domain, platform, unique_id):
            assert (domain, platform, unique_id) == (
                "sensor",
                "sharesight",
                expected_unique_id,
            )
            return "sensor.portfolio_value"

    monkeypatch.setattr(
        statistics_import.er,
        "async_get",
        lambda _hass: FakeRegistry(),
    )
    entry = SimpleNamespace(data={"account_type": account_type})

    assert (
        statistics_import._value_sensor_entity_id(object(), entry, PORTFOLIO_ID)
        == "sensor.portfolio_value"
    )
