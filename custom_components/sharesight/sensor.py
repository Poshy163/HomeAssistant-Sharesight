from homeassistant.helpers.entity import Entity
from . import DOMAIN
from .const import PORTFOLIO_ID, API_VERSION
import logging

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass, entry, async_add_entities):
    sharesight = hass.data[DOMAIN]
    async_add_entities([SharesightSensor(sharesight)], True)


class SharesightSensor(Entity):
    def __init__(self, sharesight):
        self._sharesight = sharesight
        self._state = None

    @property
    def name(self):
        return "Sharesight Portfolio Value"

    @property
    def state(self):
        return self._state

    async def async_update(self):
        _LOGGER.info(f"CALLING DATA")
        access_token = await self._sharesight.validate_token()
        _LOGGER.info(f"CODE IS: {access_token}")
        _LOGGER.info(f"PORTFOLIO ID IS: {PORTFOLIO_ID}")
        data = await self._sharesight.get_api_request(f"portfolios/{PORTFOLIO_ID}/valuation", API_VERSION, access_token)
        _LOGGER.info(f"DATA IS {data}")
        if data:
            port_value = data["value"]
            self._state = port_value
            _LOGGER.info(f"VALUE IS: {port_value}")
