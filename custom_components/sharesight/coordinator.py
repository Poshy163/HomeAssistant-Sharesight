import logging
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)
from datetime import date, timedelta, datetime
from typing import Dict, Any
import itertools

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


def get_financial_year_dates(end_date_str):
    end_date = datetime.strptime(end_date_str, "%m-%d")
    today = datetime.today()
    end_year = today.year if today.month <= 6 else today.year + 1
    end_date = end_date.replace(year=end_year)
    start_date = end_date.replace(year=end_year - 1) + timedelta(days=1)

    return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")


class SharesightCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, portfolio_id, client) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.sharesight = client
        self.update_method = self._async_update_data
        self.data: dict = {}
        self.portfolioID = portfolio_id
        self.startup_endpoint = ["v3", f"portfolios/{self.portfolioID}", None, False]
        self.started_up = False

    async def _async_update_data(self):
        await self.sharesight.get_token_data()
        access_token = await self.sharesight.validate_token()
        combined_dict = {}

        if self.started_up is False:
            local_data = await self.sharesight.get_api_request(self.startup_endpoint, access_token)
            self.start_financial_year, self.end_financial_year = get_financial_year_dates(
                local_data.get('portfolio', {}).get('financial_year_end'))
            self.started_up = True

        self.current_date = date.today()

        self.start_of_week = (self.current_date - timedelta(days=self.current_date.weekday())).strftime('%Y-%m-%d')
        self.end_of_week = (self.current_date + timedelta(days=6 - self.current_date.weekday())).strftime('%Y-%m-%d')

        endpoint_list = [
            ["v2", f"portfolios/{self.portfolioID}/performance",
             {'start_date': f"{self.current_date}", 'end_date': f"{self.current_date}"}, 'one-day'],
            ["v2", f"portfolios/{self.portfolioID}/performance",
             {'start_date': f"{self.start_of_week}", 'end_date': f"{self.end_of_week}"}, 'one-week'],
            ["v2", f"portfolios/{self.portfolioID}/performance",
             {'start_date': f"{self.start_financial_year}", 'end_date': f"{self.end_financial_year}"}, 'financial-year'],
            ["v3", "portfolios", None, False],
            ["v3", f"portfolios/{self.portfolioID}/performance", None, False],
        ]

        try:
            for endpoint in endpoint_list:
                _LOGGER.info(f"Calling {endpoint}")
                response = await self.sharesight.get_api_request(endpoint, access_token)
                _LOGGER.info(f"RESPONSE IS: {response}")
                extension = endpoint[3]

                if extension is not False:
                    response = {
                        extension: response
                    }

                combined_dict = await merge_dicts(combined_dict, response)

            self.data = combined_dict
            _LOGGER.info(f"DATA RECEIVED: {self.data}")

            SOFY_DATE, EOFY_DATE = get_financial_year_dates(self.data.get('portfolios', [{}])[0].get('financial_year_end'))
            if self.end_financial_year != EOFY_DATE:
                self.end_financial_year = EOFY_DATE
                self.start_financial_year = SOFY_DATE

            return self.data
        except Exception as e:
            _LOGGER.error(e)
            self.data = None
            return self.data
