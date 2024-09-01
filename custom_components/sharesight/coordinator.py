import logging
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)

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


class SharesightCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, portfolio_id, client) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.sharesight = client
        self.update_method = self._async_update_data
        self.data: dict = {}
        self.portfolioID = portfolio_id

    async def _async_update_data(self):
        await self.sharesight.get_token_data()
        access_token = await self.sharesight.validate_token()
        combined_dict = {}

        endpoint_list = [
            ["v3", "portfolios"],
            ["v3", f"portfolios/{self.portfolioID}/performance"]
        ]
        try:
            for endpoint in endpoint_list:
                _LOGGER.info(f"Calling {endpoint}")
                response = await self.sharesight.get_api_request(endpoint, access_token)
                combined_dict = await merge_dicts(combined_dict, response)

            self.data = combined_dict
            _LOGGER.info(f"DATA RECEIVED IT IS: {self.data}")
            return self.data
        except Exception as e:
            _LOGGER.error(e)
            self.data = None
            return self.data
