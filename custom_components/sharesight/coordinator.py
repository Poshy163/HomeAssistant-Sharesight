import logging
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)

from .const import DOMAIN, SCAN_INTERVAL, API_VERSION, get_portfolio_id

_LOGGER = logging.getLogger(__name__)


async def merge_dicts(d1, d2):
    for key in d2:
        if key in d1 and isinstance(d1[key], dict) and isinstance(d2[key], dict):
            await merge_dicts(d1[key], d2[key])
        else:
            d1[key] = d2[key]
    return d1


class SharesightCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, client) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.sharesight = client
        self.update_method = self._async_update_data
        self.data: dict = {}

    async def _async_update_data(self):

        portfolioID = await get_portfolio_id()
        await self.sharesight.get_token_data()
        access_token = await self.sharesight.validate_token()
        combined_dict = {}

        v2_endpoint_list = [
            "portfolios",
            f"portfolios/{portfolioID}/performance",
            f"portfolios/{portfolioID}/valuation"
        ]
        try:
            for endpoint in v2_endpoint_list:
                _LOGGER.info(f"Calling {endpoint}")
                response = await self.sharesight.get_api_request(endpoint, API_VERSION, access_token)
                combined_dict = await merge_dicts(combined_dict, response)

            _LOGGER.info("DATA RECEIVED")
            self.data = combined_dict
            return self.data
        except Exception as e:
            _LOGGER.error(e)
            self.data = None
            return self.data
