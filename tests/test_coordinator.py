"""Tests for the coordinator's endpoint plan, merge and post-processing.

``SharesightCoordinator`` inherits from Home Assistant's DataUpdateCoordinator,
whose constructor needs a running core.  These tests therefore build the object
with ``__new__`` and populate only the attributes the methods under test read -
which is enough to exercise every piece of pure logic (the endpoint plan, the
merge, carry-forward, degradation bounds and the whole post-processing
pipeline) on any platform, without a Home Assistant instance.

The end-to-end setup path is covered separately in ``tests/ha/``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import ClassVar

import pytest

from custom_components.sharesight import analytics
from custom_components.sharesight.api import (
    Endpoint,
    SharesightApiError,
    SharesightRequestGate,
    async_request,
)
from custom_components.sharesight.const import (
    DEFAULT_SCAN_INTERVAL,
    MIN_STALE_DATA_GRACE,
)
from custom_components.sharesight.coordinator import (
    SharesightCoordinator,
    get_financial_year_dates,
    merge_dicts,
)

from . import fixtures as F

TODAY = date(2026, 8, 27)


class _Options(dict):
    """Stand-in for ConfigEntry.options."""


class _Entry:
    def __init__(self, options: dict | None = None) -> None:
        self.options = _Options(options or {})
        self.entry_id = "test-entry"


def make_coordinator(
    *, options: dict | None = None, data: dict | None = None
) -> SharesightCoordinator:
    """A coordinator with just enough state for the pure methods."""
    coordinator = SharesightCoordinator.__new__(SharesightCoordinator)
    coordinator.entry = _Entry(options)
    coordinator.portfolio_id = F.PORTFOLIO_ID
    coordinator.data = data if data is not None else {}
    coordinator.data_timestamp = None
    coordinator.update_interval = DEFAULT_SCAN_INTERVAL
    coordinator._portfolio_detail = dict(F.PORTFOLIO_DETAIL)
    coordinator.start_financial_year = "2026-01-01"
    coordinator.end_financial_year = "2026-12-31"
    coordinator._poll_count = 0
    coordinator._degraded_polls = 0
    coordinator.degraded_reason = None
    coordinator._logged_failures = {}
    coordinator._carry_forward = {}
    coordinator._optional_endpoint_cooldowns = {}
    coordinator._cash_tx_account_cooldowns = {}
    coordinator._unsupported_endpoints = set()
    coordinator._fallback_routes = set()
    coordinator._cash_transactions_by_account = {}
    coordinator._request_gate = SharesightRequestGate()
    coordinator._lockout_until = 0.0
    coordinator._lockout_reason = None
    coordinator._activity_seq = 0
    coordinator._activity_seeded = False
    coordinator._seen_trade_ids = set()
    coordinator._seen_payout_ids = set()
    coordinator._seen_upcoming_ids = set()
    coordinator._seen_cash_tx_ids = set()
    coordinator._seen_holding_symbols = set()
    coordinator._holdings_snapshot_seeded = False
    coordinator._seen_daily_close_date = None
    coordinator.current_date = TODAY
    # Created here rather than in __init__ because these tests bypass it.
    # asyncio primitives no longer bind to a loop at construction, so one
    # coordinator can serve several asyncio.run() calls in a single test.
    coordinator._request_semaphore = coordinator._request_gate.request_semaphore
    coordinator._heavy_request_semaphore = coordinator._request_gate.heavy_semaphore
    coordinator._ENDPOINT_TIMEOUT = 5
    # DataUpdateCoordinator sets this; nothing under test writes it.
    coordinator.last_update_success = True
    return coordinator


def post_processed(**overrides) -> dict:
    """Run a full payload through _post_process and return the result."""
    coordinator = make_coordinator()
    combined = F.coordinator_data()
    combined.update(overrides)
    coordinator._post_process(combined, TODAY)
    return combined


# --------------------------------------------------------------------------
# merge_dicts
# --------------------------------------------------------------------------


def test_merge_dicts_is_recursive() -> None:
    left = {"a": {"x": 1, "y": 2}, "b": 1}
    right = {"a": {"y": 3, "z": 4}, "c": 5}
    assert merge_dicts(left, right) == {
        "a": {"x": 1, "y": 3, "z": 4},
        "b": 1,
        "c": 5,
    }


def test_merge_dicts_scalar_wins_over_dict() -> None:
    assert merge_dicts({"a": {"x": 1}}, {"a": 7}) == {"a": 7}


# --------------------------------------------------------------------------
# Financial year
# --------------------------------------------------------------------------


def test_get_financial_year_dates_uses_the_supplied_day() -> None:
    assert get_financial_year_dates("12-31", TODAY) == ("2026-01-01", "2026-12-31")


def test_get_financial_year_dates_survives_a_leap_day_setting() -> None:
    assert get_financial_year_dates("02-29", TODAY) == ("2026-03-01", "2027-02-28")


# --------------------------------------------------------------------------
# Endpoint plan
# --------------------------------------------------------------------------


def test_required_endpoints_pin_the_grouping() -> None:
    """Otherwise the server falls back to the user's saved report preference."""
    coordinator = make_coordinator()
    for endpoint in coordinator._required_endpoints(TODAY):
        if "performance" in endpoint.path:
            assert endpoint.params["grouping"] == "market"


def test_required_endpoints_are_marked_heavy() -> None:
    coordinator = make_coordinator()
    heavy = [e for e in coordinator._required_endpoints(TODAY) if e.heavy]
    assert len(heavy) == 3  # one-day, one-week and the combined V3 report


def test_week_window_never_asks_for_a_future_date() -> None:
    coordinator = make_coordinator()
    week = next(e for e in coordinator._required_endpoints(TODAY) if e.key == "one-week")
    assert week.params["end_date"] == TODAY.isoformat()
    assert week.params["start_date"] <= week.params["end_date"]


def test_period_windows_include_sales() -> None:
    """Realised gains from positions closed inside the window must count."""
    coordinator = make_coordinator()
    windows = coordinator._required_endpoints(TODAY) + coordinator._slow_endpoints(TODAY)
    for endpoint in windows:
        if endpoint.key in {"one-day", "one-week", "financial-year", "ytd", "one-month"}:
            assert endpoint.params["include_sales"] == "true"


def test_slow_tier_has_an_all_time_window_on_the_public_api() -> None:
    """The dedicated /totals endpoint is internal-scoped and 403s in practice."""
    coordinator = make_coordinator()
    all_time = next(e for e in coordinator._slow_endpoints(TODAY) if e.key == "all_time")
    assert all_time.version == "v3"
    assert all_time.path.endswith("/performance")
    assert all_time.params["include_sales"] == "true"


def test_extended_windows_are_opt_in() -> None:
    plain = make_coordinator()
    keys = {e.key for e in plain._slow_endpoints(TODAY)}
    assert "one-year" not in keys

    opted_in = make_coordinator(options={"enable_extended_performance": True})
    keys = {e.key for e in opted_in._slow_endpoints(TODAY)}
    assert {"three-month", "six-month", "one-year", "three-year", "five-year"} <= keys


def test_extended_windows_are_clamped_to_inception() -> None:
    coordinator = make_coordinator(options={"enable_extended_performance": True})
    five_year = next(e for e in coordinator._slow_endpoints(TODAY) if e.key == "five-year")
    # The fixture portfolio was opened in 2023, so a five-year window starts
    # at inception rather than asking for data that cannot exist.
    assert five_year.params["start_date"] == F.PORTFOLIO_DETAIL["inception_date"]


def test_market_diversity_reuses_performance_subtotals() -> None:
    """Do not spend a heavy V2 request on industry buckets labelled as markets."""
    coordinator = make_coordinator()
    assert all(
        endpoint.key != "diversity_v2" for endpoint in coordinator._optional_endpoints(TODAY)
    )
    combined = {"report": F.performance_report()}
    coordinator._post_process(combined, TODAY)
    assert [row["group_name"] for row in combined["diversity"]["breakdown"]] == [
        "ASX",
        "NASDAQ",
    ]


def test_benchmark_matches_the_portfolio_interest_method() -> None:
    coordinator = make_coordinator()
    benchmark = next(e for e in coordinator._optional_endpoints(TODAY) if "benchmark" in e.path)
    assert benchmark.params["interest_method"] == "simple"


def test_no_redundant_holdings_request() -> None:
    """The report carries richer holdings than the standalone endpoint."""
    coordinator = make_coordinator()
    paths = {
        e.path
        for e in coordinator._required_endpoints(TODAY)
        + coordinator._slow_endpoints(TODAY)
        + coordinator._optional_endpoints(TODAY)
    }
    assert f"portfolios/{F.PORTFOLIO_ID}/holdings" not in paths


def test_cgt_endpoints_only_for_australian_portfolios() -> None:
    au = make_coordinator()
    assert any(e.key == "capital_gains" for e in au._optional_endpoints(TODAY))

    non_au = make_coordinator()
    non_au._portfolio_detail = dict(F.PORTFOLIO_DETAIL, country_code="US")
    assert not any(e.key == "capital_gains" for e in non_au._optional_endpoints(TODAY))


def test_known_unsupported_versioned_routes_are_not_polled() -> None:
    coordinator = make_coordinator(data=F.coordinator_data())
    paths = {endpoint.path for endpoint in coordinator._optional_endpoints(TODAY)}
    assert "markets" not in paths
    assert "exchange_rates" not in paths
    assert not any("instrument_news" in path for path in paths)


def test_endpoint_cooldown_key_separates_windows_of_one_path() -> None:
    """Past and upcoming payouts share a path and must back off separately."""
    coordinator = make_coordinator()
    optional = coordinator._optional_endpoints(TODAY)
    payouts = [e for e in optional if e.path.endswith("/payouts")]
    assert len(payouts) == 2
    assert payouts[0].cooldown_key != payouts[1].cooldown_key


def test_endpoint_str_is_loggable() -> None:
    endpoint = Endpoint("v3", "portfolios/1/performance", None, "one-day")
    assert str(endpoint) == "v3/portfolios/1/performance [one-day]"
    assert str(Endpoint("v3", "portfolios", None, None)) == "v3/portfolios"


# --------------------------------------------------------------------------
# Carry-forward and degradation
# --------------------------------------------------------------------------


def test_parked_endpoint_payload_is_replayed() -> None:
    coordinator = make_coordinator()
    coordinator._remember("watchlist", {"watchlist": [1, 2, 3]})
    combined: dict = {}
    replayed = coordinator._replay_missing(combined)
    assert replayed == ["watchlist"]
    assert combined["watchlist"] == {"watchlist": [1, 2, 3]}


def test_fresh_data_is_not_overwritten_by_the_cache() -> None:
    coordinator = make_coordinator()
    coordinator._remember("watchlist", {"watchlist": ["old"]})
    combined = {"watchlist": {"watchlist": ["new"]}}
    assert coordinator._replay_missing(combined) == []
    assert combined["watchlist"] == {"watchlist": ["new"]}


def test_carry_forward_expires() -> None:
    """A stale optional payload is worse than an honest unknown."""
    coordinator = make_coordinator()
    coordinator._remember("watchlist", {"watchlist": []})
    # Age the cache entry well past the maximum.
    payload, _ = coordinator._carry_forward["watchlist"]
    coordinator._carry_forward["watchlist"] = (payload, -1e9)
    combined: dict = {}
    assert coordinator._replay_missing(combined) == []
    assert "watchlist" not in combined
    assert "watchlist" not in coordinator._carry_forward


def test_degrade_serves_the_previous_payload_within_the_grace_period() -> None:
    coordinator = make_coordinator(data={"report": {"value": 1}})
    coordinator.data_timestamp = datetime.now(UTC)
    assert coordinator._degrade("transient blip") == {"report": {"value": 1}}
    assert coordinator.is_degraded is True
    assert coordinator.degraded_reason == "transient blip"


def test_degrade_gives_up_once_the_data_is_too_stale() -> None:
    """Otherwise the integration reports healthy through a multi-hour outage."""
    from homeassistant.helpers.update_coordinator import UpdateFailed

    coordinator = make_coordinator(data={"report": {"value": 1}})
    coordinator.data_timestamp = datetime.now(UTC) - timedelta(days=1)
    with pytest.raises(UpdateFailed):
        coordinator._degrade("sustained outage")


def test_degrade_raises_when_there_is_nothing_to_serve() -> None:
    from homeassistant.helpers.update_coordinator import UpdateFailed

    coordinator = make_coordinator()
    with pytest.raises(UpdateFailed):
        coordinator._degrade("first poll failed")


def test_stale_limit_has_a_floor_for_short_intervals() -> None:
    coordinator = make_coordinator()
    coordinator.update_interval = timedelta(seconds=60)
    assert coordinator._stale_data_limit() == MIN_STALE_DATA_GRACE


def test_log_failure_deduplicates() -> None:
    coordinator = make_coordinator()
    coordinator._log_failure("k", "boom %s", 1)
    assert coordinator._logged_failures["k"] == "boom 1"
    coordinator._log_failure("k", "boom %s", 1)
    coordinator._log_recovery("k", "better")
    assert "k" not in coordinator._logged_failures


# --------------------------------------------------------------------------
# Currency resolution
# --------------------------------------------------------------------------


def test_portfolio_currency_prefers_the_report() -> None:
    coordinator = make_coordinator(data=F.coordinator_data())
    assert coordinator.portfolio_currency == "AUD"


def test_portfolio_currency_ignores_other_portfolios_in_the_account() -> None:
    """It used to take portfolios[0], whichever portfolio that was."""
    coordinator = make_coordinator(
        data={
            "portfolios": [
                {"id": 999, "currency_code": "GBP"},
                {"id": F.PORTFOLIO_ID, "currency_code": "NZD"},
            ]
        }
    )
    coordinator._portfolio_detail = {}
    assert coordinator.portfolio_currency == "NZD"


def test_portfolio_currency_never_guesses_when_metadata_is_missing() -> None:
    coordinator = make_coordinator()
    coordinator._portfolio_detail = {}
    with pytest.raises(ValueError, match="did not identify"):
        _ = coordinator.portfolio_currency


def test_portfolio_report_boundary_uses_portfolio_timezone() -> None:
    coordinator = make_coordinator()

    boundary = coordinator.portfolio_start_of_day(date(2026, 8, 27))

    assert boundary == datetime(2026, 8, 27, tzinfo=boundary.tzinfo)
    assert boundary.tzname() == "AEST"
    assert boundary.utcoffset() == timedelta(hours=10)


# --------------------------------------------------------------------------
# Post-processing
# --------------------------------------------------------------------------


def test_post_process_filters_sold_out_holdings() -> None:
    combined = post_processed()
    symbols = {analytics.holding_symbol(h) for h in combined["holdings"]["holdings"]}
    assert "ZERO" not in symbols
    assert symbols == {"AAA", "BBB", "GLB", "STALE"}


def test_post_process_builds_every_derived_key() -> None:
    combined = post_processed()
    for key in (
        "holding_income",
        "holding_trades",
        "sector_allocation",
        "industry_allocation",
        "type_allocation",
        "currency_allocation",
        "portfolio_analytics",
        "value_trend",
        "value_analytics",
        "label_allocation",
        "cgt_analytics",
        "income_report",
        "diversity",
        "instrument_lookup",
    ):
        assert key in combined, key


def test_post_process_income_total_is_currency_converted() -> None:
    combined = post_processed()
    # 100 + 200 + 50 + 25, all at rate 1.0 in the fixture.
    assert combined["income_report"]["total_income"] == 375.0


def test_post_process_forecast_only_projects_held_symbols() -> None:
    combined = post_processed()
    # GONE paid dividends historically but is not in the holdings list.
    gone = dict(F.PAYOUTS[0], symbol="GONE", holding_id=999, amount=9999.0)
    combined_with_ghost = post_processed(payouts={"payouts": [*F.PAYOUTS, gone]})
    assert (
        combined_with_ghost["income_report"]["forward_annual_income"]
        == combined["income_report"]["forward_annual_income"]
    )


def test_post_process_treats_present_empty_holdings_as_authoritative() -> None:
    """Selling the final holding must clear the previous holdings snapshot."""
    coordinator = make_coordinator(
        data={"holdings": {"holdings": F.OPEN_HOLDINGS, "value": 22000.0}}
    )
    combined = {"report": dict(F.performance_report(), holdings=[])}
    coordinator._post_process(combined, TODAY)
    assert combined["holdings"]["holdings"] == []


@pytest.mark.parametrize("holdings", [None, {"unexpected": "shape"}])
def test_post_process_keeps_previous_holdings_when_field_is_malformed(holdings) -> None:
    """Only a valid list can replace the last known holdings snapshot."""
    previous = {"holdings": F.OPEN_HOLDINGS, "value": 22000.0}
    coordinator = make_coordinator(data={"holdings": previous})
    combined = {"report": dict(F.performance_report(), holdings=holdings)}
    coordinator._post_process(combined, TODAY)
    assert combined["holdings"] == previous


def test_post_process_keeps_previous_holdings_when_field_is_missing() -> None:
    """A short report without a holdings field must not erase live entities."""
    previous = {"holdings": F.OPEN_HOLDINGS, "value": 22000.0}
    coordinator = make_coordinator(data={"holdings": previous})
    report = F.performance_report()
    report.pop("holdings")
    combined = {"report": report}
    coordinator._post_process(combined, TODAY)
    assert combined["holdings"] == previous


def test_post_process_dedupes_repeated_report_rows() -> None:
    report = F.performance_report()
    report["sub_totals"] = report["sub_totals"] + [report["sub_totals"][0]]
    combined = post_processed(report=report)
    names = [row["group_name"] for row in combined["report"]["sub_totals"]]
    assert names == ["ASX", "NASDAQ"]


def test_post_process_carries_the_diversity_breakdown_forward() -> None:
    previous = {"diversity": {"breakdown": [{"group_name": "ASX", "value": 1}]}}
    coordinator = make_coordinator(data=previous)
    combined = {"report": {}}
    coordinator._post_process(combined, TODAY)
    assert combined["diversity"] == previous["diversity"]


def test_post_process_treats_empty_market_subtotals_as_authoritative() -> None:
    previous = {"diversity": {"breakdown": [{"group_name": "ASX", "value": 1}]}}
    coordinator = make_coordinator(data=previous)
    combined = {"report": {"sub_totals": [], "holdings": [], "value": 0}}
    coordinator._post_process(combined, TODAY)
    assert combined["diversity"] == {"breakdown": []}


def test_post_process_updates_the_financial_year_from_our_own_portfolio() -> None:
    coordinator = make_coordinator()
    coordinator.start_financial_year = "2000-01-01"
    coordinator.end_financial_year = "2000-12-31"
    combined = F.coordinator_data()
    combined["portfolios"] = [
        {"id": 999, "financial_year_end": "06-30"},
        {"id": F.PORTFOLIO_ID, "financial_year_end": "12-31"},
    ]
    coordinator._post_process(combined, TODAY)
    assert coordinator.start_financial_year == "2026-01-01"
    assert coordinator.end_financial_year == "2026-12-31"


# --------------------------------------------------------------------------
# Activity events
# --------------------------------------------------------------------------


def test_first_poll_seeds_silently() -> None:
    combined = post_processed()
    assert combined["activity_events"] == {}


def test_second_poll_reports_only_new_records() -> None:
    coordinator = make_coordinator()
    first = F.coordinator_data()
    coordinator._post_process(first, TODAY)
    coordinator.data = first

    second = F.coordinator_data()
    new_trade = dict(F.TRADES[0], id=999, transaction_date="2026-08-27")
    second["trades"] = {"trades": [*F.TRADES, new_trade]}
    coordinator._post_process(second, TODAY)

    events = second["activity_events"]
    assert list(events) == ["trade_confirmed"]
    assert events["trade_confirmed"][0]["symbol"] == "AAA"
    assert second["activity_events_seq"] == 2


def test_records_beyond_the_cap_are_not_lost() -> None:
    """They used to be marked seen before truncation and never fired again."""
    coordinator = make_coordinator()
    first = F.coordinator_data()
    first["trades"] = {"trades": []}
    coordinator._post_process(first, TODAY)
    coordinator.data = first

    many = [dict(F.TRADES[0], id=1000 + n) for n in range(30)]
    second = F.coordinator_data()
    second["trades"] = {"trades": many}
    coordinator._post_process(second, TODAY)
    assert len(second["activity_events"]["trade_confirmed"]) == 20

    third = F.coordinator_data()
    third["trades"] = {"trades": many}
    coordinator._post_process(third, TODAY)
    assert len(third["activity_events"]["trade_confirmed"]) == 10


def test_announced_dividend_event_carries_a_real_ex_date() -> None:
    """The live field is goes_ex_on; reading ex_date always produced None."""
    coordinator = make_coordinator()
    first = F.coordinator_data()
    first["upcoming_payouts"] = {"payouts": []}
    coordinator._post_process(first, TODAY)
    coordinator.data = first

    second = F.coordinator_data()
    coordinator._post_process(second, TODAY)
    announced = second["activity_events"]["dividend_announced"]
    assert announced[0]["ex_date"] == "2026-09-01"
    assert announced[0]["reinvested"] is True


def test_cash_transaction_event_carries_its_type() -> None:
    """The type is nested; the flat keys the diff used to read do not exist."""
    coordinator = make_coordinator()
    first = F.coordinator_data()
    first["cash_account_transactions"] = {"cash_account_transactions": []}
    coordinator._post_process(first, TODAY)
    coordinator.data = first

    second = F.coordinator_data()
    coordinator._post_process(second, TODAY)
    events = second["activity_events"]["cash_transaction"]
    assert {event["type"] for event in events} == {"DEPOSIT", "WITHDRAWAL", "DIVIDEND"}


def test_activity_money_fields_are_explicit_in_mixed_currency_portfolio() -> None:
    coordinator = make_coordinator()
    first = F.coordinator_data()
    first["payouts"] = {"payouts": []}
    first["upcoming_payouts"] = {"payouts": []}
    first["trades"] = {"trades": []}
    first["cash_account_transactions"] = {"cash_account_transactions": []}
    coordinator._post_process(first, TODAY)
    coordinator.data = first

    foreign_payout = dict(F.PAYOUTS[3], id=999, amount=25.0, exchange_rate=0.5)
    foreign_trade = dict(F.TRADES[2], id=999, value=5200.0, price=260.0)
    foreign_account = dict(F.CASH_ACCOUNTS_V2[0], currency="USD")
    foreign_cash = dict(F.CASH_TRANSACTIONS[0], id=999, amount=100.0)
    second = F.coordinator_data()
    second["payouts"] = {"payouts": [foreign_payout]}
    second["upcoming_payouts"] = {"payouts": []}
    second["trades"] = {"trades": [foreign_trade]}
    second["cash_accounts_v2"] = {"cash_accounts": [foreign_account]}
    second["cash_account_transactions"] = {"cash_account_transactions": [foreign_cash]}
    coordinator._post_process(second, TODAY)

    paid = second["activity_events"]["dividend_paid"][0]
    assert paid["amount"] == 50.0
    assert paid["currency"] == "AUD"
    assert paid["native_amount"] == 25.0
    assert paid["native_currency"] == "USD"

    trade = second["activity_events"]["trade_confirmed"][0]
    assert trade["value_currency"] == "AUD"
    assert trade["price_currency"] == "USD"

    cash = second["activity_events"]["cash_transaction"][0]
    assert cash["amount"] is None
    assert cash["currency"] == "AUD"
    assert cash["native_amount"] == 100.0
    assert cash["native_currency"] == "USD"


def test_holding_closed_fires_for_an_authoritative_empty_snapshot() -> None:
    coordinator = make_coordinator()
    first = F.coordinator_data()
    coordinator._post_process(first, TODAY)
    coordinator.data = first

    second = F.coordinator_data()
    second["report"] = dict(F.performance_report(), holdings=[])
    second["holdings"] = {"holdings": [], "value": 0}
    coordinator._post_process(second, TODAY)
    assert second["activity_events"]["holding_closed"] == [
        {"symbol": symbol}
        for symbol in sorted(analytics.holding_symbol(holding) for holding in F.OPEN_HOLDINGS)
    ]


def test_holding_closed_does_not_fire_for_a_missing_snapshot() -> None:
    coordinator = make_coordinator()
    first = F.coordinator_data()
    coordinator._post_process(first, TODAY)
    coordinator.data = first

    second = F.coordinator_data()
    second["report"].pop("holdings")
    coordinator._post_process(second, TODAY)
    assert "holding_closed" not in second["activity_events"]


# --------------------------------------------------------------------------
# API error classification
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "reason", "expected"),
    [
        (401, "Token incorrect, expired or locked out", "lockout"),
        (401, "The OAuth signature can't be verified", "unauthorised"),
        (403, "Too many parallel requests. Currently 3 in process.", "rate_limited"),
        (403, "not entitled", "forbidden"),
        (404, "not found", "not_found"),
        (429, "slow down", "rate_limited"),
    ],
)
def test_api_error_classification(status, reason, expected) -> None:
    err = SharesightApiError(Endpoint("v3", "portfolios", None, None), status=status, reason=reason)
    result = {
        "lockout": err.is_lockout,
        "unauthorised": err.is_unauthorised and not err.is_lockout,
        "rate_limited": err.is_rate_limited,
        "forbidden": err.is_forbidden and not err.is_rate_limited,
        "not_found": err.is_not_found,
    }
    assert result[expected] is True


def test_api_error_classifies_header_only_403_budget_exhaustion() -> None:
    """A generic 403 still means throttling when the minute budget is zero."""
    err = SharesightApiError(
        Endpoint("v3", "portfolios"),
        status=403,
        reason="Forbidden",
        headers={"X-MinuteRate-Remaining": "0"},
    )

    assert err.is_rate_limited
    assert err.is_forbidden


def test_api_error_detail_is_log_shaped() -> None:
    err = SharesightApiError(
        Endpoint("v2", "portfolios/1/performance", None, "one-day"),
        status=429,
        code=2004,
        reason="slow down",
        transaction_id=42,
    )
    assert err.detail == (
        "endpoint=v2/portfolios/1/performance [one-day], status=429, "
        "code=2004, reason=slow down, txn=42"
    )


def test_transport_errors_are_always_retryable() -> None:
    err = SharesightApiError(None, reason="Timeout", transport=True)
    assert err.is_retryable is True
    assert err.status is None


def test_request_gate_honours_case_insensitive_server_budget_headers() -> None:
    gate = SharesightRequestGate()
    gate.observe_headers({"X-MinuteRate-Limit": "360", "x-minuterate-remaining": "7"})

    assert gate.minute_limit == 360
    assert gate.minute_remaining == 7
    assert gate.reserve() is None
    assert gate.reserve() is None
    assert gate.minute_remaining == 5
    assert gate.reserve() is not None


def test_request_gate_ignores_zero_limit_placeholder_headers() -> None:
    gate = SharesightRequestGate()
    gate.observe_headers({"X-MinuteRate-Limit": "360", "X-MinuteRate-Remaining": "0"})
    assert gate.reserve() is not None

    gate.observe_headers({"X-MinuteRate-Limit": "0", "X-MinuteRate-Remaining": "0"})

    assert gate.minute_limit == 360
    assert gate.minute_remaining is None
    assert gate.headers_observed_at is None
    assert gate.reserve() is None
    assert len(gate.request_times) == 1


def test_request_gate_honours_exhausted_positive_server_budget() -> None:
    gate = SharesightRequestGate()
    gate.observe_headers({"X-MinuteRate-Limit": "360", "X-MinuteRate-Remaining": "0"})

    assert gate.reserve() is not None


def test_request_gate_rejects_partial_or_malformed_budget_headers_atomically() -> None:
    gate = SharesightRequestGate()
    gate.observe_headers({"X-MinuteRate-Limit": "360", "X-MinuteRate-Remaining": "7"})
    observed_at = gate.headers_observed_at

    gate.observe_headers({"X-MinuteRate-Limit": "120"})
    gate.observe_headers({"X-MinuteRate-Limit": "invalid", "X-MinuteRate-Remaining": "0"})
    gate.observe_headers({"X-MinuteRate-Limit": "120", "X-MinuteRate-Remaining": "121"})

    assert gate.minute_limit == 360
    assert gate.minute_remaining == 7
    assert gate.headers_observed_at == observed_at


def test_async_request_returns_response_local_metadata() -> None:
    class RichResponse:
        data: ClassVar = {"portfolios": []}
        status = 200
        headers: ClassVar = {
            "X-MinuteRate-Limit": "360",
            "X-MinuteRate-Remaining": "359",
        }

    class RichClient:
        async def get_api_response(self, endpoint, access_token):
            return RichResponse()

    result = asyncio.run(async_request(RichClient(), Endpoint("v3", "portfolios"), "token", 5))

    assert result.data == {"portfolios": []}
    assert result.status == 200
    assert result.headers == RichResponse.headers
