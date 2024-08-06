from homeassistant.const import CURRENCY_DOLLAR
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import Entity
from .const import API_VERSION, DOMAIN
import logging
from .enum import SENSOR_DESCRIPTIONS, MARKET_SENSOR_DESCRIPTIONS, CASH_SENSOR_DESCRIPTIONS
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .coordinator import SharesightCoordinator

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator: SharesightCoordinator = hass.data[DOMAIN][entry.entry_id]
    sharesight = hass.data[DOMAIN]
    portfolio_id = hass.data[DOMAIN]["portfolio_id"]
    local_currency = coordinator.data['portfolios'][0]['currency_code']
    edge = hass.data[DOMAIN]["edge"]
    sensors = []
    for sensor in SENSOR_DESCRIPTIONS:
        sensors.append(SharesightSensor(sharesight, entry, sensor.native_unit_of_measurement,
                                        sensor.device_class, sensor.name, sensor.key, sensor.state_class, coordinator,
                                        local_currency, portfolio_id, sensor.icon, edge))

    __index_market = 0
    for market in coordinator.data['sub_totals']:
        for market_sensor in MARKET_SENSOR_DESCRIPTIONS:
            market_sensor.name = f"{market['market']} value"
            market_sensor.key = f'sub_totals/{__index_market}/value'
            sensors.append(SharesightSensor(sharesight, entry, market_sensor.native_unit_of_measurement,
                                            market_sensor.device_class, market_sensor.name, market_sensor.key,
                                            market_sensor.state_class,
                                            coordinator,
                                            local_currency, portfolio_id, market_sensor.icon, edge))
            __index_market += 1

    __index_cash = 0
    for cash in coordinator.data['cash_accounts']:
        for cash_sensor in CASH_SENSOR_DESCRIPTIONS:
            cash_sensor.name = f"{cash['name']} cash balance"
            cash_sensor.key = f'cash_accounts/{__index_cash}/balance_in_portfolio_currency'
            sensors.append(SharesightSensor(sharesight, entry, cash_sensor.native_unit_of_measurement,
                                            cash_sensor.device_class, cash_sensor.name, cash_sensor.key,
                                            cash_sensor.state_class,
                                            coordinator,
                                            local_currency, portfolio_id, cash_sensor.icon, edge))
            __index_cash += 1

    async_add_entities(sensors, True)
    return


class SharesightSensor(CoordinatorEntity, Entity):
    def __init__(self, sharesight, entry, native_unit_of_measurement, device_class, name, key, state, coordinator,
                 currency, portfolio_id, icon, edge):
        super().__init__(coordinator)
        self._state = state
        self._coordinator = coordinator
        self.portfolioID = portfolio_id
        self.datapoint = []
        self._name = f"{name}"
        self._edge = edge
        self._key = key
        self._icon = icon

        if "sub_totals" in self._key and "value" in self._key:
            parts = self._key.split('/')
            self._state = self._coordinator.data[parts[0]][int(parts[1])][parts[2]]
            self.entity_id = f"sensor.{name.lower().replace(' ', '_')}_{self.portfolioID}"
            _LOGGER.info(f"NEW MARKET SENSOR WITH KEY: {[parts[0]]}{[int(parts[1])]}{[parts[2]]}")
        elif "cash_accounts" in self._key and "balance" in self._key:
            parts = self._key.split('/')
            self._state = self._coordinator.data[parts[0]][int(parts[1])][parts[2]]
            self.entity_id = f"sensor.{name.lower().replace(' ', '_')}_{self.portfolioID}"
            _LOGGER.info(f"NEW CASH SENSOR WITH KEY: {[parts[0]]}{[int(parts[1])]}{[parts[2]]}")
        else:
            self.entity_id = f"sensor.{key}_{self.portfolioID}"
            self.datapoint.append(key)
            self._state = coordinator.data[self.datapoint[0]]
            _LOGGER.info(f"NEW SENSOR WITH KEY: {self.datapoint[0]}")

        self.entry = entry
        self._sharesight = sharesight
        self._native_unit_of_measurement = native_unit_of_measurement
        self._device_class = device_class
        self._unique_id = f"{self.portfolioID}_{key}_{API_VERSION}"
        self.currency = currency

    @callback
    def _handle_coordinator_update(self):
        if "sub_totals" in self._key and "value" in self._key or "cash_accounts" in self._key and "value" in self._key:
            parts = self._key.split('/')
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
        if self._edge:
            return {
                "configuration_url": f"https://edge-portfolio.sharesight.com/portfolios/{self.portfolioID}",
                "identifiers": {(DOMAIN, self.portfolioID)},
                "name": f"Sharesight Edge Portfolio {self.portfolioID}",
                "model": f"Sharesight EDGE API {API_VERSION}",
                "entry_type": DeviceEntryType.SERVICE,
            }
        else:
            return {
                "configuration_url": f"https://portfolio.sharesight.com/portfolios/{self.portfolioID}",
                "identifiers": {(DOMAIN, self.portfolioID)},
                "name": f"Sharesight Portfolio {self.portfolioID}",
                "model": f"Sharesight API {API_VERSION}",
                "entry_type": DeviceEntryType.SERVICE,
            }

    @property
    def icon(self):
        return self._icon
