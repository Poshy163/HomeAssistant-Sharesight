"""
Extensions to the SharesightAPI library to support additional endpoints.
These methods should be added to the SharesightAPI class.
"""


async def get_portfolio_holdings(self, portfolio_id):
    """Get all holdings in a portfolio with detailed information.

    Returns a dict with:
    - holdings: list of holdings with symbol, quantity, value, cost_base, gain, gain_percent
    - value: total portfolio value
    """
    return await self.get_api_request(['v3', f'portfolios/{portfolio_id}/holdings', None])


async def get_portfolio_income_report(self, portfolio_id):
    """Get income/dividend report for a portfolio.

    Returns a dict with:
    - total_income: total dividends received
    - franked_amount: franked dividends (AUS)
    - unfranked_amount: unfranked dividends
    - payouts: list of individual dividends with amount, date, instrument
    """
    return await self.get_api_request(['v3', f'portfolios/{portfolio_id}/income_report', None])


async def get_portfolio_diversity(self, portfolio_id):
    """Get portfolio diversity breakdown by market.

    Returns a dict with:
    - breakdown: list of markets with group_name, value, percentage
    - value: total portfolio value
    """
    return await self.get_api_request(['v3', f'portfolios/{portfolio_id}/diversity', None])

