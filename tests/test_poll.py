"""End-to-end simulation of one coordinator poll against a stub API.

This drives the real ``_async_update_data`` - token refresh, all three request
tiers, the merge, carry-forward, cash-account fan-out, post-processing and the
degradation bounds - with only the HTTP client and the OAuth session replaced.
It is the closest thing to an integration test that runs without a Home
Assistant instance, and it is what catches a wiring mistake between the
endpoint plan and the payload keys the platforms read.

``asyncio.run`` is used directly rather than an async pytest plugin so the
suite stays runnable with plugin autoloading disabled (which is how it runs on
Windows, where ``pytest-homeassistant-custom-component`` cannot import).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import aiohttp
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.sharesight.api import Endpoint, SharesightApiError

from . import fixtures as F
from .test_coordinator import make_coordinator

PID = F.PORTFOLIO_ID


class StubOAuthSession:
    """Hands back a token, or raises whatever the test asked it to."""

    def __init__(self, error: Exception | None = None) -> None:
        self.token = {"access_token": "test-token", "expires_at": 9_999_999_999}
        self.error = error
        self.calls = 0

    async def async_ensure_token_valid(self) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


class StubClient:
    """Answers the endpoint plan from the fixtures.

    ``failures`` maps a path fragment to the exception to raise for it, which
    is how the tests exercise a parked optional endpoint, a dead required one
    and a rate limit.
    """

    def __init__(self, failures: dict[str, Exception] | None = None) -> None:
        self.failures = failures or {}
        self.requests: list[tuple[str, str, dict | None]] = []

    async def get_api_request(self, endpoint, access_token=None):
        version, path, params, _ = endpoint
        self.requests.append((version, path, params))
        for fragment, error in self.failures.items():
            if fragment in path:
                raise error
        return self._respond(version, path, params)

    @staticmethod
    def _respond(version, path, params):
        data = F.coordinator_data()
        if path == "portfolios":
            return {"portfolios": F.PORTFOLIOS}
        if path == f"portfolios/{PID}":
            return {"portfolio": F.PORTFOLIO_DETAIL}
        if path.endswith("/performance"):
            if version == "v3":
                return {"report": F.performance_report()}
            return F.period_report(
                start_date=params.get("start_date", "2026-01-01"),
                end_date=params.get("end_date", F.TODAY),
                capital_gain=100.0,
            )
        if path.endswith("/payouts"):
            if params and params.get("start_date") == F.TODAY:
                return {"payouts": F.UPCOMING_PAYOUTS}
            return {"payouts": F.PAYOUTS}
        if path.endswith("/trades"):
            return {"trades": F.TRADES}
        if path == "cash_accounts":
            return {"cash_accounts": F.CASH_ACCOUNTS_V2}
        if "cash_account_transactions" in path:
            return {"cash_account_transactions": F.CASH_TRANSACTIONS}
        if path.endswith("/user_setting"):
            return F.USER_SETTING
        if path == "user_instruments":
            return F.USER_INSTRUMENTS
        if path.endswith("/benchmark"):
            return {"benchmark": F.BENCHMARK}
        if path == "my_user.json":
            return F.MY_USER
        if path == "watchlist.json":
            return F.WATCHLIST
        if path.endswith("/capital_gains"):
            return F.CAPITAL_GAINS
        if path.endswith("/unrealised_cgt"):
            return F.UNREALISED_CGT
        if "portfolio_value_data" in path:
            return F.VALUE_SERIES
        return data.get(path, {})


def build(*, failures=None, oauth_error=None, options=None, data=None):
    coordinator = make_coordinator(options=options, data=data)
    coordinator.sharesight = StubClient(failures)
    coordinator.oauth_session = StubOAuthSession(oauth_error)
    return coordinator


def run(coro):
    return asyncio.run(coro)


def poll(coordinator):
    return run(coordinator._async_update_data())


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


def test_setup_seeds_the_financial_year() -> None:
    coordinator = build()
    run(coordinator._async_setup())
    # The fixture portfolio uses a calendar financial year.
    assert coordinator.start_financial_year.endswith("-01-01")
    assert coordinator.end_financial_year.endswith("-12-31")
    assert coordinator._portfolio_detail["currency_code"] == "AUD"


def test_a_full_poll_produces_every_planned_payload_key() -> None:
    coordinator = build()
    run(coordinator._async_setup())
    data = poll(coordinator)

    for key in (
        "report",
        "portfolios",
        "portfolio_detail",
        "holdings",
        "payouts",
        "upcoming_payouts",
        "trades",
        "cash_accounts_v2",
        "cash_account_transactions",
        "user_setting",
        "user_instruments",
        "benchmark",
        "my_user",
        "watchlist",
        "capital_gains",
        "unrealised_cgt",
        "value_series",
        "one-day",
        "one-week",
        "one-month",
        "ytd",
        "financial-year",
        "all_time",
        "income_report",
        "diversity",
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
        "instrument_lookup",
        "activity_events",
        "activity_events_seq",
    ):
        assert key in data, key


def test_a_poll_stamps_a_real_data_timestamp() -> None:
    coordinator = build()
    run(coordinator._async_setup())
    poll(coordinator)
    assert coordinator.data_timestamp is not None
    assert coordinator.is_degraded is False
    assert coordinator.data_age < timedelta(seconds=30)


def test_slow_windows_are_skipped_on_later_polls() -> None:
    coordinator = build()
    run(coordinator._async_setup())
    poll(coordinator)
    first_slow = sum(
        1
        for _, path, params in coordinator.sharesight.requests
        if params and params.get("start_date") and "performance" in path
    )
    coordinator.sharesight.requests.clear()
    poll(coordinator)
    second_slow = sum(
        1
        for _, path, params in coordinator.sharesight.requests
        if params and params.get("start_date") and "performance" in path
    )
    assert second_slow < first_slow


def test_skipped_slow_windows_are_carried_forward() -> None:
    coordinator = build()
    run(coordinator._async_setup())
    first = poll(coordinator)
    second = poll(coordinator)
    for key in ("financial-year", "ytd", "one-month", "all_time", "value_series"):
        assert second[key] == first[key]


def test_skipped_slow_windows_expire_instead_of_copying_forever() -> None:
    coordinator = build()
    run(coordinator._async_setup())
    first = poll(coordinator)
    slow_keys = {
        endpoint.key
        for endpoint in coordinator._slow_endpoints(coordinator.current_date)
        if endpoint.key is not None
    }
    assert slow_keys <= first.keys()

    for key in slow_keys:
        payload, _ = coordinator._carry_forward[key]
        coordinator._carry_forward[key] = (payload, -1e9)

    second = poll(coordinator)
    assert slow_keys.isdisjoint(second)
    assert slow_keys.isdisjoint(coordinator._carry_forward)


def test_poll_count_only_advances_on_real_data() -> None:
    coordinator = build()
    run(coordinator._async_setup())
    poll(coordinator)
    assert coordinator._poll_count == 1


# --------------------------------------------------------------------------
# Optional endpoint failure
# --------------------------------------------------------------------------


def _api_error(status: int, reason: str) -> SharesightApiError:
    return SharesightApiError(Endpoint("v3", "x", None, None), status=status, reason=reason)


def test_a_parked_optional_endpoint_does_not_fail_the_poll() -> None:
    coordinator = build(failures={"watchlist": _api_error(403, "not entitled")})
    run(coordinator._async_setup())
    data = poll(coordinator)
    assert coordinator.last_update_success is not False
    assert "report" in data
    assert any("watchlist" in key for key in coordinator._optional_endpoint_cooldowns)


def test_a_parked_endpoint_is_not_retried_next_poll() -> None:
    coordinator = build(failures={"watchlist": _api_error(403, "not entitled")})
    run(coordinator._async_setup())
    poll(coordinator)
    coordinator.sharesight.requests.clear()
    poll(coordinator)
    assert not any("watchlist" in path for _, path, _ in coordinator.sharesight.requests)


def test_a_parked_endpoints_last_payload_is_replayed() -> None:
    """Its sensors hold their reading instead of dropping to Unknown."""
    coordinator = build()
    run(coordinator._async_setup())
    first = poll(coordinator)
    assert "watchlist" in first

    coordinator.sharesight.failures = {"watchlist": _api_error(403, "gone")}
    # Clear the cooldown so the endpoint is actually attempted and fails.
    coordinator._optional_endpoint_cooldowns.clear()
    second = poll(coordinator)
    assert second["watchlist"] == first["watchlist"]


def test_backoff_doubles_and_is_capped() -> None:
    from custom_components.sharesight.const import OPTIONAL_ENDPOINT_MAX_BACKOFF

    coordinator = build()
    key = "v3/watchlist.json#watchlist"
    for _ in range(20):
        coordinator._note_optional_failure(key)
    assert coordinator._optional_endpoint_cooldowns[key]["backoff"] <= OPTIONAL_ENDPOINT_MAX_BACKOFF


def test_success_clears_the_backoff() -> None:
    coordinator = build()
    coordinator._note_optional_failure("v3/watchlist.json#watchlist")
    coordinator._note_optional_success("v3/watchlist.json#watchlist")
    assert coordinator._optional_endpoint_cooldowns == {}


# --------------------------------------------------------------------------
# Required endpoint failure and degradation
# --------------------------------------------------------------------------


def test_a_dead_required_endpoint_degrades_rather_than_erasing_data() -> None:
    coordinator = build()
    run(coordinator._async_setup())
    good = poll(coordinator)

    coordinator.sharesight.failures = {"performance": _api_error(500, "boom")}
    degraded = poll(coordinator)
    assert degraded == good
    assert coordinator.is_degraded is True
    assert "required endpoint" in coordinator.degraded_reason


def test_degradation_eventually_gives_up() -> None:
    coordinator = build()
    run(coordinator._async_setup())
    poll(coordinator)
    coordinator.data_timestamp = datetime.now(UTC) - timedelta(hours=6)
    coordinator.sharesight.failures = {"performance": _api_error(500, "boom")}
    with pytest.raises(UpdateFailed):
        poll(coordinator)


def test_a_401_triggers_reauthentication() -> None:
    coordinator = build(
        failures={"performance": _api_error(401, "The OAuth signature can't be verified")}
    )
    run(coordinator._async_setup())
    with pytest.raises(ConfigEntryAuthFailed):
        poll(coordinator)


def test_a_404_on_the_portfolio_raises_a_permanent_entry_error() -> None:
    coordinator = build(failures={f"portfolios/{PID}": _api_error(404, "gone")})
    with pytest.raises(ConfigEntryError, match="Add the replacement portfolio"):
        run(coordinator._async_setup())


def test_a_403_on_a_mobile_endpoint_does_not_trigger_reauthentication() -> None:
    """Optional endpoints 403 routinely; that is entitlement, not auth."""
    coordinator = build(failures={"watchlist": _api_error(403, "not entitled")})
    run(coordinator._async_setup())
    data = poll(coordinator)
    assert "report" in data


def test_a_rate_limit_opens_a_cooldown() -> None:
    coordinator = build()
    run(coordinator._async_setup())
    poll(coordinator)
    coordinator.sharesight.failures = {
        "performance": _api_error(403, "Too many parallel requests.")
    }
    poll(coordinator)
    assert coordinator.lockout_seconds_remaining > 0


def test_a_cooldown_short_circuits_the_next_poll() -> None:
    coordinator = build()
    run(coordinator._async_setup())
    good = poll(coordinator)
    coordinator._register_lockout(timedelta(minutes=5), "test")
    coordinator.sharesight.requests.clear()
    assert poll(coordinator) == good
    assert coordinator.sharesight.requests == []


def test_a_revoked_grant_asks_for_reauthentication_immediately() -> None:
    # It subclasses aiohttp.ClientResponseError, which is precisely why a
    # generic "except ClientError" used to swallow it and retry forever.
    from multidict import CIMultiDict, CIMultiDictProxy
    from yarl import URL

    from custom_components.sharesight.coordinator import (
        OAuth2TokenRequestReauthError,
    )

    if not issubclass(OAuth2TokenRequestReauthError, aiohttp.ClientResponseError):
        pytest.skip("This Home Assistant version predates dedicated OAuth exceptions")

    url = URL("https://api.sharesight.com/oauth2/token")
    error = OAuth2TokenRequestReauthError(
        request_info=aiohttp.RequestInfo(url, "POST", CIMultiDictProxy(CIMultiDict()), url),
        history=(),
        status=400,
        message="invalid_grant",
        domain="sharesight",
    )
    coordinator = build(oauth_error=error)
    with pytest.raises(ConfigEntryAuthFailed):
        run(coordinator._refresh_token_with_retries())
    # No retries: a revoked grant will not un-revoke itself.
    assert coordinator.oauth_session.calls == 1


def _bare_oauth_response_error(status: int, message: str) -> aiohttp.ClientResponseError:
    """Shape emitted by Home Assistant cores before dedicated OAuth errors."""
    from multidict import CIMultiDict, CIMultiDictProxy
    from yarl import URL

    url = URL("https://api.sharesight.com/oauth2/token")
    return aiohttp.ClientResponseError(
        request_info=aiohttp.RequestInfo(url, "POST", CIMultiDictProxy(CIMultiDict()), url),
        history=(),
        status=status,
        message=message,
    )


def test_bare_oauth_401_asks_for_reauthentication_without_retrying() -> None:
    coordinator = build(oauth_error=_bare_oauth_response_error(401, "invalid_grant"))
    coordinator._TOKEN_RETRY_DELAY = 0

    with pytest.raises(ConfigEntryAuthFailed):
        run(coordinator._refresh_token_with_retries())
    assert coordinator.oauth_session.calls == 1


def test_bare_oauth_429_is_retried_and_not_misclassified_as_reauth() -> None:
    error = _bare_oauth_response_error(429, "rate limited")
    coordinator = build(oauth_error=error)
    coordinator._TOKEN_RETRY_DELAY = 0

    with pytest.raises(aiohttp.ClientResponseError) as raised:
        run(coordinator._refresh_token_with_retries())
    assert raised.value is error
    assert coordinator.oauth_session.calls == coordinator._TOKEN_RETRIES + 1


def test_a_transient_token_failure_is_retried_then_degrades() -> None:
    coordinator = build(oauth_error=TimeoutError("token endpoint slow"))
    coordinator._TOKEN_RETRY_DELAY = 0
    coordinator.data = {"report": {"value": 1}}
    coordinator.data_timestamp = datetime.now(UTC)
    assert poll(coordinator) == {"report": {"value": 1}}
    assert coordinator.oauth_session.calls == 3


# --------------------------------------------------------------------------
# Cash accounts
# --------------------------------------------------------------------------


def test_cash_transactions_are_fetched_per_account() -> None:
    coordinator = build()
    run(coordinator._async_setup())
    data = poll(coordinator)
    assert len(data["cash_account_transactions"]["cash_account_transactions"]) == 4


def test_cash_transactions_wait_for_a_complete_per_account_snapshot() -> None:
    class CashClient:
        def __init__(self) -> None:
            self.fail_ids = {901}
            self.rows = {
                900: [{"id": "900-old", "cash_account_id": 900}],
                901: [{"id": "901-old", "cash_account_id": 901}],
            }

        async def get_api_request(self, endpoint, access_token=None):
            _, path, _, _ = endpoint
            account_id = int(path.split("/")[1])
            if account_id in self.fail_ids:
                raise _api_error(500, f"cash account {account_id} failed")
            return {"cash_account_transactions": self.rows[account_id]}

    accounts = [
        dict(F.CASH_ACCOUNTS_V2[0], id=900),
        dict(F.CASH_ACCOUNTS_V2[0], id=901, name="Second Cash"),
    ]
    coordinator = make_coordinator()
    client = CashClient()
    coordinator.sharesight = client

    partial = {"cash_accounts_v2": {"cash_accounts": accounts}}
    run(coordinator._fetch_cash_transactions(partial, "token"))
    assert "cash_account_transactions" not in partial
    assert set(coordinator._cash_transactions_by_account) == {900}

    client.fail_ids.clear()
    coordinator._cash_tx_account_cooldowns.clear()
    complete = {"cash_accounts_v2": {"cash_accounts": accounts}}
    run(coordinator._fetch_cash_transactions(complete, "token"))
    assert {
        row["id"] for row in complete["cash_account_transactions"]["cash_account_transactions"]
    } == {"900-old", "901-old"}

    client.rows[900] = [{"id": "900-new", "cash_account_id": 900}]
    client.fail_ids = {901}
    coordinator._cash_tx_account_cooldowns.clear()
    mixed = {"cash_accounts_v2": {"cash_accounts": accounts}}
    run(coordinator._fetch_cash_transactions(mixed, "token"))
    assert {
        row["id"] for row in mixed["cash_account_transactions"]["cash_account_transactions"]
    } == {"900-new", "901-old"}


def test_an_unfetchable_cash_account_does_not_publish_an_empty_list() -> None:
    """That used to report $0 contributions and a bogus net investment gain."""
    coordinator = build()
    run(coordinator._async_setup())
    good = poll(coordinator)

    coordinator.sharesight.failures = {"cash_account_transactions": _api_error(500, "boom")}
    coordinator._cash_tx_account_cooldowns.clear()
    degraded = poll(coordinator)
    assert degraded["cash_account_transactions"] == good["cash_account_transactions"]


def test_no_cash_accounts_means_no_transaction_requests() -> None:
    coordinator = build(failures={"cash_accounts": _api_error(403, "nope")})
    run(coordinator._async_setup())
    poll(coordinator)
    assert not any(
        "cash_account_transactions" in path for _, path, _ in coordinator.sharesight.requests
    )


# --------------------------------------------------------------------------
# Version fallback
# --------------------------------------------------------------------------


def test_exact_v3_version_mismatch_falls_back_once_and_caches_the_route() -> None:
    class VersionedClient:
        def __init__(self) -> None:
            self.requests: list[str] = []

        async def get_api_request(self, endpoint, access_token=None):
            version, _, _, _ = endpoint
            self.requests.append(version)
            if version == "v3":
                raise _api_error(406, "Version not supported")
            return {"served_by": version}

    coordinator = make_coordinator()
    client = VersionedClient()
    coordinator.sharesight = client
    endpoint = Endpoint("v3", "portfolios/1/performance", key="one-day", fallback_version="v2")

    assert run(coordinator._call(endpoint, "token")) == {"served_by": "v2"}
    assert run(coordinator._call(endpoint, "token")) == {"served_by": "v2"}
    assert client.requests == ["v3", "v2", "v2"]
    assert coordinator._fallback_route_key(endpoint) in coordinator._fallback_routes


def test_v3_404_does_not_probe_the_fallback_version() -> None:
    class NotFoundClient:
        def __init__(self) -> None:
            self.requests: list[str] = []

        async def get_api_request(self, endpoint, access_token=None):
            version, _, _, _ = endpoint
            self.requests.append(version)
            raise _api_error(404, "not found")

    coordinator = make_coordinator()
    client = NotFoundClient()
    coordinator.sharesight = client
    endpoint = Endpoint("v3", "portfolios/1/performance", key="one-day", fallback_version="v2")

    with pytest.raises(SharesightApiError):
        run(coordinator._call(endpoint, "token"))
    assert client.requests == ["v3"]
    assert coordinator._fallback_route_key(endpoint) not in coordinator._fallback_routes


# --------------------------------------------------------------------------
# On-demand calls
# --------------------------------------------------------------------------


def test_one_shot_calls_return_an_error_block_rather_than_raising() -> None:
    coordinator = build(failures={"sharechecker": _api_error(403, "mobile only")})
    result = run(coordinator.async_get_sharechecker(123))
    assert "error" in result
    assert result["status"] == 403


def test_official_costs_prefer_the_single_public_call() -> None:
    coordinator = build()
    coordinator.sharesight._respond = staticmethod(
        lambda version, path, params: {
            "holding": {
                "id": 101,
                "average_purchase_price": 9.75,
                "cost_base": {"total_value": 9750.0, "value_per_share": 9.75},
                "instrument_currency": {"code": "AUD"},
            }
        }
    )
    result = run(coordinator.async_get_official_costs(101))
    paths = [path for _, path, _ in coordinator.sharesight.requests]
    assert paths == ["holdings/101"]

    # Normalised to the shape the two split endpoints return, so the service
    # extractors do not care which route answered.
    from custom_components.sharesight.services import (
        _extract_average_purchase_price,
        _extract_cost_base,
    )

    assert _extract_average_purchase_price(result["average_purchase_price"]) == {
        "value": 9.75,
        "currency": "AUD",
    }
    assert _extract_cost_base(result["cost_base"]) == {
        "total_value": 9750.0,
        "value_per_share": 9.75,
        "currency": None,
    }


def test_official_costs_fall_back_to_split_calls_for_a_route_mismatch() -> None:
    coordinator = build(failures={"holdings/101": _api_error(406, "Version not supported")})
    run(coordinator.async_get_official_costs(101))
    paths = [path for _, path, _ in coordinator.sharesight.requests]
    assert "holdings/101/average_purchase_price.json" in paths
    assert "holdings/101/cost_base.json" in paths


def test_official_costs_do_not_multiply_entitlement_failures() -> None:
    coordinator = build(failures={"holdings/101": _api_error(403, "not entitled")})
    run(coordinator.async_get_official_costs(101))
    paths = [path for _, path, _ in coordinator.sharesight.requests]
    assert paths == ["holdings/101"]
