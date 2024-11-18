from homeassistant.const import CURRENCY_DOLLAR
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity
from .const import APP_VERSION, DOMAIN, UPDATE_SENSOR_SCAN_INTERVAL
import logging
from .enum import SENSOR_DESCRIPTIONS, MARKET_SENSOR_DESCRIPTIONS, CASH_SENSOR_DESCRIPTIONS
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .coordinator import SharesightCoordinator
from homeassistant.helpers.event import async_track_time_interval

_LOGGER: logging.Logger = logging.getLogger(__package__)

MARKET_SENSORS = []
CASH_SENSORS = []


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]

    coordinator: SharesightCoordinator = data["coordinator"]
    portfolio_id = data["portfolio_id"]
    edge = data["edge"]
    local_currency = coordinator.data['portfolios'][0]['currency_code']

    sensors = []

    for sensor in SENSOR_DESCRIPTIONS:
        sensors.append(SharesightSensor(sensor, entry, coordinator,
                                        local_currency, portfolio_id, edge))

    __index_market = 0
    for market in coordinator.data['report']['sub_totals']:
        for market_sensor in MARKET_SENSOR_DESCRIPTIONS:
            local_name = market['group_name']
            market_sensor.name = f"{local_name} value"
            new_sensor = SharesightSensor(market_sensor, entry, coordinator,
                                          local_currency, portfolio_id, edge, __index_market, local_name)
            sensors.append(new_sensor)
            MARKET_SENSORS.append(market_sensor.name)
            __index_market += 1

    __index_cash = 0
    for cash in coordinator.data['report']['cash_accounts']:
        for cash_sensor in CASH_SENSOR_DESCRIPTIONS:
            local_name = cash['name']
            cash_sensor.name = f"{local_name} cash balance"
            new_sensor = SharesightSensor(cash_sensor, entry, coordinator,
                                          local_currency, portfolio_id, edge, __index_cash, local_name)
            sensors.append(new_sensor)
            CASH_SENSORS.append(cash_sensor.name)
            __index_cash += 1

    async_add_entities(sensors, True)

    async def update_sensors(_):
        _LOGGER.info(f"CHECKING FOR NEW MARKET/CASH SENSORS")
        update_data = hass.data[DOMAIN][entry.entry_id]
        update_coordinator: SharesightCoordinator = update_data["coordinator"]
        __update_index_market = 0

        for update_market in update_coordinator.data['report']['sub_totals']:
            for update_market_sensor in MARKET_SENSOR_DESCRIPTIONS:
                __local_name = update_market['group_name']
                update_market_sensor.name = f"{__local_name} value"
                if update_market_sensor.name not in MARKET_SENSORS:
                    local_market_currency = coordinator.data['portfolios'][0]['currency_code']
                    update_new_sensor = SharesightSensor(update_market_sensor, entry, update_coordinator,
                                                         local_market_currency, portfolio_id, edge,
                                                         __update_index_market, __local_name)
                    async_add_entities([update_new_sensor], True)
                    MARKET_SENSORS.append(update_market_sensor.name)
            __update_index_market += 1

        __update_index_cash = 0

        for update_cash in update_coordinator.data['report']['cash_accounts']:
            for update_cash_sensor in CASH_SENSOR_DESCRIPTIONS:
                __local_name = update_cash['name']
                update_cash_sensor.name = f"{__local_name} cash balance"
                if update_cash_sensor.name not in CASH_SENSORS:
                    local_cash_currency = coordinator.data['portfolios'][0]['currency_code']
                    update_new_sensor = SharesightSensor(update_cash_sensor, entry, update_coordinator,
                                                         local_cash_currency, portfolio_id, edge, __update_index_cash,
                                                         __local_name)
                    CASH_SENSORS.append(update_cash_sensor.name)
                    async_add_entities([update_new_sensor], True)
            __update_index_cash += 1

    async_track_time_interval(hass, update_sensors, UPDATE_SENSOR_SCAN_INTERVAL)


class SharesightSensor(CoordinatorEntity, Entity):
    def __init__(self, sensor, entry, coordinator, currency, portfolio_id, edge, index=0, local_name=""):
        super().__init__(coordinator)
        self._state_class = sensor.state_class
        self._coordinator = coordinator
        self._portfolioID = portfolio_id
        self._entity_category = sensor.entity_category
        self._name = str(sensor.name)
        self._extension_key = sensor.extension_key
        self._index = index
        self._suggested_display_precision = sensor.suggested_display_precision
        self._key = sensor.key
        self._icon = sensor.icon
        self._entry = entry
        self._device_class = sensor.device_class
        self._sub_key = sensor.sub_key

        if sensor.native_unit_of_measurement == CURRENCY_DOLLAR:
            self._native_unit_of_measurement = currency
        else:
            self._native_unit_of_measurement = sensor.native_unit_of_measurement

        base_entity_id = f"{self._name.lower().replace(' ', '_')}_{self._portfolioID}"
        self.entity_id = f"sensor.{base_entity_id}"

        if edge:
            edge_name = " Edge "
            edge_url = "edge-"
        else:
            edge_name = " "
            edge_url = ""

        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, self._portfolioID)},
            configuration_url=f"https://{edge_url}portfolio.sharesight.com/portfolios/{self._portfolioID}",
            model=f"Sharesight{edge_name}API",
            name=f"Sharesight{edge_name}Portfolio {self._portfolioID}")

        try:
            if self._extension_key == "Extention":
                self._state = self._coordinator.data[self._sub_key][self._key]
                self._unique_id = f"{self._portfolioID}_{self._sub_key}_{self._key}_{APP_VERSION}"
            elif self._sub_key == "report" and self._key != "sub_totals" or self._sub_key == "report" and self._key != "cash_accounts":
                self._state = self._coordinator.data[self._sub_key][self._key]
                self._unique_id = f"{self._portfolioID}_{self._key}_{APP_VERSION}"
            elif self._key == "user_id":
                self._state = self._coordinator.data[self._sub_key][0][self._key]
                self._unique_id = f"{self._portfolioID}_{self._key}_{APP_VERSION}"
            elif "sub_totals" in self._key or "cash_accounts" in self._key:
                self._state = self._coordinator.data['report'][self._key][self._index][self._sub_key]
                self._unique_id = f"{self._portfolioID}_{local_name}_VALUE_{APP_VERSION}"

        except ValueError as e:
            _LOGGER.error(f"KeyError accessing data for key '{self._key}': {e}")
            self._state = None
        except IndexError as e:
            _LOGGER.error(f"IndexError accessing data for key '{self._key}': {e}")
            self._state = None

    @callback
    def _handle_coordinator_update(self):
        try:

            if self._extension_key == "Extention":
                # Used for one-day, one-week and current financial year
                self._state = self._coordinator.data[self._sub_key][self._key]
            elif self._sub_key == "report" and self._key != "sub_totals" or self._sub_key == "report" and self._key != "cash_accounts":
                # Used for cash accounts or sub_totals from the report API
                self._state = self._coordinator.data[self._sub_key][self._key]
            elif self._key == "user_id":
                # Used to get the userID
                self._state = self._coordinator.data[self._sub_key][0][self._key]
            elif "sub_totals" in self._key or "cash_accounts" in self._key:
                # Used for cash accounts or market data
                self._state = self._coordinator.data['report'][self._key][self._index][self._sub_key]

        except (KeyError, IndexError) as e:
            _LOGGER.error(f"Error accessing data for key '{self._key}': {e}: Defaulting to None")
            self._state = None
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
