"""Contracts with the published 1.6.0 helpers, including concurrent metadata."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from SharesightAPI import SharesightAPI, SharesightAPIError, SharesightResponse

from custom_components.sharesight.api import Endpoint, SharesightApiError, async_request
from custom_components.sharesight.read_client import SharesightReadClient


@pytest.mark.parametrize(
    "path,method,params",
    [
        ("v3/watchlist.json", "get_watchlist", {"start_date": "2026-08-01"}),
        ("v3/instruments/123/sharechecker", "get_sharechecker", None),
        ("v3/holdings/123/average_purchase_price.json", "get_holding_average_purchase_price", None),
        ("v3/holdings/123/cost_base.json", "get_holding_cost_base", None),
        (
            "v3/holdings/123/holding_value_data.json",
            "get_holding_value_data",
            {"start_date": "2026-08-01"},
        ),
        (
            "v3/holdings/123",
            "get_holding",
            {"average_purchase_price": "true", "cost_base": "false"},
        ),
        ("v3/portfolios/123/value", "get_portfolio_value", {"consolidated": "false"}),
        (
            "v3/portfolios/123/portfolio_value_data.json",
            "get_portfolio_value_data",
            {"start_date": "2026-08-01"},
        ),
        (
            "v2/instruments/123/prices.json",
            "list_instrument_prices",
            {"start_date": "2026-08-01", "end_date": "2026-08-31"},
        ),
        ("v2/single_sign_on.json", "get_single_sign_on", None),
        (
            "v2/portfolios/123/capital_gains.json",
            "get_capital_gains",
            {"start_date": "2026-08-01", "end_date": "2026-08-31"},
        ),
        (
            "v2/portfolios/123/unrealised_cgt.json",
            "get_unrealised_cgt",
            {"balance_date": "2026-08-31"},
        ),
        ("v3/portfolios/123/user_setting", "get_portfolio_user_setting", None),
        (
            "v3/portfolios/123/benchmark.json",
            "get_portfolio_benchmark",
            {"interest_method": "simple"},
        ),
        (
            "v3/portfolios/123/performance_index_chart",
            "get_portfolio_performance_index_chart",
            {"grouping": "market"},
        ),
        ("v2/user_instruments.json", "list_user_instruments", None),
        ("v2/my_user.json", "get_my_user", None),
    ],
)
def test_published_helper_preserves_route_controls_body_and_headers(
    monkeypatch, path, method, params
):
    """Exercise upstream code, not a mock of its route construction."""
    original = getattr(SharesightAPI, method)
    called = []

    async def tracked(self, *args, **kwargs):
        called.append(method)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(SharesightReadClient, method, tracked)
    body = {"extra_future_field": 0, "currency": {"code": "AUD"}}
    reply = SharesightResponse(body, 200, {"X-MinuteRate-Remaining": "123"}, "https://example.test")
    client = SimpleNamespace(get_api_response=AsyncMock(return_value=reply))
    version, route = path.split("/", 1)
    result = asyncio.run(async_request(client, Endpoint(version, route, params), "test", 5))
    assert called == [method]
    client.get_api_response.assert_awaited_once_with([version, route, params, False], "test")
    assert result.data is body
    assert result.headers == {"X-MinuteRate-Remaining": "123"}


@pytest.mark.parametrize("params", [{"future_query_control": "keep"}, {"consolidated": "unknown"}])
def test_unmodelled_query_controls_use_raw_transport_without_loss(params):
    reply = SharesightResponse({}, 200, {}, "https://example.test")
    client = SimpleNamespace(get_api_response=AsyncMock(return_value=reply))
    asyncio.run(async_request(client, Endpoint("v3", "portfolios/123/value", params), "test", 5))
    client.get_api_response.assert_awaited_once_with(
        ["v3", "portfolios/123/value", params, False], "test"
    )


def test_concurrent_typed_reads_do_not_share_metadata():
    async def transport(request, token):
        await asyncio.sleep(0)
        return SharesightResponse(
            {"token_label": token}, 200, {"request": token}, "https://example.test"
        )

    async def run():
        client = SimpleNamespace(get_api_response=transport)
        return await asyncio.gather(
            *(
                async_request(client, Endpoint("v3", "watchlist.json"), token, 5)
                for token in ("first", "second")
            )
        )

    replies = asyncio.run(run())
    assert [(r.data["token_label"], r.headers["request"]) for r in replies] == [
        ("first", "first"),
        ("second", "second"),
    ]


@pytest.mark.parametrize(
    "status,reason",
    [
        (401, "expired"),
        (403, "not entitled"),
        (404, "absent"),
        (406, "API version 3 is not supported"),
        (406, "unacceptable format"),
        (408, "timeout"),
        (425, "early"),
        (429, "limited"),
        (500, "failed"),
        (501, "unsupported"),
        (502, "gateway"),
        (503, "busy"),
        (504, "timeout"),
    ],
)
def test_normalised_errors_share_upstream_classifications(status, reason):
    error = SharesightAPIError(status, reason, response_headers={"Retry-After": "5"})
    client = SimpleNamespace(get_api_response=AsyncMock(side_effect=error))
    with pytest.raises(SharesightApiError) as caught:
        asyncio.run(async_request(client, Endpoint("v3", "watchlist.json"), "test", 5))
    for property_name in (
        "is_unauthorised",
        "is_forbidden",
        "is_not_found",
        "is_version_unsupported",
        "is_retryable",
    ):
        assert getattr(caught.value, property_name) == getattr(error, property_name)
    assert caught.value.headers == {"Retry-After": "5"}


def test_ha_transport_and_header_only_rate_limit_extensions_remain():
    assert SharesightApiError(transport=True).is_retryable
    assert SharesightApiError(status=403, headers={"X-MinuteRate-Remaining": "0"}).is_rate_limited
    assert not SharesightApiError().is_retryable


def test_bare_history_array_is_preserved():
    points = [{"timestamp": "2026-08-01", "value": 0}]
    client = SimpleNamespace(
        get_api_response=AsyncMock(
            return_value=SharesightResponse(points, 200, {}, "https://example.test")
        )
    )
    reply = asyncio.run(
        async_request(client, Endpoint("v3", "holdings/123/holding_value_data.json"), "test", 5)
    )
    assert reply.data is points


def test_read_adapter_rejects_mutations_before_transport():
    client = SimpleNamespace(get_api_response=AsyncMock())
    with pytest.raises(ValueError):
        asyncio.run(SharesightReadClient(client)._request("POST", ["v3", "holdings", None]))
    client.get_api_response.assert_not_awaited()
