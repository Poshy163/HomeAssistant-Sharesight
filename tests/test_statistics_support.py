"""Regression tests for Home Assistant long-term-statistics support."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import statistics as recorder_statistics
from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, PERCENTAGE
import pytest

from custom_components.sharesight import analytics, enum, recorder, statistics_import
from custom_components.sharesight.sensor import SharesightSensor


def test_custom_equivalent_units_are_exact_and_entity_scoped(monkeypatch) -> None:
    """Only known safe unit-label migrations are declared equivalent."""
    entries = [
        SimpleNamespace(
            entity_id="sensor.net_instrument_price_123",
            translation_key="holding_instrument_price",
        ),
        SimpleNamespace(
            entity_id="sensor.rpi_vwap_buy_price_123",
            translation_key="holding_vwap_buy_price",
        ),
        SimpleNamespace(
            entity_id="sensor.effective_number_of_holdings_123",
            translation_key="effective_number_of_holdings",
        ),
        SimpleNamespace(
            entity_id="sensor.stale_price_count_123",
            translation_key="stale_price_count",
        ),
        # Already in the portfolio currency: there is no migration to declare.
        SimpleNamespace(
            entity_id="sensor.cred_instrument_price_123",
            translation_key="holding_instrument_price",
        ),
        # A currency-valued sensor outside the audited holding recipes must not
        # make different currencies globally equivalent.
        SimpleNamespace(
            entity_id="sensor.net_value_123",
            translation_key="holding_value",
        ),
        # An unrelated unitless sensor must not become an invalid
        # ``{None: None}`` equivalent-unit declaration.
        SimpleNamespace(
            entity_id="sensor.portfolio_id_123",
            translation_key="portfolio_id",
        ),
        SimpleNamespace(
            entity_id="binary_sensor.sharesight_api_degraded_123",
            translation_key="holding_instrument_price",
        ),
        # Missing entities are ignored defensively.
        SimpleNamespace(
            entity_id="sensor.missing_instrument_price_123",
            translation_key="holding_instrument_price",
        ),
    ]
    states = {
        "sensor.net_instrument_price_123": SimpleNamespace(
            attributes={ATTR_UNIT_OF_MEASUREMENT: "USD"}
        ),
        "sensor.rpi_vwap_buy_price_123": SimpleNamespace(
            attributes={ATTR_UNIT_OF_MEASUREMENT: "GBP"}
        ),
        "sensor.effective_number_of_holdings_123": SimpleNamespace(
            attributes={ATTR_UNIT_OF_MEASUREMENT: "holdings"}
        ),
        "sensor.stale_price_count_123": SimpleNamespace(
            attributes={ATTR_UNIT_OF_MEASUREMENT: "instruments"}
        ),
        "sensor.cred_instrument_price_123": SimpleNamespace(
            attributes={ATTR_UNIT_OF_MEASUREMENT: "AUD"}
        ),
        "sensor.net_value_123": SimpleNamespace(attributes={ATTR_UNIT_OF_MEASUREMENT: "USD"}),
        "sensor.portfolio_id_123": SimpleNamespace(attributes={ATTR_UNIT_OF_MEASUREMENT: None}),
        "binary_sensor.sharesight_api_degraded_123": SimpleNamespace(
            attributes={ATTR_UNIT_OF_MEASUREMENT: "USD"}
        ),
    }
    config_entry = SimpleNamespace(
        entry_id="entry",
        runtime_data=SimpleNamespace(coordinator=SimpleNamespace(portfolio_currency="AUD")),
    )
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda _domain: [config_entry]),
        states=SimpleNamespace(get=states.get),
    )

    monkeypatch.setattr(recorder.er, "async_get", lambda _hass: object())
    monkeypatch.setattr(
        recorder.er,
        "async_entries_for_config_entry",
        lambda _registry, _entry_id: entries,
    )

    assert recorder.async_custom_equivalent_units(hass) == {
        "sensor.net_instrument_price_123": {"AUD": "USD", "USD": "USD"},
        "sensor.rpi_vwap_buy_price_123": {"AUD": "GBP", "GBP": "GBP"},
        "sensor.effective_number_of_holdings_123": {
            None: "holdings",
            "holdings": "holdings",
        },
        "sensor.stale_price_count_123": {
            None: "instruments",
            "instruments": "instruments",
        },
    }


def test_custom_equivalent_units_tolerate_entry_without_currency(monkeypatch) -> None:
    """One cold/degraded entry cannot disable recorder migrations globally."""

    class MissingCurrencyCoordinator:
        @property
        def portfolio_currency(self):
            raise ValueError("missing")

    config_entry = SimpleNamespace(
        entry_id="entry",
        runtime_data=SimpleNamespace(coordinator=MissingCurrencyCoordinator()),
    )
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda _domain: [config_entry]),
        states=SimpleNamespace(get=lambda _entity_id: None),
    )
    monkeypatch.setattr(recorder.er, "async_get", lambda _hass: object())
    monkeypatch.setattr(
        recorder.er,
        "async_entries_for_config_entry",
        lambda _registry, _entry_id: [],
    )

    assert recorder.async_custom_equivalent_units(hass) == {}


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), -float("inf")])
def test_statistics_sensor_rejects_non_finite_or_boolean_values(value) -> None:
    """Recorder-eligible sensors never publish invalid numeric states."""
    sensor = object.__new__(SharesightSensor)
    sensor._state_class = SensorStateClass.MEASUREMENT
    sensor._device_class = None
    sensor._raw_native_value = lambda: value

    assert sensor.native_value is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("12.5", 12.5), ("0", 0.0), ("-7.25", -7.25)],
)
def test_statistics_sensor_normalises_numeric_strings(value, expected) -> None:
    """API numeric strings become native numbers before recorder sees them."""
    sensor = object.__new__(SharesightSensor)
    sensor._state_class = SensorStateClass.MEASUREMENT
    sensor._device_class = None
    sensor._raw_native_value = lambda: value

    assert sensor.native_value == expected
    assert isinstance(sensor.native_value, float)


def test_cgt_concession_rate_is_a_percentage_measurement() -> None:
    """Sharesight's 0.5 ratio is published as Home Assistant's 50 percent."""
    description = next(
        item
        for item in enum.TAX_SENSOR_DESCRIPTIONS
        if item.translation_key == "cgt_concession_rate"
    )
    assert description.native_unit_of_measurement == PERCENTAGE
    assert description.state_class == SensorStateClass.MEASUREMENT
    assert description.suggested_display_precision == 2
    assert (
        analytics.build_cgt_analytics({"cgt_concession_rate": 0.5}, None)["cgt_concession_rate"]
        == 50.0
    )


def test_trailing_franking_is_a_measurement() -> None:
    """A moving 365-day window must not be accumulated as a TOTAL sum."""
    description = next(
        item
        for item in enum.HOLDING_INCOME_DESCRIPTIONS
        if item.translation_key == "holding_franking_ttm"
    )

    assert description.state_class == SensorStateClass.MEASUREMENT


def test_statistics_metadata_migrations_preserve_statistic_ids(monkeypatch) -> None:
    """Audited metadata changes preserve IDs and do not clear numeric rows."""
    entity_id = "sensor.cred_franking_ttm_123"
    incompatible_id = "sensor.net_franking_ttm_123"
    holdings_count_id = "sensor.effective_number_of_holdings_123"
    stale_count_id = "sensor.stale_price_count_123"
    entries = [
        SimpleNamespace(
            entity_id=entity_id,
            translation_key="holding_franking_ttm",
        ),
        SimpleNamespace(
            entity_id=incompatible_id,
            translation_key="holding_franking_ttm",
        ),
        SimpleNamespace(
            entity_id=holdings_count_id,
            translation_key="effective_number_of_holdings",
        ),
        SimpleNamespace(
            entity_id=stale_count_id,
            translation_key="stale_price_count",
        ),
    ]
    states = {
        entity_id: SimpleNamespace(
            attributes={
                "state_class": SensorStateClass.MEASUREMENT,
                ATTR_UNIT_OF_MEASUREMENT: "AUD",
            }
        ),
        incompatible_id: SimpleNamespace(
            attributes={
                "state_class": SensorStateClass.MEASUREMENT,
                ATTR_UNIT_OF_MEASUREMENT: "AUD",
            }
        ),
        holdings_count_id: SimpleNamespace(
            attributes={
                "state_class": SensorStateClass.MEASUREMENT,
                ATTR_UNIT_OF_MEASUREMENT: "holdings",
            }
        ),
        stale_count_id: SimpleNamespace(
            attributes={
                "state_class": SensorStateClass.MEASUREMENT,
                ATTR_UNIT_OF_MEASUREMENT: "instruments",
            }
        ),
    }
    imported: list[tuple[object, dict, tuple]] = []
    hass = SimpleNamespace(
        config=SimpleNamespace(components={"recorder"}),
        states=SimpleNamespace(get=states.get),
    )
    entry = SimpleNamespace(entry_id="entry")
    monkeypatch.setattr(statistics_import.er, "async_get", lambda _hass: object())
    monkeypatch.setattr(
        statistics_import.er,
        "async_entries_for_config_entry",
        lambda _registry, _entry_id: entries,
    )
    monkeypatch.setattr(
        recorder_statistics,
        "async_import_statistics",
        lambda target_hass, metadata, statistics: imported.append(
            (target_hass, metadata, statistics)
        ),
    )
    monkeypatch.setattr(
        recorder_statistics,
        "async_list_statistic_ids",
        lambda _hass, _statistic_ids: asyncio.sleep(
            0,
            result=[
                {
                    "statistic_id": entity_id,
                    "mean_type": recorder_statistics.StatisticMeanType.NONE,
                    "has_sum": True,
                    "source": "recorder",
                    "statistics_unit_of_measurement": "AUD",
                    "unit_class": None,
                },
                {
                    "statistic_id": incompatible_id,
                    "mean_type": recorder_statistics.StatisticMeanType.NONE,
                    "has_sum": True,
                    "source": "recorder",
                    "statistics_unit_of_measurement": "NZD",
                    "unit_class": None,
                },
                {
                    "statistic_id": holdings_count_id,
                    "mean_type": recorder_statistics.StatisticMeanType.ARITHMETIC,
                    "has_sum": False,
                    "source": "recorder",
                    "statistics_unit_of_measurement": None,
                    "unit_class": "unitless",
                },
                {
                    "statistic_id": stale_count_id,
                    "mean_type": recorder_statistics.StatisticMeanType.ARITHMETIC,
                    "has_sum": False,
                    "source": "recorder",
                    "statistics_unit_of_measurement": None,
                    "unit_class": None,
                },
            ],
        ),
    )

    asyncio.run(statistics_import.async_migrate_statistics_metadata(hass, entry))

    assert imported == [
        (
            hass,
            {
                "has_sum": False,
                "mean_type": recorder_statistics.StatisticMeanType.ARITHMETIC,
                "name": None,
                "source": "recorder",
                "statistic_id": entity_id,
                "unit_class": None,
                "unit_of_measurement": "AUD",
            },
            (),
        ),
        (
            hass,
            {
                "has_sum": False,
                "mean_type": recorder_statistics.StatisticMeanType.ARITHMETIC,
                "name": None,
                "source": "recorder",
                "statistic_id": holdings_count_id,
                "unit_class": None,
                "unit_of_measurement": "holdings",
            },
            (),
        ),
        (
            hass,
            {
                "has_sum": False,
                "mean_type": recorder_statistics.StatisticMeanType.ARITHMETIC,
                "name": None,
                "source": "recorder",
                "statistic_id": stale_count_id,
                "unit_class": None,
                "unit_of_measurement": "instruments",
            },
            (),
        ),
    ]


def test_cgt_totals_reset_at_the_source_period_in_portfolio_timezone() -> None:
    """FY totals expose a stable report boundary instead of HA-local midnight."""
    expected = datetime(2026, 7, 1, tzinfo=ZoneInfo("Australia/Sydney"))
    coordinator = SimpleNamespace(
        data={"capital_gains": {"start_date": "2026-07-01"}},
        portfolio_start_of_day=lambda day: datetime(
            day.year,
            day.month,
            day.day,
            tzinfo=ZoneInfo("Australia/Sydney"),
        ),
    )
    sensor = object.__new__(SharesightSensor)
    sensor._state_class = SensorStateClass.TOTAL
    sensor._sub_key = "cgt_analytics"
    sensor._key = "claimable_loss"
    sensor._coordinator = coordinator

    assert sensor.last_reset == expected

    # A lifetime total and a missing/malformed report must never fabricate a
    # reset cycle.
    sensor._key = "unrelated_total"
    assert sensor.last_reset is None
    sensor._key = "claimable_loss"
    coordinator.data = {"capital_gains": {"start_date": "invalid"}}
    assert sensor.last_reset is None


def test_statistics_backfill_excludes_local_today_and_deduplicates_hours(
    monkeypatch,
) -> None:
    """Backfill neither overlaps live local-day stats nor imports duplicate hours."""
    local_tz = ZoneInfo("Australia/Adelaide")
    imported: dict[str, object] = {}

    async def async_get_value_history():
        return {
            "data": [
                # This older point already exists in recorder and must not be
                # overwritten by a single daily closing value.
                {"date": "2026-08-25", "value": 55},
                {"date": "2026-08-26", "value": 101},
                # Same source hour: the later value deterministically wins.
                {"date": "2026-08-26", "value": 100},
                # At UTC this is still 2026-08-26, so this catches accidental
                # UTC-date comparisons that import today's local value.
                {"date": "2026-08-27", "value": 999},
            ]
        }

    def fake_import_statistics(hass, metadata, statistics) -> None:
        imported.update(
            hass=hass,
            metadata=metadata,
            statistics=statistics,
        )

    async def async_add_executor_job(target, *args):
        return target(*args)

    monkeypatch.setattr(
        statistics_import,
        "_value_sensor_entity_id",
        lambda _hass, _entry, _portfolio_id: "sensor.portfolio_value_123",
    )
    monkeypatch.setattr(
        recorder_statistics,
        "async_import_statistics",
        fake_import_statistics,
    )
    monkeypatch.setattr(
        recorder_statistics,
        "async_list_statistic_ids",
        lambda _hass, _statistic_ids: asyncio.sleep(
            0,
            result=[
                {
                    "statistic_id": "sensor.portfolio_value_123",
                    "has_mean": True,
                    "mean_type": recorder_statistics.StatisticMeanType.ARITHMETIC,
                    "has_sum": False,
                    "source": "recorder",
                    "statistics_unit_of_measurement": "AUD",
                    "unit_class": None,
                }
            ],
        ),
    )
    monkeypatch.setattr(
        recorder_statistics,
        "statistics_during_period",
        lambda *_args: {
            "sensor.portfolio_value_123": [
                {
                    "start": datetime(2026, 8, 24, 15, tzinfo=UTC).timestamp(),
                    "mean": 54.5,
                }
            ]
        },
    )

    hass = SimpleNamespace(
        config=SimpleNamespace(components={"recorder"}),
        states=SimpleNamespace(
            get=lambda entity_id: (
                SimpleNamespace(attributes={ATTR_UNIT_OF_MEASUREMENT: "AUD"})
                if entity_id == "sensor.portfolio_value_123"
                else None
            )
        ),
        async_add_executor_job=async_add_executor_job,
    )
    entry = SimpleNamespace(data={"portfolio_id": "123"})
    coordinator = SimpleNamespace(
        portfolio_currency="AUD",
        current_date=datetime(2026, 8, 27, 12, tzinfo=local_tz).date(),
        portfolio_start_of_day=lambda day: datetime.combine(
            day,
            time.min,
            tzinfo=local_tz,
        ),
        async_get_value_history=async_get_value_history,
    )

    asyncio.run(statistics_import.async_backfill_value_statistics(hass, entry, coordinator))

    assert imported["hass"] is hass
    assert imported["metadata"] == {
        "has_sum": False,
        "name": None,
        "source": "recorder",
        "statistic_id": "sensor.portfolio_value_123",
        "unit_of_measurement": "AUD",
        "unit_class": None,
        "mean_type": recorder_statistics.StatisticMeanType.ARITHMETIC,
    }
    statistics = imported["statistics"]
    assert len(statistics) == 1
    assert statistics[0]["start"].tzinfo is UTC
    # Adelaide midnight is 14:30 UTC in August. Recorder requires a UTC hour,
    # so the point uses 15:00 UTC / 00:30 Adelaide, still on the source day.
    assert statistics[0]["start"] == datetime(2026, 8, 25, 15, tzinfo=UTC)
    assert statistics[0]["mean"] == 100.0
    assert statistics[0]["min"] == 100.0
    assert statistics[0]["max"] == 100.0


@pytest.mark.parametrize(
    ("timezone_name", "expected"),
    [
        ("Australia/Adelaide", datetime(2026, 8, 26, 15, tzinfo=UTC)),
        ("America/New_York", datetime(2026, 8, 27, 4, tzinfo=UTC)),
    ],
)
def test_date_only_points_use_portfolio_timezone(timezone_name, expected) -> None:
    """UTC-positive and UTC-negative portfolios retain their source day."""
    portfolio_tz = ZoneInfo(timezone_name)
    points = statistics_import._extract_points(
        {"data": [{"date": "2026-08-27", "value": 10}]},
        lambda day: datetime.combine(day, time.min, tzinfo=portfolio_tz),
    )

    assert points == [(expected, 10.0)]
    assert points[0][0].astimezone(portfolio_tz).date().isoformat() == "2026-08-27"


def test_backfill_refuses_to_relabel_existing_currency_metadata(monkeypatch) -> None:
    """A portfolio currency change leaves incompatible history untouched."""
    statistic_id = "sensor.portfolio_value_123"
    imported: list[object] = []

    monkeypatch.setattr(
        statistics_import,
        "_value_sensor_entity_id",
        lambda _hass, _entry, _portfolio_id: statistic_id,
    )
    monkeypatch.setattr(
        recorder_statistics,
        "async_list_statistic_ids",
        lambda _hass, _statistic_ids: asyncio.sleep(
            0,
            result=[
                {
                    "statistic_id": statistic_id,
                    "has_mean": True,
                    "mean_type": recorder_statistics.StatisticMeanType.ARITHMETIC,
                    "has_sum": False,
                    "source": "recorder",
                    "statistics_unit_of_measurement": "NZD",
                    "unit_class": None,
                }
            ],
        ),
    )
    monkeypatch.setattr(
        recorder_statistics,
        "async_import_statistics",
        lambda *_args: imported.append(object()),
    )

    async def unexpected_history_fetch():
        raise AssertionError("metadata must be checked before fetching history")

    hass = SimpleNamespace(
        config=SimpleNamespace(components={"recorder"}),
        states=SimpleNamespace(
            get=lambda _entity_id: SimpleNamespace(attributes={ATTR_UNIT_OF_MEASUREMENT: "AUD"})
        ),
    )
    entry = SimpleNamespace(data={"portfolio_id": "123"})
    coordinator = SimpleNamespace(
        portfolio_currency="AUD",
        async_get_value_history=unexpected_history_fetch,
    )

    asyncio.run(statistics_import.async_backfill_value_statistics(hass, entry, coordinator))

    assert imported == []


def test_backfill_with_only_today_does_not_query_or_import(monkeypatch) -> None:
    """A new portfolio with no historical days exits cleanly."""
    statistic_id = "sensor.portfolio_value_123"
    portfolio_tz = ZoneInfo("Australia/Adelaide")
    side_effects: list[str] = []

    monkeypatch.setattr(
        statistics_import,
        "_value_sensor_entity_id",
        lambda _hass, _entry, _portfolio_id: statistic_id,
    )
    monkeypatch.setattr(
        recorder_statistics,
        "async_list_statistic_ids",
        lambda _hass, _statistic_ids: asyncio.sleep(
            0,
            result=[
                {
                    "statistic_id": statistic_id,
                    "has_mean": True,
                    "mean_type": recorder_statistics.StatisticMeanType.ARITHMETIC,
                    "has_sum": False,
                    "source": "recorder",
                    "statistics_unit_of_measurement": "AUD",
                    "unit_class": None,
                }
            ],
        ),
    )
    monkeypatch.setattr(
        recorder_statistics,
        "async_import_statistics",
        lambda *_args: side_effects.append("import"),
    )

    async def async_get_value_history():
        return {"data": [{"date": "2026-08-27", "value": 100}]}

    async def unexpected_executor(*_args):
        side_effects.append("query")
        return {}

    hass = SimpleNamespace(
        config=SimpleNamespace(components={"recorder"}),
        states=SimpleNamespace(
            get=lambda _entity_id: SimpleNamespace(attributes={ATTR_UNIT_OF_MEASUREMENT: "AUD"})
        ),
        async_add_executor_job=unexpected_executor,
    )
    coordinator = SimpleNamespace(
        portfolio_currency="AUD",
        current_date=datetime(2026, 8, 27, tzinfo=portfolio_tz).date(),
        portfolio_start_of_day=lambda day: datetime.combine(
            day,
            time.min,
            tzinfo=portfolio_tz,
        ),
        async_get_value_history=async_get_value_history,
    )

    asyncio.run(
        statistics_import.async_backfill_value_statistics(
            hass,
            SimpleNamespace(data={"portfolio_id": "123"}),
            coordinator,
        )
    )

    assert side_effects == []
