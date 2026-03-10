import logging
import asyncio
from datetime import date, datetime, timedelta
from typing import Any, Dict
import itertools

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


async def merge_dicts(d1: Dict[Any, Any], d2: Dict[Any, Any]) -> Dict[Any, Any]:
    for key in itertools.chain(d1.keys(), d2.keys()):
        if key in d1 and key in d2:
            if isinstance(d1[key], dict) and isinstance(d2[key], dict):
                d1[key] = await merge_dicts(d1[key], d2[key])
            else:
                d1[key] = d2[key]
        elif key in d2:
            d1[key] = d2[key]
    return d1


async def get_financial_year_dates(end_date_str):
    if not end_date_str:
        today = datetime.today()
        end_year = today.year if today.month <= 6 else today.year + 1
        return f"{end_year - 1}-07-01", f"{end_year}-06-30"

    end_date = datetime.strptime(end_date_str, "%m-%d")
    today = datetime.today()
    end_year = today.year if today.month <= 6 else today.year + 1
    end_date = end_date.replace(year=end_year)
    start_date = end_date.replace(year=end_year - 1) + timedelta(days=1)

    return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")


class SharesightCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, portfolio_id, client, oauth_session) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.sharesight = client
        self.oauth_session = oauth_session
        self.update_method = self._async_update_data
        self.data: dict = {}
        self.portfolio_id = portfolio_id
        self.startup_endpoint = ["v3", f"portfolios/{self.portfolio_id}", None, False]
        self.started_up = False
        self._failed_optional_endpoints: set[str] = set()
        self._failed_cash_transaction_accounts: set[int] = set()
        # Sharesight limits intensive report endpoints to 3 concurrent requests.
        self._heavy_request_semaphore = asyncio.Semaphore(3)
        # General cap to avoid request bursts across many portfolios.
        self._request_semaphore = asyncio.Semaphore(8)

        # Monkey-patch convenience methods if they don't exist
        if not hasattr(self.sharesight, 'get_portfolio_holdings'):
            self.sharesight.get_portfolio_holdings = self._get_portfolio_holdings
        if not hasattr(self.sharesight, 'get_portfolio_income_report'):
            self.sharesight.get_portfolio_income_report = self._get_portfolio_income_report
        if not hasattr(self.sharesight, 'get_portfolio_diversity'):
            self.sharesight.get_portfolio_diversity = self._get_portfolio_diversity

    async def _get_portfolio_holdings(self, portfolio_id, access_token=None):
        """Get holdings for a portfolio."""
        return await self.sharesight.get_api_request(['v3', f'portfolios/{portfolio_id}/holdings', None], access_token)

    async def _get_portfolio_income_report(self, portfolio_id, access_token=None):
        """Get income report for a portfolio."""
        return await self.sharesight.get_api_request(['v3', f'portfolios/{portfolio_id}/income_report', None], access_token)

    async def _get_portfolio_diversity(self, portfolio_id, access_token=None):
        """Get diversity report for a portfolio."""
        return await self.sharesight.get_api_request(['v3', f'portfolios/{portfolio_id}/diversity', None], access_token)

    @staticmethod
    def _is_heavy_endpoint(path: str) -> bool:
        """Whether this endpoint should be constrained by the heavy concurrency limit."""
        heavy_markers = ("/performance", "/diversity", "/valuation")
        return any(marker in path for marker in heavy_markers)

    async def _call_endpoint(self, endpoint, access_token):
        """Call one API endpoint with concurrency controls."""
        version, path, params, _ = endpoint

        async with self._request_semaphore:
            if self._is_heavy_endpoint(path):
                async with self._heavy_request_semaphore:
                    return await self.sharesight.get_api_request([version, path, params, False], access_token)
            return await self.sharesight.get_api_request([version, path, params, False], access_token)

    async def _async_update_data(self):
        await self.oauth_session.async_ensure_token_valid()
        access_token = self.oauth_session.token["access_token"]
        combined_dict = {}

        if not self.started_up:
            local_data = await self.sharesight.get_api_request(
                self.startup_endpoint, access_token
            )
            self.start_financial_year, self.end_financial_year = (
                await get_financial_year_dates(
                    local_data.get("portfolio", {}).get("financial_year_end")
                )
            )
            self.started_up = True

        self.current_date = date.today()

        self.start_of_week = (
            self.current_date - timedelta(days=self.current_date.weekday())
        ).strftime("%Y-%m-%d")
        self.end_of_week = (
            self.current_date + timedelta(days=6 - self.current_date.weekday())
        ).strftime("%Y-%m-%d")

        performance_params: dict[str, Any] = {
            "include_limited": "true",
            "report_combined": "true",
        }

        _LOGGER.debug(
            "Performance request params: include_limited=%s report_combined=%s",
            performance_params.get("include_limited"),
            performance_params.get("report_combined"),
        )

        endpoint_list = [
            [
                "v2",
                f"portfolios/{self.portfolio_id}/performance",
                {"start_date": f"{self.current_date}", "end_date": f"{self.current_date}"},
                "one-day",
            ],
            [
                "v2",
                f"portfolios/{self.portfolio_id}/performance",
                {"start_date": f"{self.start_of_week}", "end_date": f"{self.end_of_week}"},
                "one-week",
            ],
            [
                "v2",
                f"portfolios/{self.portfolio_id}/performance",
                {
                    "start_date": f"{self.start_financial_year}",
                    "end_date": f"{self.end_financial_year}",
                },
                "financial-year",
            ],
            ["v3", "portfolios", None, False],
            ["v3", f"portfolios/{self.portfolio_id}/performance", performance_params, False],
        ]

        # Optional endpoints that may fail (premium features or different API plans)
        optional_endpoint_list = [
            ["v3", f"portfolios/{self.portfolio_id}/holdings", None, "holdings"],
            ["v2", f"portfolios/{self.portfolio_id}/payouts", None, "payouts"],
            ["v2", f"portfolios/{self.portfolio_id}/diversity", None, "diversity_v2"],
            ["v2", f"portfolios/{self.portfolio_id}/trades", None, "trades"],
            ["v2", "cash_accounts", None, "cash_accounts_v2"],
            ["v3", f"portfolios/{self.portfolio_id}/user_setting", None, "user_setting"],
            ["v2", "user_instruments", None, "user_instruments"],
        ]

        try:
            _LOGGER.debug("Calling %s required endpoints in parallel", len(endpoint_list))
            required_tasks = [self._call_endpoint(endpoint, access_token) for endpoint in endpoint_list]
            required_results = await asyncio.gather(*required_tasks)

            for endpoint, response in zip(endpoint_list, required_results):
                _LOGGER.debug(
                    "Response for %s: %s",
                    endpoint[1],
                    list(response.keys()) if isinstance(response, dict) else type(response),
                )
                extension = endpoint[3]
                if extension:
                    response = {extension: response}
                combined_dict = await merge_dicts(combined_dict, response)

            # Try optional endpoints - don't fail if they error
            active_optional = [
                endpoint
                for endpoint in optional_endpoint_list
                if endpoint[1] not in self._failed_optional_endpoints
            ]
            _LOGGER.debug("Calling %s optional endpoints in parallel", len(active_optional))
            optional_tasks = [self._call_endpoint(endpoint, access_token) for endpoint in active_optional]
            optional_results = await asyncio.gather(*optional_tasks, return_exceptions=True)

            for endpoint, result in zip(active_optional, optional_results):
                endpoint_path = endpoint[1]
                extension = endpoint[3]

                if isinstance(result, Exception):
                    _LOGGER.info("Optional endpoint %s failed: %s, skipping future calls", endpoint_path, result)
                    self._failed_optional_endpoints.add(endpoint_path)
                    continue

                response = result
                if response is None:
                    _LOGGER.info("Optional endpoint %s returned None, skipping", endpoint_path)
                    self._failed_optional_endpoints.add(endpoint_path)
                    continue
                if not isinstance(response, dict):
                    _LOGGER.info(
                        "Optional endpoint %s returned non-dict: %s, skipping",
                        endpoint_path,
                        type(response),
                    )
                    self._failed_optional_endpoints.add(endpoint_path)
                    continue
                if 'error' in response:
                    _LOGGER.info(
                        "Optional endpoint %s returned error: %s, skipping future calls",
                        endpoint_path,
                        response.get('error'),
                    )
                    self._failed_optional_endpoints.add(endpoint_path)
                    continue

                if extension:
                    response = {extension: response}

                _LOGGER.debug(
                    "Optional response for %s: %s",
                    endpoint_path,
                    list(result.keys()) if isinstance(result, dict) else type(result),
                )
                combined_dict = await merge_dicts(combined_dict, response)

            # Fetch per-account cash transactions for the selected portfolio.
            # These power contribution sensors and are optional for users/plans
            # that don't expose cash account transaction APIs.
            cash_accounts_data = combined_dict.get("cash_accounts_v2", {})
            cash_accounts = []
            if isinstance(cash_accounts_data, dict):
                cash_accounts = cash_accounts_data.get("cash_accounts", [])

            cash_account_transactions: list[dict[str, Any]] = []
            if cash_accounts:
                tx_work = []
                for account in cash_accounts:
                    account_id = account.get("id")
                    account_portfolio_id = account.get("portfolio_id")
                    if account_id is None or str(account_portfolio_id) != str(self.portfolio_id):
                        continue
                    if account_id in self._failed_cash_transaction_accounts:
                        continue
                    endpoint = ["v2", f"cash_accounts/{account_id}/cash_account_transactions", None, False]
                    tx_work.append((account_id, endpoint))

                if tx_work:
                    tx_tasks = [self._call_endpoint(endpoint, access_token) for _, endpoint in tx_work]
                    tx_results = await asyncio.gather(*tx_tasks, return_exceptions=True)

                    for (account_id, endpoint), tx_result in zip(tx_work, tx_results):
                        tx_endpoint_path = endpoint[1]
                        if isinstance(tx_result, Exception):
                            _LOGGER.info(
                                "Optional cash account transactions endpoint %s failed: %s",
                                tx_endpoint_path,
                                tx_result,
                            )
                            self._failed_cash_transaction_accounts.add(account_id)
                            continue

                        tx_response = tx_result
                        if not isinstance(tx_response, dict) or "error" in tx_response:
                            self._failed_cash_transaction_accounts.add(account_id)
                            continue
                        tx_list = tx_response.get("cash_account_transactions", [])
                        if isinstance(tx_list, list):
                            cash_account_transactions.extend(tx_list)

            combined_dict["cash_account_transactions"] = {
                "cash_account_transactions": cash_account_transactions
            }

            self.data = combined_dict
            _LOGGER.debug(f"Data keys available: {list(self.data.keys())}")

            report_data = self.data.get('report', {})
            report_holdings = report_data.get('holdings', [])
            _LOGGER.debug(f"Report keys: {list(report_data.keys())}")

            sub_totals = report_data.get('sub_totals', [])
            if sub_totals:
                # Deduplicate sub_totals by group_name (API may return duplicates)
                seen_groups: set[str] = set()
                deduped_sub_totals = []
                for st in sub_totals:
                    gn = st.get('group_name', '')
                    if gn not in seen_groups:
                        seen_groups.add(gn)
                        deduped_sub_totals.append(st)
                    else:
                        _LOGGER.debug(f"Deduplicating sub_total group_name: {gn}")
                if len(deduped_sub_totals) < len(sub_totals):
                    self.data['report']['sub_totals'] = deduped_sub_totals
                    sub_totals = deduped_sub_totals
                _LOGGER.debug(f"Sub totals count: {len(sub_totals)}, sample keys: {list(sub_totals[0].keys())}")
                _LOGGER.debug(f"Sub totals sample data: {sub_totals[0]}")

            # Deduplicate cash_accounts by name
            cash_accounts = report_data.get('cash_accounts', [])
            if cash_accounts:
                seen_cash_names: set[str] = set()
                deduped_cash = []
                for ca in cash_accounts:
                    cn = ca.get('name', '')
                    if cn not in seen_cash_names:
                        seen_cash_names.add(cn)
                        deduped_cash.append(ca)
                    else:
                        _LOGGER.debug(f"Deduplicating cash_account name: {cn}")
                if len(deduped_cash) < len(cash_accounts):
                    self.data['report']['cash_accounts'] = deduped_cash

            one_day_data = self.data.get('one-day', {})
            if one_day_data:
                _LOGGER.debug(f"One-day keys: {list(one_day_data.keys())}")
            one_week_data = self.data.get('one-week', {})
            if one_week_data:
                _LOGGER.debug(f"One-week keys: {list(one_week_data.keys())}")

            # Always use report holdings as the canonical holdings source since
            # it contains value, capital_gain, etc. per holding and the
            # portfolio-level value.  The dedicated v3 holdings endpoint may
            # succeed but returns a different structure without gain fields.
            holdings_from_api = self.data.get('holdings', {})
            _LOGGER.debug(f"Holdings keys: {list(holdings_from_api.keys()) if isinstance(holdings_from_api, dict) else type(holdings_from_api)}")

            if report_holdings:
                self.data['holdings'] = {
                    'holdings': report_holdings,
                    'value': report_data.get('value', 0)
                }
                _LOGGER.debug(f"Using {len(report_holdings)} holdings from report data")
                _LOGGER.debug(f"Sample report holding keys: {list(report_holdings[0].keys())}")
            elif isinstance(holdings_from_api, dict) and 'error' not in holdings_from_api:
                # Fallback to dedicated endpoint data; ensure 'value' key exists
                api_holdings_list = holdings_from_api.get('holdings', [])
                if api_holdings_list:
                    total_val = sum(float(h.get('value', 0) or h.get('market_value', 0) or 0) for h in api_holdings_list)
                    self.data['holdings'] = {
                        'holdings': api_holdings_list,
                        'value': total_val or report_data.get('value', 0)
                    }
                else:
                    self.data['holdings'] = {'holdings': [], 'value': 0}
            else:
                self.data['holdings'] = {'holdings': [], 'value': 0}

            # Build income_report from payouts when available; otherwise fallback
            # to report payout gain.
            payouts_data = self.data.get("payouts", {})
            payouts = []
            if isinstance(payouts_data, dict):
                payouts = payouts_data.get("payouts", [])

            if payouts:
                self.data["income_report"] = {
                    "payouts": payouts,
                    "total_income": sum(
                        float(p.get("amount", 0) or 0) for p in payouts if isinstance(p, dict)
                    ),
                }
            else:
                self.data["income_report"] = {
                    "payout_gain": report_data.get("payout_gain"),
                    "payouts": [],
                }

            income_data = self.data.get("income_report", {})
            _LOGGER.debug(
                "Income report keys: %s",
                list(income_data.keys()) if isinstance(income_data, dict) else type(income_data),
            )

            # Build diversity from v2 diversity when available, otherwise fallback
            # from report sub_totals.

            diversity_v2 = self.data.get("diversity_v2", {})
            if isinstance(diversity_v2, dict) and "groups" in diversity_v2:
                breakdown: list[dict[str, Any]] = []
                for group_entry in diversity_v2.get("groups", []):
                    if not isinstance(group_entry, dict):
                        continue
                    for group_name, group_payload in group_entry.items():
                        if not isinstance(group_payload, dict):
                            continue
                        breakdown.append(
                            {
                                "group_name": group_name,
                                "percentage": group_payload.get("percentage"),
                                "value": group_payload.get("value"),
                            }
                        )
                self.data["diversity"] = {"breakdown": breakdown}
            else:
                sub_totals = report_data.get("sub_totals", [])
                if sub_totals:
                    total_value = float(report_data.get("value", 1) or 1)
                    breakdown = []
                    for st in sub_totals:
                        st_value = float(st.get("value", 0) or 0)
                        pct = (st_value / total_value * 100) if total_value else 0
                        breakdown.append(
                            {
                                "group_name": st.get("group_name", ""),
                                "percentage": round(pct, 2),
                                "value": st_value,
                            }
                        )
                    self.data["diversity"] = {"breakdown": breakdown}
                    _LOGGER.debug("Built diversity from %s sub_totals", len(sub_totals))
                else:
                    self.data["diversity"] = {"breakdown": []}

            diversity_data = self.data.get("diversity", {})
            _LOGGER.debug(
                "Diversity keys: %s",
                list(diversity_data.keys()) if isinstance(diversity_data, dict) else type(diversity_data),
            )

            _LOGGER.debug(f"Holdings count: {len(self.data.get('holdings', {}).get('holdings', []))}")
            _LOGGER.debug(f"Diversity breakdown count: {len(self.data.get('diversity', {}).get('breakdown', []))}")
            _LOGGER.debug(
                "Cash account transaction count: %s",
                len(self.data.get("cash_account_transactions", {}).get("cash_account_transactions", [])),
            )

            # Log trades data
            trades_data = self.data.get('trades', {})
            if trades_data and isinstance(trades_data, dict) and 'error' not in trades_data:
                trades_list = trades_data.get('trades', [])
                _LOGGER.debug(f"Trades count: {len(trades_list)}")
                if trades_list:
                    _LOGGER.debug(f"Sample trade keys: {list(trades_list[0].keys())}")
                    _LOGGER.debug(f"Sample trade data: {trades_list[0]}")
            else:
                _LOGGER.debug(f"Trades data unavailable or error: {type(trades_data)}")
                self.data['trades'] = {'trades': []}


            sofy_date, eofy_date = await get_financial_year_dates(
                self.data.get("portfolios", [{}])[0].get("financial_year_end")
            )
            if self.end_financial_year != eofy_date:
                self.end_financial_year = eofy_date
                self.start_financial_year = sofy_date

            return self.data

        except Exception as e:
            _LOGGER.error(f"Error in coordinator update: {e}", exc_info=True)
            raise UpdateFailed(f"Error fetching Sharesight data: {e}") from e
