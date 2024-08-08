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
        sensors.append(SharesightSensor(sensor, sharesight, entry, coordinator,
                                        local_currency, portfolio_id, edge))

    __index_market = 0
    for market in coordinator.data['sub_totals']:
        for market_sensor in MARKET_SENSOR_DESCRIPTIONS:
            market_sensor.name = f"{market['market']} value"
            market_sensor.key = f'sub_totals/{__index_market}/value'
            sensors.append(SharesightSensor(market_sensor, sharesight, entry, coordinator,
                                            local_currency, portfolio_id, edge))
            __index_market += 1

    __index_cash = 0
    for cash in coordinator.data['cash_accounts']:
        for cash_sensor in CASH_SENSOR_DESCRIPTIONS:
            cash_sensor.name = f"{cash['name']} cash balance"
            cash_sensor.key = f'cash_accounts/{__index_cash}/balance_in_portfolio_currency'
            sensors.append(SharesightSensor(cash_sensor, sharesight, entry, coordinator,
                                            local_currency, portfolio_id, edge))
            __index_cash += 1

    async_add_entities(sensors, True)
    return


class SharesightSensor(CoordinatorEntity, Entity):
    def __init__(self, sensor, sharesight, entry, coordinator, currency, portfolio_id, edge):
        super().__init__(coordinator)
        self._state_class = sensor.state_class
        self._coordinator = coordinator
        self._portfolioID = portfolio_id
        self._entity_category = sensor.entity_category
        self._name = f"{sensor.name}"
        self._edge = edge
        self._suggested_display_precision = sensor.suggested_display_precision
        self._key = sensor.key
        self._icon = sensor.icon
        self.datapoint = []
        self._entry = entry
        self._sharesight = sharesight
        self._device_class = sensor.device_class
        self._unique_id = f"{self._portfolioID}_{self._key}_{API_VERSION}"
        self.currency = currency

        if sensor.native_unit_of_measurement == CURRENCY_DOLLAR:
            self._native_unit_of_measurement = self.currency
        else:
            self._native_unit_of_measurement = sensor.native_unit_of_measurement

        if "sub_totals" in self._key:
            parts = self._key.split('/')
            self._state = self._coordinator.data[parts[0]][int(parts[1])][parts[2]]
            self.entity_id = f"sensor.{self._name.lower().replace(' ', '_')}_{self._portfolioID}"
            _LOGGER.info(f"NEW MARKET SENSOR WITH KEY: {[parts[0]]}{[int(parts[1])]}{[parts[2]]}")

        elif "cash_accounts" in self._key:
            parts = self._key.split('/')
            self._state = self._coordinator.data[parts[0]][int(parts[1])][parts[2]]
            self.entity_id = f"sensor.{self._name.lower().replace(' ', '_')}_{self._portfolioID}"
            _LOGGER.info(f"NEW CASH SENSOR WITH KEY: {[parts[0]]}{[int(parts[1])]}{[parts[2]]}")

        else:
            self.entity_id = f"sensor.{self._key}_{self._portfolioID}"
            self.datapoint.append(self._key)
            self._state = coordinator.data[self.datapoint[0]]
            _LOGGER.info(f"NEW SENSOR WITH KEY: {self.datapoint[0]}")

    @callback
    def _handle_coordinator_update(self):
        if "sub_totals" in self._key or "cash_accounts" in self._key:
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
    def icon(self):
        return self._icon

    @property
    def entity_category(self):
        return self._entity_category

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def unit_of_measurement(self):
        return self._native_unit_of_measurement

    @property
    def suggested_display_precision(self):
        return self._suggested_display_precision

    @property
    def state_class(self):
        return self._state_class

    @property
    def device_class(self):
        return self._device_class

    @property
    def device_info(self):
        if self._edge:
            return {
                "configuration_url": f"https://edge-portfolio.sharesight.com/portfolios/{self._portfolioID}",
                "identifiers": {(DOMAIN, self._portfolioID)},
                "name": f"Sharesight Edge Portfolio {self._portfolioID}",
                "model": f"Sharesight EDGE API {API_VERSION}",
                "entry_type": DeviceEntryType.SERVICE,
            }
        else:
            return {
                "configuration_url": f"https://portfolio.sharesight.com/portfolios/{self._portfolioID}",
                "identifiers": {(DOMAIN, self._portfolioID)},
                "name": f"Sharesight Portfolio {self._portfolioID}",
                "model": f"Sharesight API {API_VERSION}",
                "entry_type": DeviceEntryType.SERVICE,
            }
