from homeassistant.components.number import NumberDeviceClass
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import Entity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import CURRENCY_DOLLAR, PERCENTAGE
from . import DOMAIN
from .const import PORTFOLIO_ID, API_VERSION
from datetime import timedelta
import logging
import asyncio
from .enum import SENSOR_DESCRIPTIONS

_LOGGER: logging.Logger = logging.getLogger(__package__)

SCAN_INTERVAL = timedelta(minutes=5)

endpoint_list_version = "v2"


async def merge_dicts(d1, d2):
    for key in d2:
        if key in d1 and isinstance(d1[key], dict) and isinstance(d2[key], dict):
            await merge_dicts(d1[key], d2[key])
        else:
            d1[key] = d2[key]
    return d1


async def fetch_and_update_data(hass, sharesight, entry, sensors):
    while True:
        await sharesight.get_token_data()
        access_token = await sharesight.validate_token()
        try:
            _LOGGER.info(f"CALLED FROM: update loop")
            _LOGGER.info(f"ACCESS CODE IS: {access_token}")
            _LOGGER.info(f"PORTFOLIO ID IS: {PORTFOLIO_ID}")
            v2_endpoint_list = ["portfolios", "groups", f"portfolios/{PORTFOLIO_ID}/performance",
                                f"portfolios/{PORTFOLIO_ID}/valuation", "memberships",
                                f"portfolios/{PORTFOLIO_ID}/trades", f"portfolios/{PORTFOLIO_ID}/payouts",
                                "cash_accounts",
                                "user_instruments", "currencies", "my_user.json"]
            combined_dict = {}
            for endpoint in v2_endpoint_list:
                _LOGGER.info(f"Calling {endpoint}")
                response = await sharesight.get_api_request(endpoint, API_VERSION, access_token)
                combined_dict = await merge_dicts(combined_dict, response)

            _LOGGER.info(f"DATA RECEIVED")
            data = combined_dict
        except Exception as e:
            _LOGGER.error(e)
            data = None

        for sensor in sensors:
            sensor.update_data(data)

        await asyncio.sleep(SCAN_INTERVAL.total_seconds())


async def async_setup_entry(hass, entry, async_add_entities):
    sharesight = hass.data[DOMAIN]
    _LOGGER.info(f"GETTING INITIAL DATA")

    await sharesight.get_token_data()
    access_token = await sharesight.validate_token()
    try:
        _LOGGER.info(f"CALLED FROM: STARTUP BOOT")
        _LOGGER.info(f"ACCESS CODE IS: {access_token}")
        _LOGGER.info(f"PORTFOLIO ID IS: {PORTFOLIO_ID}")
        v2_endpoint_list = ["portfolios", "groups", f"portfolios/{PORTFOLIO_ID}/performance",
                            f"portfolios/{PORTFOLIO_ID}/valuation", "memberships",
                            f"portfolios/{PORTFOLIO_ID}/trades", f"portfolios/{PORTFOLIO_ID}/payouts",
                            "cash_accounts",
                            "user_instruments", "currencies", "my_user.json"]
        combined_dict = {}
        for endpoint in v2_endpoint_list:
            _LOGGER.info(f"Calling {endpoint}")
            response = await sharesight.get_api_request(endpoint, API_VERSION, access_token)
            combined_dict = await merge_dicts(combined_dict, response)

        _LOGGER.info(f"GETTING INITIAL DATA - COMPLETE")
        data = combined_dict

    except Exception as e:
        _LOGGER.error(f"GETTING INITIAL DATA - FAILED")
        _LOGGER.error(e)
        data = None

    sensors = []

    for sensor in SENSOR_DESCRIPTIONS:
        sensors.append(SharesightSensor(sharesight, entry, sensor.native_unit_of_measurement, data, sensor.device_class, sensor.name, sensor.key))

    async_add_entities(sensors, True)

    hass.loop.create_task(fetch_and_update_data(hass, sharesight, entry, sensors))


class SharesightSensor(Entity):
    def __init__(self, sharesight, entry, native_unit_of_measurement, data, device_class, name, key):
        self.datapoint = key
        self.data = data
        self.entry = entry
        self._sharesight = sharesight
        self._state = None
        self._native_unit_of_measurement = native_unit_of_measurement
        self._device_class = device_class
        self._name = name
        self._unique_id = f"{PORTFOLIO_ID}_{key}_{API_VERSION}"
        self._entry_id = f"{PORTFOLIO_ID}_{key}"

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
        return self._native_unit_of_measurement

    @property
    def device_class(self):
        return self._device_class

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, PORTFOLIO_ID)},
            "name": f"Sharesight Portfolio {PORTFOLIO_ID}",
            "model": f"Sharesight API {API_VERSION}",
            "entry_type": DeviceEntryType.SERVICE,
        }

    def update_data(self, data):
        self.data = data
        if self.data:
            self._state = self.data.get(self.datapoint, None)
            if self._state is not None:
                self._state = float(self._state)
            else:
                _LOGGER.warning(f"Requested value for '{self.datapoint}' is None")
        else:
            _LOGGER.warning("No data received from Sharesight API")
        self.async_write_ha_state()



