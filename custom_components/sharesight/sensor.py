from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import Entity
from . import DOMAIN
from .const import API_VERSION, get_portfolio_id, PORTFOLIO_ID
import logging
from .enum import SENSOR_DESCRIPTIONS
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)

from .coordinator import SharesightCoordinator

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def merge_dicts(d1, d2):
    for key in d2:
        if key in d1 and isinstance(d1[key], dict) and isinstance(d2[key], dict):
            await merge_dicts(d1[key], d2[key])
        else:
            d1[key] = d2[key]
    return d1


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator: SharesightCoordinator = hass.data[DOMAIN][entry.entry_id]
    sharesight = hass.data[DOMAIN]
    sensors = []
    for sensor in SENSOR_DESCRIPTIONS:
        _LOGGER.info(f"PARSING VALUE: {sensor.key}")
        sensors.append(SharesightSensor(sharesight, entry, sensor.native_unit_of_measurement,
                                        sensor.device_class, sensor.name, sensor.key, sensor.state_class, coordinator))
    async_add_entities(sensors, True)

    return


async def get_port_id():
    return await get_portfolio_id()


class SharesightSensor(CoordinatorEntity, Entity):
    def __init__(self, sharesight, entry, native_unit_of_measurement, device_class, name, key, state, coordinator):
        super().__init__(coordinator)
        self._state = state
        self._coordinator = coordinator
        self.portfolioID = PORTFOLIO_ID
        self.datapoint = key
        _LOGGER.info(f"NEW SENSOR WITH KEY: {self.datapoint}")
        self.entry = entry
        self._sharesight = sharesight
        self._native_unit_of_measurement = native_unit_of_measurement
        self._device_class = device_class
        self._name = name
        self._unique_id = f"{self.portfolioID}_{key}_{API_VERSION}"
        self._entry_id = f"{self.portfolioID}_{key}"

    @callback
    def _handle_coordinator_update(self):
        self._state = self._coordinator.data[self.datapoint]
        self.async_write_ha_state()

    @property
    def name(self):
        return self._name

    @property
    def native_value(self):
        return self._coordinator.data[self.datapoint]

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
            "identifiers": {(DOMAIN, self.portfolioID)},
            "name": f"Sharesight Portfolio {self.portfolioID}",
            "model": f"Sharesight API {API_VERSION}",
            "entry_type": DeviceEntryType.SERVICE,
        }
