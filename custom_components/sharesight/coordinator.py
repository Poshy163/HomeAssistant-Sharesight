import logging
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
            ["v3", f"portfolios/{self.portfolio_id}/performance", None, False],
        ]

        # Optional endpoints that may fail (premium features or different API plans)
        optional_endpoint_list = [
            ["v3", f"portfolios/{self.portfolio_id}/holdings", None, "holdings"],
            ["v3", f"portfolios/{self.portfolio_id}/income_report", None, "income_report"],
            ["v3", f"portfolios/{self.portfolio_id}/diversity", None, "diversity"],
            ["v3", f"portfolios/{self.portfolio_id}/trades", None, "trades"],
            ["v3", f"portfolios/{self.portfolio_id}/contributions", None, "contributions"],
        ]

        try:
            for endpoint in endpoint_list:
                _LOGGER.debug(f"Calling {endpoint}")
                response = await self.sharesight.get_api_request(endpoint, access_token)
                _LOGGER.debug(f"Response for {endpoint[1]}: {list(response.keys()) if isinstance(response, dict) else type(response)}")
                extension = endpoint[3]

                if extension:
                    response = {extension: response}

                combined_dict = await merge_dicts(combined_dict, response)

            # Try optional endpoints - don't fail if they error
            for endpoint in optional_endpoint_list:
                endpoint_path = endpoint[1]
                if endpoint_path in self._failed_optional_endpoints:
                    _LOGGER.debug(f"Skipping previously failed optional endpoint {endpoint_path}")
                    continue
                try:
                    _LOGGER.debug(f"Calling optional endpoint {endpoint}")
                    response = await self.sharesight.get_api_request(endpoint, access_token)
                    extension = endpoint[3]

                    # Check if the response is an error or invalid
                    if response is None:
                        _LOGGER.info(f"Optional endpoint {endpoint_path} returned None, skipping")
                        self._failed_optional_endpoints.add(endpoint_path)
                        continue
                    if not isinstance(response, dict):
                        _LOGGER.info(f"Optional endpoint {endpoint_path} returned non-dict: {type(response)}, skipping")
                        self._failed_optional_endpoints.add(endpoint_path)
                        continue
                    if 'error' in response:
                        _LOGGER.info(f"Optional endpoint {endpoint_path} returned error: {response.get('error')}, skipping future calls")
                        self._failed_optional_endpoints.add(endpoint_path)
                        continue

                    if extension:
                        response = {extension: response}

                    combined_dict = await merge_dicts(combined_dict, response)
                except Exception as e:
                    _LOGGER.info(f"Optional endpoint {endpoint_path} failed: {e}, skipping future calls")
                    self._failed_optional_endpoints.add(endpoint_path)

            self.data = combined_dict
            _LOGGER.debug(f"Data keys available: {list(self.data.keys())}")

            report_data = self.data.get('report', {})
            report_holdings = report_data.get('holdings', [])
            _LOGGER.debug(f"Report keys: {list(report_data.keys())}")

            sub_totals = report_data.get('sub_totals', [])
            if sub_totals:
                _LOGGER.debug(f"Sub totals count: {len(sub_totals)}, sample keys: {list(sub_totals[0].keys())}")
                _LOGGER.debug(f"Sub totals sample data: {sub_totals[0]}")

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

            # If income_report failed, build what we can from report data
            income_data = self.data.get('income_report', {})
            _LOGGER.debug(f"Income report keys: {list(income_data.keys()) if isinstance(income_data, dict) else type(income_data)}")
            if not income_data or 'error' in income_data:
                self.data['income_report'] = {
                    'payout_gain': report_data.get('payout_gain'),
                }

            # If diversity failed, build from report sub_totals
            diversity_data = self.data.get('diversity', {})
            _LOGGER.debug(f"Diversity keys: {list(diversity_data.keys()) if isinstance(diversity_data, dict) else type(diversity_data)}")
            if not diversity_data or 'error' in diversity_data:
                sub_totals = report_data.get('sub_totals', [])
                if sub_totals:
                    total_value = float(report_data.get('value', 1) or 1)
                    breakdown = []
                    for st in sub_totals:
                        st_value = float(st.get('value', 0) or 0)
                        pct = (st_value / total_value * 100) if total_value else 0
                        breakdown.append({
                            'group_name': st.get('group_name', ''),
                            'percentage': round(pct, 2),
                            'value': st_value
                        })
                    self.data['diversity'] = {'breakdown': breakdown}
                    _LOGGER.debug(f"Built diversity from {len(sub_totals)} sub_totals")
                else:
                    self.data['diversity'] = {'breakdown': []}

            _LOGGER.debug(f"Holdings count: {len(self.data.get('holdings', {}).get('holdings', []))}")
            _LOGGER.debug(f"Diversity breakdown count: {len(self.data.get('diversity', {}).get('breakdown', []))}")

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

            # Log contributions data
            contributions_data = self.data.get('contributions', {})
            if contributions_data and isinstance(contributions_data, dict) and 'error' not in contributions_data:
                contributions_list = contributions_data.get('contributions', [])
                _LOGGER.debug(f"Contributions count: {len(contributions_list)}")
                if contributions_list:
                    _LOGGER.debug(f"Sample contribution keys: {list(contributions_list[0].keys())}")
                    _LOGGER.debug(f"Sample contribution data: {contributions_list[0]}")
            else:
                _LOGGER.debug(f"Contributions data unavailable or error: {type(contributions_data)}")
                self.data['contributions'] = {'contributions': []}

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
