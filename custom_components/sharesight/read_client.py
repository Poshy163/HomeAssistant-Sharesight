"""Use the pinned client's typed reads without losing per-request HTTP metadata.

Each adapter lives for exactly one request. The coordinator still owns OAuth,
concurrency, retries, version fallback and rate budgeting. Only GET helpers are
dispatched here; their private transport hook is covered by contract tests
against the published client. Unknown routes/parameters retain the raw path.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from SharesightAPI import SharesightAPI, SharesightResponse


@dataclass(frozen=True)
class ReadRoute:
    """A supported helper and its exact keyword parameter contract."""

    pattern: str
    method: str
    parameters: frozenset[str] = frozenset()


_DATES = frozenset({"start_date", "end_date"})
_BOOLS = frozenset({"consolidated", "average_purchase_price", "cost_base"})
_ROUTES = (
    ReadRoute(r"v3/watchlist.json", "get_watchlist", frozenset({"start_date"})),
    ReadRoute(r"v3/instruments/([^/]+)/sharechecker", "get_sharechecker"),
    ReadRoute(
        r"v3/holdings/([^/]+)/average_purchase_price.json", "get_holding_average_purchase_price"
    ),
    ReadRoute(r"v3/holdings/([^/]+)/cost_base.json", "get_holding_cost_base"),
    ReadRoute(
        r"v3/holdings/([^/]+)/holding_value_data.json",
        "get_holding_value_data",
        frozenset({"start_date"}),
    ),
    ReadRoute(
        r"v3/holdings/([^/]+)", "get_holding", _BOOLS - {"consolidated"} | {"values_over_time"}
    ),
    ReadRoute(r"v3/portfolios/([^/]+)/value", "get_portfolio_value", frozenset({"consolidated"})),
    ReadRoute(
        r"v3/portfolios/([^/]+)/portfolio_value_data.json",
        "get_portfolio_value_data",
        frozenset({"start_date", "consolidated"}),
    ),
    ReadRoute(r"v2/instruments/([^/]+)/prices.json", "list_instrument_prices", _DATES),
    ReadRoute(r"v2/single_sign_on.json", "get_single_sign_on"),
    ReadRoute(r"v2/portfolios/([^/]+)/capital_gains.json", "get_capital_gains", _DATES),
    ReadRoute(
        r"v2/portfolios/([^/]+)/unrealised_cgt.json",
        "get_unrealised_cgt",
        frozenset({"balance_date"}),
    ),
    ReadRoute(
        r"v3/portfolios/([^/]+)/user_setting",
        "get_portfolio_user_setting",
        frozenset({"consolidated"}),
    ),
    ReadRoute(
        r"v3/portfolios/([^/]+)/benchmark.json",
        "get_portfolio_benchmark",
        _DATES | {"instrument_id", "consolidated", "interest_method"},
    ),
    ReadRoute(
        r"v3/portfolios/([^/]+)/performance_index_chart",
        "get_portfolio_performance_index_chart",
        _DATES | {"grouping", "consolidated", "custom_group_id", "benchmark_code"},
    ),
    ReadRoute(r"v2/user_instruments.json", "list_user_instruments"),
    ReadRoute(r"v2/my_user.json", "get_my_user"),
)


class SharesightReadClient(SharesightAPI):
    """A GET-only helper adapter using the existing authenticated transport."""

    def __init__(self, client: Any) -> None:
        # No token file, session or second transport is created by this adapter.
        super().__init__("", "", "", "", "", "", use_token_file=False, max_retries=0)
        self._transport = client
        self.response: SharesightResponse | None = None

    async def _request(
        self, method: str, endpoint: list[Any], *, access_token: str | None = None, **kwargs: Any
    ) -> Any:
        """Retain metadata locally while the upstream helper checks the body."""
        if method != "GET" or kwargs:
            raise ValueError("Only read requests are supported")
        request = [*endpoint[:3], False]
        if hasattr(self._transport, "get_api_response"):
            self.response = await self._transport.get_api_response(request, access_token)
        else:
            body = await self._transport.get_api_request(request, access_token)
            self.response = SharesightResponse(data=body, status=200, headers={}, url="")
        return self.response.data

    async def read(self, endpoint: list[Any], access_token: str) -> SharesightResponse:
        """Dispatch known contracts, preserving unsupported query controls."""
        version, path, params = endpoint[:3]
        parameters = dict(params or {})
        for route in _ROUTES:
            match = re.fullmatch(route.pattern.replace(".json", r"\.json"), f"{version}/{path}")
            if match is None or not parameters.keys() <= route.parameters:
                continue
            # Never coerce an unknown boolean spelling or silently drop it.
            if any(
                parameters[key] not in (True, False, "true", "false")
                for key in parameters.keys() & _BOOLS
            ):
                break
            keywords = {
                key: value in (True, "true") if key in _BOOLS else value
                for key, value in parameters.items()
            }
            await getattr(self, route.method)(
                *match.groups(), **keywords, access_token=access_token
            )
            break
        else:
            await self._request("GET", endpoint, access_token=access_token)
        if self.response is None:
            await self._request("GET", endpoint, access_token=access_token)
        assert self.response is not None
        return self.response
