from homeassistant.const import CURRENCY_DOLLAR
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import Entity

from . import DOMAIN
from .const import API_VERSION, get_portfolio_id, PORTFOLIO_ID
import logging
from .enum import SENSOR_DESCRIPTIONS, MARKET_SENSOR_DESCRIPTIONS
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)

from .coordinator import SharesightCoordinator

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator: SharesightCoordinator = hass.data[DOMAIN][entry.entry_id]
    sharesight = hass.data[DOMAIN]
    local_currency = coordinator.data['portfolios'][0]['currency_code']
    markets = coordinator.data['sub_totals']
    sensors = []
    for sensor in SENSOR_DESCRIPTIONS:
        sensors.append(SharesightSensor(sharesight, entry, sensor.native_unit_of_measurement,
                                        sensor.device_class, sensor.name, sensor.key, sensor.state_class, coordinator,
                                        local_currency))

    market_codes = []
    index = 0
    for market in markets:
        for market_sensor in MARKET_SENSOR_DESCRIPTIONS:
            market_codes.append(market['market'])
            market_sensor.name = f"{market['market']} value"
            market_sensor.key = f'sub_totals/{index}/value'
            sensors.append(SharesightSensor(sharesight, entry, market_sensor.native_unit_of_measurement,
                                            market_sensor.device_class, market_sensor.name, market_sensor.key,
                                            market_sensor.state_class,
                                            coordinator,
                                            local_currency))
            index += 1

    async_add_entities(sensors, True)

    return


async def get_port_id():
    return await get_portfolio_id()


class SharesightSensor(CoordinatorEntity, Entity):
    def __init__(self, sharesight, entry, native_unit_of_measurement, device_class, name, key, state, coordinator,
                 currency):
        super().__init__(coordinator)
        self._state = state
        self._coordinator = coordinator
        self.portfolioID = PORTFOLIO_ID
        self.datapoint = []
        self.key = key

        if "sub_totals" in self.key and "value" in self.key:
            parts = self.key.split('/')
            self._state = self._coordinator.data[parts[0]][int(parts[1])][parts[2]]
            _LOGGER.info(f"NEW SENSOR WITH KEY: {[parts[0]]}{[int(parts[1])]}{[parts[2]]}")
        else:
            self.datapoint.append(key)
            self._state = coordinator.data[self.datapoint[0]]
            _LOGGER.info(f"NEW SENSOR WITH KEY: {self.datapoint[0]}")

        self.entry = entry
        self._sharesight = sharesight
        self._native_unit_of_measurement = native_unit_of_measurement
        self._device_class = device_class
        self._name = name
        self._unique_id = f"{self.portfolioID}_{key}_{API_VERSION}"
        self._entry_id = f"{key}_{self.portfolioID}"
        self.currency = currency

    @callback
    def _handle_coordinator_update(self):
        if "sub_totals" in self.key and "value" in self.key:
            parts = self.key.split('/')
            self._state = self._coordinator.data[parts[0]][int(parts[1])][parts[2]]
        else:
            self._state = self._coordinator.data[self.datapoint[0]]
        self.async_write_ha_state()

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
        if self._native_unit_of_measurement == CURRENCY_DOLLAR:
            self._native_unit_of_measurement = self.currency
            return self._native_unit_of_measurement
        else:
            return self._native_unit_of_measurement

    @property
    def device_class(self):
        return self._device_class

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.portfolioID)},
            "name": f"Sharesight Portfolio {self.portfolioID}",
            "model": f"Sharesight API {API_VERSION}",
            "entry_type": DeviceEntryType.SERVICE,
        }
