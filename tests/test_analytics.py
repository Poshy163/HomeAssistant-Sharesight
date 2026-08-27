"""Tests for the pure aggregation helpers in ``analytics.py``.

No Home Assistant dependency, so these run on any platform.  Expected values
are hand-computed from ``tests/fixtures.py`` and written out in the test so a
future change that shifts a number has to justify itself.
"""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.sharesight import analytics

from . import fixtures as F

TODAY = date(2026, 8, 27)


@pytest.fixture(name="data")
def data_fixture() -> dict:
    return F.coordinator_data()


@pytest.fixture(name="holdings")
def holdings_fixture(data) -> list[dict]:
    return data["holdings"]["holdings"]


@pytest.fixture(name="income")
def income_fixture(data, holdings) -> dict:
    return analytics.build_holding_income(data["payouts"]["payouts"], holdings, TODAY)


# --------------------------------------------------------------------------
# Currency handling
# --------------------------------------------------------------------------


def test_to_portfolio_currency_divides_by_the_rate() -> None:
    """Sharesight's rate is native-per-portfolio, so converting is a divide."""
    payout = {"amount": 0.65, "currency": "USD", "exchange_rate": 0.6633}
    assert analytics.to_portfolio_currency(payout, payout["amount"]) == pytest.approx(
        0.9799, abs=1e-4
    )


@pytest.mark.parametrize("rate", [None, 0, 0.0, -1, "abc"])
def test_to_portfolio_currency_falls_back_on_a_bad_rate(rate) -> None:
    """A missing or nonsensical rate must not produce inf/NaN."""
    assert analytics.to_portfolio_currency({"exchange_rate": rate}, 12.5) == 12.5


def test_to_portfolio_currency_returns_none_for_a_non_number() -> None:
    assert analytics.to_portfolio_currency({"exchange_rate": 1.0}, None) is None
    assert analytics.to_portfolio_currency({"exchange_rate": 1.0}, "n/a") is None


def test_monetary_amount_details_converts_and_labels_foreign_amount() -> None:
    details = analytics.monetary_amount_details(
        {"currency": "USD", "exchange_rate": 0.5},
        25,
        "AUD",
    )
    assert details == {
        "amount": 50.0,
        "currency": "AUD",
        "native_amount": 25.0,
        "native_currency": "USD",
        "exchange_rate": 0.5,
    }


def test_monetary_amount_details_fails_closed_without_foreign_rate() -> None:
    details = analytics.monetary_amount_details(
        {"currency": {"code": "USD"}},
        25,
        "AUD",
    )
    assert details["amount"] is None
    assert details["currency"] == "AUD"
    assert details["native_amount"] == 25.0
    assert details["native_currency"] == "USD"


def test_monetary_amount_details_keeps_same_currency_amount_raw() -> None:
    details = analytics.monetary_amount_details(
        {"currency_code": "AUD", "exchange_rate": 0.5},
        25,
        "AUD",
    )
    assert details["amount"] == 25.0


def test_holding_currency_prefers_instrument_currency(holdings) -> None:
    by_code = {analytics.holding_symbol(h): h for h in holdings}
    assert analytics.holding_currency(by_code["AAA"]) == "AUD"
    assert analytics.holding_currency(by_code["GLB"]) == "USD"
    assert analytics.holding_currency({}) is None


def test_payout_ex_date_reads_the_real_field() -> None:
    """The live V2 field is goes_ex_on; ex_date only exists in derived shapes."""
    assert analytics.payout_ex_date({"goes_ex_on": "2026-09-01"}) == "2026-09-01"
    assert analytics.payout_ex_date({"ex_date": "2026-09-02"}) == "2026-09-02"
    assert analytics.payout_ex_date({"paid_on": "2026-09-03"}) is None


# --------------------------------------------------------------------------
# Sold-out holdings
# --------------------------------------------------------------------------


def test_dust_holding_is_not_an_open_position() -> None:
    dust = next(h for h in F.HOLDINGS if h["instrument"]["code"] == "ZERO")
    assert analytics.is_open_position(dust) is False


def test_short_position_is_still_open() -> None:
    """Negative quantity with real value is a short, not dust."""
    assert analytics.is_open_position({"quantity": -100.0, "value": -2500.0}) is True


def test_holding_with_no_quantity_field_is_kept() -> None:
    assert analytics.is_open_position({"value": 10.0}) is True


# --------------------------------------------------------------------------
# Per-holding income
# --------------------------------------------------------------------------


def test_holding_income_totals(income) -> None:
    assert income["AAA"]["ttm_income"] == 300.0  # 100 + 200, both inside TTM
    assert income["AAA"]["franking_ttm"] == 90.0  # 30 + 60
    assert income["AAA"]["count"] == 2
    assert income["AAA"]["last_dividend_date"] == "2026-03-18"
    assert income["AAA"]["last_dividend_amount"] == 200.0
    # 300 income on a 9000 cost base.
    assert income["AAA"]["yield_on_cost"] == pytest.approx(3.33, abs=0.01)


def test_holding_income_marks_whether_the_symbol_is_still_held(income) -> None:
    assert income["AAA"]["held"] is True


def test_holding_income_converts_foreign_payouts(holdings) -> None:
    """A USD payout must be converted before it joins an AUD total."""
    payout = dict(F.PAYOUTS[3])  # GLB, 25.00
    payout["exchange_rate"] = 0.5
    result = analytics.build_holding_income([payout], holdings, TODAY)
    assert result["GLB"]["ttm_income"] == 50.0


def test_yield_on_cost_is_suppressed_when_implausible(holdings) -> None:
    """A collapsed cost base used to produce figures like 8579%."""
    residual = dict(
        next(h for h in holdings if h["instrument"]["code"] == "AAA"),
        value=10.0,
        capital_gain=8.0,
    )
    payout = dict(F.PAYOUTS[0], amount=500.0)
    result = analytics.build_holding_income([payout], [residual], TODAY)
    assert result["AAA"]["ttm_income"] == 500.0
    assert result["AAA"]["yield_on_cost"] is None


def test_payouts_outside_the_trailing_year_still_count_for_last_dividend(
    holdings,
) -> None:
    old = dict(F.PAYOUTS[0], paid_on="2020-01-01", goes_ex_on="2019-12-15")
    result = analytics.build_holding_income([old], holdings, TODAY)
    assert result["AAA"]["ttm_income"] == 0.0
    assert result["AAA"]["last_dividend_date"] == "2020-01-01"


# --------------------------------------------------------------------------
# Per-holding trades
# --------------------------------------------------------------------------


def test_holding_trades_brokerage_and_shares(data, holdings) -> None:
    result = analytics.build_holding_trades(data["trades"]["trades"], holdings)
    assert result["AAA"]["count"] == 3  # BUY + SELL + SPLIT
    assert result["AAA"]["brokerage"] == 20.0  # 10 on the buy, 10 on the sell
    assert result["AAA"]["last_date"] == "2026-08-06"
    # 900 bought, 250 added by the split, 150 sold.
    assert result["AAA"]["net_shares"] == 1000.0


def test_holding_trades_convert_foreign_brokerage(holdings) -> None:
    """Brokerage is charged in the instrument's currency, not the portfolio's."""
    trade = dict(F.TRADES[2], exchange_rate=0.5, brokerage=10.0)  # GLB, USD
    result = analytics.build_holding_trades([trade], holdings)
    assert result["GLB"]["brokerage"] == 20.0


@pytest.mark.parametrize(
    ("brokerage_currency", "expected"),
    [
        ("AUD", 10.0),
        ("USD", 20.0),
        ("EUR", None),
    ],
)
def test_brokerage_conversion_respects_its_declared_currency(brokerage_currency, expected) -> None:
    """Only an instrument-currency fee may use the trade's FX rate."""
    trade = {
        "brokerage_currency_code": brokerage_currency,
        "exchange_rate": 0.5,
        "instrument": {"currency_code": "USD"},
    }
    converted = analytics.brokerage_to_portfolio_currency(
        trade,
        10.0,
        portfolio_currency="AUD",
    )
    if expected is None:
        assert converted is None
    else:
        assert converted == expected


def test_holding_trade_total_omits_unconvertible_third_currency_brokerage(
    holdings,
) -> None:
    """A third-currency fee must not be silently labelled as portfolio money."""
    base = dict(F.TRADES[2], exchange_rate=0.5, brokerage=10.0)
    portfolio_fee = dict(base, id=901, brokerage_currency_code="AUD")
    instrument_fee = dict(base, id=902, brokerage_currency_code="USD")
    third_currency_fee = dict(base, id=903, brokerage_currency_code="EUR")

    result = analytics.build_holding_trades(
        [portfolio_fee, instrument_fee, third_currency_fee],
        holdings,
        portfolio_currency="AUD",
    )

    assert result["GLB"]["brokerage"] == 30.0


def test_split_rescales_the_average_buy_price(holdings) -> None:
    """A 900-share buy at $10 plus a 250-share split is $7.83 per share."""
    trades = [F.TRADES[0], F.TRADES[5]]
    result = analytics.build_holding_trades(trades, holdings)
    assert result["AAA"]["net_shares"] == 1150.0
    assert result["AAA"]["vwap_buy_price"] == pytest.approx(7.826, abs=0.001)


# --------------------------------------------------------------------------
# Allocation breakdowns
# --------------------------------------------------------------------------


def test_sector_allocation_reads_the_embedded_instrument(holdings) -> None:
    """No user_instruments feed supplied - the holding carries the sector."""
    result = analytics.build_sector_allocation(holdings, {}, axis="sector")
    buckets = {b["group_name"]: b["value"] for b in result["breakdown"]}
    assert buckets == {"Technology": 10000.0, "Finance": 10000.0, "Health Care": 2000.0}
    assert result["total"] == 22000.0


def test_investment_type_allocation(holdings) -> None:
    result = analytics.build_sector_allocation(holdings, {}, axis="instrument_type")
    buckets = {b["group_name"]: b["value"] for b in result["breakdown"]}
    assert buckets == {"Ordinary Share": 12000.0, "Exchange Traded Fund": 10000.0}


def test_currency_allocation(holdings) -> None:
    result = analytics.build_currency_allocation(holdings, "AUD")
    buckets = {b["group_name"]: b["percentage"] for b in result["breakdown"]}
    assert buckets == {"AUD": 72.73, "USD": 27.27}
    assert result["base_currency"] == "AUD"


def test_label_allocation_counts_a_holding_once_per_label(holdings) -> None:
    result = analytics.build_label_allocation(holdings)
    by_label = {entry["label"]: entry for entry in result}
    assert by_label["Core"]["value"] == 14000.0
    assert by_label["Core"]["holding_count"] == 2
    assert by_label["Growth"]["value"] == 4000.0
    # Labels are non-exclusive, so the percentages need not sum to 100.
    assert by_label["Core"]["percentage"] == pytest.approx(63.64, abs=0.01)


# --------------------------------------------------------------------------
# Portfolio analytics
# --------------------------------------------------------------------------


def test_portfolio_analytics_concentration(data, holdings, income) -> None:
    lookup = analytics.build_instrument_lookup(data["user_instruments"])
    result = analytics.build_portfolio_analytics(
        holdings, lookup, data["report"], TODAY, income, "AUD"
    )
    # (10/22)^2 + (4/22)^2 + (6/22)^2 + (2/22)^2
    assert result["hhi"] == pytest.approx(0.3223, abs=0.0001)
    assert result["effective_holdings"] == pytest.approx(3.10, abs=0.01)


def test_fx_exposure_uses_the_declared_base_currency(data, holdings, income) -> None:
    lookup = analytics.build_instrument_lookup(data["user_instruments"])
    result = analytics.build_portfolio_analytics(
        holdings, lookup, data["report"], TODAY, income, "AUD"
    )
    assert result["fx_exposure_percent"] == pytest.approx(27.27, abs=0.01)


def test_fx_exposure_works_without_the_instruments_feed(data, holdings, income) -> None:
    """It used to read a currency only the optional feed supplied."""
    result = analytics.build_portfolio_analytics(holdings, {}, data["report"], TODAY, income, "AUD")
    assert result["fx_exposure_percent"] == pytest.approx(27.27, abs=0.01)


def test_fx_exposure_is_none_when_no_currency_is_known(data) -> None:
    bare = [{"value": 100.0}, {"value": 50.0}]
    result = analytics.build_portfolio_analytics(bare, {}, data["report"], TODAY, {}, "AUD")
    assert result["fx_exposure_percent"] is None


def test_weighted_yield_counts_non_payers_as_zero(data, holdings, income) -> None:
    """STALE pays nothing, so it must drag the average down, not vanish."""
    lookup = analytics.build_instrument_lookup(data["user_instruments"])
    result = analytics.build_portfolio_analytics(
        holdings, lookup, data["report"], TODAY, income, "AUD"
    )
    # (300 + 50 + 25) / 22000 as a portfolio-wide figure.
    assert result["weighted_yield"] == pytest.approx(1.70, abs=0.01)
    assert result["yield_coverage_percent"] == 100.0


def test_weighted_pe_is_a_harmonic_mean(data, holdings, income) -> None:
    lookup = analytics.build_instrument_lookup(data["user_instruments"])
    result = analytics.build_portfolio_analytics(
        holdings, lookup, data["report"], TODAY, income, "AUD"
    )
    # 20000 of covered value / (10000/20 + 4000/10 + 6000/40) = 20000/1050
    assert result["weighted_pe"] == pytest.approx(19.05, abs=0.01)
    assert result["pe_coverage_percent"] == pytest.approx(90.91, abs=0.01)


def test_weighted_pe_is_suppressed_below_the_coverage_floor(data, income) -> None:
    """A P/E backed by a sliver of the portfolio is noise, not an average."""
    lookup = {"AAA": {"pe_ratio": 20.0}}
    holdings = [
        F.HOLDINGS[0],
        dict(F.HOLDINGS[1], value=1_000_000.0),
    ]
    result = analytics.build_portfolio_analytics(
        holdings, lookup, data["report"], TODAY, income, "AUD"
    )
    assert result["weighted_pe"] is None
    assert result["pe_coverage_percent"] < 50


def test_stale_price_count_is_none_without_price_timestamps(data, holdings) -> None:
    """Reporting a confident 0 when there is no price data at all was a lie."""
    result = analytics.build_portfolio_analytics(holdings, {}, data["report"], TODAY, {}, "AUD")
    assert result["stale_price_count"] is None
    assert result["price_timestamp_coverage_percent"] == 0.0


def test_stale_price_count_when_the_feed_is_present(data, holdings) -> None:
    lookup = analytics.build_instrument_lookup(data["user_instruments"])
    result = analytics.build_portfolio_analytics(holdings, lookup, data["report"], TODAY, {}, "AUD")
    assert result["stale_price_count"] == 1  # only STALE is out of date
    assert result["price_timestamp_coverage_percent"] == 100.0


def test_cash_drag(data, holdings, income) -> None:
    result = analytics.build_portfolio_analytics(holdings, {}, data["report"], TODAY, income, "AUD")
    # 3000 cash against 22000 equity.
    assert result["cash_drag_percent"] == pytest.approx(12.0, abs=0.01)


# --------------------------------------------------------------------------
# Income forecast
# --------------------------------------------------------------------------


def test_forecast_ignores_sold_holdings(income) -> None:
    """The projection used to include every symbol that ever paid a dividend."""
    held = {"AAA", "BBB", "GLB"}
    baseline = analytics.build_income_forecast(
        F.UPCOMING_PAYOUTS, income, F.PORTFOLIO_VALUE, TODAY, held
    )

    # A position exited years ago still has payouts in the feed, so
    # build_holding_income still produces an entry for it.
    sold = dict(income)
    sold["GONE"] = {"ttm_income": 5000.0, "count": 4, "held": False}

    by_held_symbols = analytics.build_income_forecast(
        F.UPCOMING_PAYOUTS, sold, F.PORTFOLIO_VALUE, TODAY, held
    )
    assert by_held_symbols["forward_annual_income"] == baseline["forward_annual_income"]

    # With no held-symbol set supplied the "held" marker is the fallback.
    by_marker = analytics.build_income_forecast(
        F.UPCOMING_PAYOUTS, sold, F.PORTFOLIO_VALUE, TODAY, None
    )
    assert by_marker["forward_annual_income"] == baseline["forward_annual_income"]

    # An unmarked legacy entry is only excluded by the explicit symbol set -
    # which is why the coordinator always passes one.
    legacy = dict(income)
    legacy["GONE"] = {"ttm_income": 5000.0, "count": 4}
    unfiltered = analytics.build_income_forecast(
        F.UPCOMING_PAYOUTS, legacy, F.PORTFOLIO_VALUE, TODAY, None
    )
    assert unfiltered["forward_annual_income"] - baseline["forward_annual_income"] == pytest.approx(
        5000.0, abs=0.01
    )


def test_forecast_still_projects_an_announced_payer(income) -> None:
    """An announcement used to cancel that payer's whole annual run rate."""
    result = analytics.build_income_forecast(
        F.UPCOMING_PAYOUTS, income, F.PORTFOLIO_VALUE, TODAY, {"AAA", "BBB", "GLB"}
    )
    # AAA announced 110 against a 300 run rate: 190 of it is still projected.
    # BBB announced 55 against 50: nothing further. GLB projects its full 25.
    assert result["announced_income"] == 165.0
    assert result["forward_annual_income"] == pytest.approx(380.0, abs=0.01)


def test_forecast_windows_are_not_duplicates_of_announced_income(income) -> None:
    result = analytics.build_income_forecast(
        F.UPCOMING_PAYOUTS, income, F.PORTFOLIO_VALUE, TODAY, {"AAA", "BBB", "GLB"}
    )
    assert result["income_30d"] != result["announced_income"]
    assert result["income_90d"] > result["income_30d"]


def test_forecast_next_dividend_uses_the_ex_date_fallback() -> None:
    """A payout with no pay date still has a goes_ex_on to sort on."""
    payout = {"symbol": "AAA", "amount": 10.0, "goes_ex_on": "2026-09-10"}
    result = analytics.build_income_forecast([payout], {}, 1000.0, TODAY)
    assert result["days_to_next"] == 14


def test_forecast_handles_empty_inputs() -> None:
    result = analytics.build_income_forecast(None, None, None, TODAY)
    assert result["forward_annual_income"] == 0.0
    assert result["forward_yield_percent"] is None
    assert result["days_to_next"] is None


# --------------------------------------------------------------------------
# Value-series analytics
# --------------------------------------------------------------------------


def test_value_analytics_drawdown() -> None:
    result = analytics.build_value_analytics(F.VALUE_SERIES)
    assert result["all_time_high"] == 24000.0
    assert result["all_time_high_date"] == "2026-08-06"
    # 24000 -> 18000 is a 25% fall.
    assert result["max_drawdown_percent"] == 25.0
    # 22000 today is 8.33% below the peak.
    assert result["current_drawdown_percent"] == pytest.approx(8.33, abs=0.01)
    assert result["days_since_high"] == 21
    assert result["point_count"] == 7


def test_value_analytics_at_a_new_high() -> None:
    series = {
        "chart": {
            "data": [
                {"timestamp": "2026-08-01", "value": 100.0},
                {"timestamp": "2026-08-02", "value": 110.0},
            ]
        }
    }
    result = analytics.build_value_analytics(series)
    assert result["current_drawdown_percent"] == 0.0
    assert result["max_drawdown_percent"] == 0.0


def test_value_analytics_degrades_on_junk() -> None:
    for payload in (None, {}, {"chart": {}}, {"error": "nope"}, []):
        result = analytics.build_value_analytics(payload)
        assert result["max_drawdown_percent"] is None
        assert result["point_count"] == 0


def test_value_trend_percentages() -> None:
    result = analytics.build_value_trend(F.VALUE_SERIES)
    assert result["change_30d_percent"] == pytest.approx(10.0, abs=0.01)
    assert len(result["series"]) == 7


# --------------------------------------------------------------------------
# CGT analytics
# --------------------------------------------------------------------------


def test_cgt_analytics_scalars(data) -> None:
    result = analytics.build_cgt_analytics(data["capital_gains"], data["unrealised_cgt"])
    assert result["short_term_losses"] == -50.0
    assert result["long_term_losses"] == -150.0
    assert result["cgt_concession_rate"] == 0.5


def test_cgt_analytics_harvestable_losses() -> None:
    """Parcels are shaped as the live unrealised_cgt report returns them."""
    unrealised = {
        "losses": [
            {
                "symbol": "AAA",
                "purchase_date": "2025-07-14",
                "cost_base": 560.92,
                "market_value": 440.92,
                "unrealised_gain": -120.0,
            },
            {
                "symbol": "BBB",
                "purchase_date": "2026-01-02",
                "cost_base": 200.0,
                "market_value": 119.5,
                "unrealised_gain": -80.5,
            },
            # A gain in the losses array (Sharesight groups by parcel, not
            # by sign) must not be counted as harvestable.
            {"symbol": "CCC", "unrealised_gain": 10.0},
        ],
        "short_term_parcels": [{}, {}],
        "long_term_parcels": [{}],
        "balance_date": "2026-08-27",
    }
    result = analytics.build_cgt_analytics(None, unrealised)
    assert result["harvestable_loss"] == 200.5
    assert result["harvestable_parcel_count"] == 3
    assert result["unrealised_short_term_parcels"] == 2
    assert result["unrealised_long_term_parcels"] == 1
    assert result["unrealised_balance_date"] == "2026-08-27"
    assert result["largest_loss_symbol"] == "AAA"
    assert result["largest_loss_amount"] == 120.0
    assert result["largest_loss_purchased_on"] == "2025-07-14"


def test_cgt_parcel_gain_falls_back_to_the_arithmetic() -> None:
    """A parcel with no gain field still has cost base and market value."""
    unrealised = {"losses": [{"symbol": "AAA", "cost_base": 100.0, "market_value": 60.0}]}
    result = analytics.build_cgt_analytics(None, unrealised)
    assert result["harvestable_loss"] == 40.0


def test_cgt_analytics_handles_absent_reports() -> None:
    result = analytics.build_cgt_analytics(None, None)
    assert all(value is None for value in result.values())
