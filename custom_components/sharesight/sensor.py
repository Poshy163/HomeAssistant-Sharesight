from homeassistant.helpers.entity import Entity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)

from homeassistant.const import CURRENCY_DOLLAR
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
        self._name = f"{PORTFOLIO_ID} Portfolio Value"
        self._unique_id = f"{PORTFOLIO_ID}_portfolio_value"

    @property
    def name(self):
        return self._name

    @property
    def state(self):
        return self._state

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def unit_of_measurement(self):
        return CURRENCY_DOLLAR

    @property
    def device_class(self):
        return SensorDeviceClass.MONETARY

    async def async_update(self):
        _LOGGER.info(f"CALLING DATA")
        access_token = await self._sharesight.validate_token()
        _LOGGER.info(f"CODE IS: {access_token}")
        _LOGGER.info(f"PORTFOLIO ID IS: {PORTFOLIO_ID}")
        data = await self._sharesight.get_api_request(f"portfolios/{PORTFOLIO_ID}/valuation", API_VERSION, access_token)
        _LOGGER.info(f"DATA IS {data}")
        if data:
            port_value = data.get("value")
            if port_value is not None:
                self._state = float(port_value)
                _LOGGER.info(f"VALUE IS: {self._state}")
            else:
                _LOGGER.warning("Value is None")
        else:
            _LOGGER.warning("No data received from Sharesight API")
