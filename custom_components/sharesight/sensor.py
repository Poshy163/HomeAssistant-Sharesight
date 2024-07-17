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
    sensors = [
        SharesightSensor(sharesight, entry, "valuation"),
        # Add more sensors as needed
    ]
    async_add_entities(sensors, True)

    # Set up a timer to update the sensor every 5 minutes


class SharesightSensor(Entity):
    def __init__(self, sharesight, entry, sensor_type):
        self._sharesight = sharesight
        self._state = None
        self._sensor_type = sensor_type
        self._name = "Portfolio value"
        self._unique_id = f"{PORTFOLIO_ID}_value"
        self._entry_id = entry.entry_id

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
        if self._sensor_type == "valuation":
            return CURRENCY_DOLLAR
        return None

    @property
    def device_class(self):
        if self._sensor_type == "valuation":
            return SensorDeviceClass.MONETARY
        return None

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, PORTFOLIO_ID)},
            "name": f"Sharesight Portfolio {PORTFOLIO_ID}",
            "model": f"Sharesight API {API_VERSION}",
            "entry_type": "service",
        }

    async def async_update(self):
        _LOGGER.info(f"CALLING DATA for {self._sensor_type}")
        access_token = await self._sharesight.validate_token()
        endpoint = f"portfolios/{PORTFOLIO_ID}/{self._sensor_type}"
        _LOGGER.info(f"CODE IS: {access_token}")
        _LOGGER.info(f"PORTFOLIO ID IS: {PORTFOLIO_ID}")
        data = await self._sharesight.get_api_request(endpoint, API_VERSION, access_token)
        _LOGGER.info(f"DATA IS {data}")
        if data:
            self._state = data.get("value", None)
            if self._state is not None:
                self._state = float(self._state)
                _LOGGER.info(f"VALUE IS: {self._state}")
            else:
                _LOGGER.warning("Value is None")
        else:
            _LOGGER.warning("No data received from Sharesight API")
