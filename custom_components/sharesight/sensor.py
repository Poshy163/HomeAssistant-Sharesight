from homeassistant.const import CURRENCY_DOLLAR
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.components.sensor import (
    SensorEntity
)
from .const import APP_VERSION, DOMAIN, UPDATE_SENSOR_SCAN_INTERVAL
import logging
from .enum import SENSOR_DESCRIPTIONS, MARKET_SENSOR_DESCRIPTIONS, CASH_SENSOR_DESCRIPTIONS
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .coordinator import SharesightCoordinator
from homeassistant.helpers.event import async_track_time_interval

_LOGGER: logging.Logger = logging.getLogger(__package__)

MARKET_SENSORS = []
CASH_SENSORS = []


def _get_holding_value(h):
    """Get the market value of a holding, trying multiple field names."""
    for field in ('value', 'market_value', 'total_value', 'current_value', 'last_value'):
        val = h.get(field)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                continue
    return 0.0


def _get_holding_gain(h):
    """Get the gain of a holding, trying multiple field names."""
    for field in ('capital_gain', 'gain', 'total_gain', 'unrealised_gain'):
        val = h.get(field)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                continue
    return 0.0


def _get_holding_gain_percent(h):
    """Get the gain percent of a holding, trying multiple field names."""
    for field in ('capital_gain_percent', 'gain_percent', 'total_gain_percent'):
        val = h.get(field)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                continue
    return None


def _get_holding_symbol(h):
    """Get the symbol/code of a holding."""
    return (
        h.get('symbol')
        or h.get('code')
        or h.get('instrument_code')
        or (h.get('instrument', {}) or {}).get('code', '')
        or (h.get('instrument', {}) or {}).get('symbol', '')
        or ''
    )


def _get_largest_holding(holdings_data):
    """Get the largest holding by value."""
    if not holdings_data:
        _LOGGER.debug("holdings_data is empty")
        return None

    holdings = holdings_data.get('holdings', [])
    if not holdings:
        _LOGGER.debug(f"No holdings in holdings_data. Keys: {list(holdings_data.keys())}")
        return None

    try:
        # Log sample holding keys for debugging
        if holdings:
            _LOGGER.debug(f"Sample holding keys: {list(holdings[0].keys())}")

        largest = max(holdings, key=_get_holding_value)
        portfolio_value = float(holdings_data.get('value', 0) or 1)
        largest_value = _get_holding_value(largest)
        percent = (largest_value / portfolio_value * 100) if portfolio_value else 0
        symbol = _get_holding_symbol(largest)
        _LOGGER.debug(f"Found largest holding: {symbol} with value {largest_value}")
        return {
            'symbol': symbol,
            'value': largest_value,
            'percent': round(percent, 2)
        }
    except (ValueError, TypeError, KeyError) as e:
        _LOGGER.debug(f"Error in _get_largest_holding: {e}, first holding sample: {holdings[0] if holdings else 'no holdings'}")
        return None


def _get_top_gain_holding(holdings_data):
    """Get the holding with the highest gain (by amount)."""
    if not holdings_data:
        return None

    holdings = holdings_data.get('holdings', [])
    if not holdings:
        return None

    try:
        top = max(holdings, key=_get_holding_gain)
        symbol = _get_holding_symbol(top)
        gain_pct = _get_holding_gain_percent(top)
        return {
            'symbol': symbol,
            'amount': _get_holding_gain(top),
            'percent': gain_pct
        }
    except (ValueError, TypeError, KeyError) as e:
        _LOGGER.debug(f"Error in _get_top_gain_holding: {e}")
        return None


def _get_worst_gain_holding(holdings_data):
    """Get the holding with the lowest gain (by amount)."""
    if not holdings_data:
        return None

    holdings = holdings_data.get('holdings', [])
    if not holdings:
        return None

    try:
        worst = min(holdings, key=_get_holding_gain)
        symbol = _get_holding_symbol(worst)
        gain_pct = _get_holding_gain_percent(worst)
        return {
            'symbol': symbol,
            'amount': _get_holding_gain(worst),
            'percent': gain_pct
        }
    except (ValueError, TypeError, KeyError) as e:
        _LOGGER.debug(f"Error in _get_worst_gain_holding: {e}")
        return None


def _get_income_summary(income_data, report_data=None):
    """Get income report summary."""
    # First try from dedicated income_report data (full API response)
    if income_data and 'error' not in income_data:
        try:
            # Use explicit None checks instead of 'or' chains (0 is a valid value)
            total_income = None
            for field in ('total_income', 'total', 'total_dividend', 'payout_gain'):
                val = income_data.get(field)
                if val is not None:
                    total_income = val
                    break

            payouts = income_data.get('payouts', [])

            if total_income is None and payouts:
                try:
                    total_income = sum(float(p.get('amount', 0)) for p in payouts)
                except (ValueError, TypeError):
                    pass

            # If we still don't have total_income, try from report_data
            if total_income is None and report_data:
                val = report_data.get('payout_gain')
                if val is not None:
                    total_income = val

            return {
                'total_income': total_income,
                'dividend_count': len(payouts) if payouts else 0
            }
        except (TypeError, KeyError) as e:
            _LOGGER.debug(f"Error in _get_income_summary from income_data: {e}")

    # Fallback: try to extract payout info from report data
    if report_data:
        try:
            payout_gain = report_data.get('payout_gain')
            return {
                'total_income': payout_gain,
                'dividend_count': 0
            }
        except (TypeError, KeyError):
            pass

    return {
        'total_income': None,
        'dividend_count': 0
    }


def _get_diversity_top_markets(diversity_data):
    """Get top 3 markets by percentage."""
    if not diversity_data:
        _LOGGER.debug(f"diversity_data is empty")
        return {}, {}, {}

    try:
        breakdown = sorted(
            diversity_data.get('breakdown', []),
            key=lambda x: float(x.get('percentage', 0)),
            reverse=True
        )

        if not breakdown:
            _LOGGER.debug(f"No breakdown in diversity_data. Keys: {list(diversity_data.keys())}")
            return {}, {}, {}

        result = [{}, {}, {}]
        for i in range(min(3, len(breakdown))):
            result[i] = {
                'name': breakdown[i].get('group_name'),
                'percent': breakdown[i].get('percentage'),
                'value': breakdown[i].get('value')
            }
            _LOGGER.debug(f"Market {i+1}: {breakdown[i].get('group_name')} - {breakdown[i].get('percentage')}%")

        return result[0], result[1], result[2]
    except (ValueError, TypeError, KeyError) as e:
        _LOGGER.debug(f"Error in _get_diversity_top_markets: {e}, sample breakdown: {diversity_data.get('breakdown', [{}])[0] if diversity_data.get('breakdown') else 'no breakdown'}")
        return {}, {}, {}



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
            display_name = f"{local_name} value"
            new_sensor = SharesightSensor(market_sensor, entry, coordinator,
                                          local_currency, portfolio_id, edge, __index_market, local_name, display_name)
            sensors.append(new_sensor)
            MARKET_SENSORS.append(display_name)
            __index_market += 1

    __index_cash = 0
    for cash in coordinator.data['report']['cash_accounts']:
        for cash_sensor in CASH_SENSOR_DESCRIPTIONS:
            local_name = cash['name']
            display_name = f"{local_name} cash balance"
            new_sensor = SharesightSensor(cash_sensor, entry, coordinator,
                                          local_currency, portfolio_id, edge, __index_cash, local_name, display_name)
            sensors.append(new_sensor)
            CASH_SENSORS.append(display_name)
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
                update_display_name = f"{__local_name} value"
                if update_display_name not in MARKET_SENSORS:
                    local_market_currency = coordinator.data['portfolios'][0]['currency_code']
                    update_new_sensor = SharesightSensor(update_market_sensor, entry, update_coordinator,
                                                         local_market_currency, portfolio_id, edge,
                                                         __update_index_market, __local_name, update_display_name)
                    async_add_entities([update_new_sensor], True)
                    MARKET_SENSORS.append(update_display_name)
            __update_index_market += 1

        __update_index_cash = 0

        for update_cash in update_coordinator.data['report']['cash_accounts']:
            for update_cash_sensor in CASH_SENSOR_DESCRIPTIONS:
                __local_name = update_cash['name']
                update_display_name = f"{__local_name} cash balance"
                if update_display_name not in CASH_SENSORS:
                    local_cash_currency = coordinator.data['portfolios'][0]['currency_code']
                    update_new_sensor = SharesightSensor(update_cash_sensor, entry, update_coordinator,
                                                         local_cash_currency, portfolio_id, edge, __update_index_cash,
                                                         __local_name, update_display_name)
                    CASH_SENSORS.append(update_display_name)
                    async_add_entities([update_new_sensor], True)
            __update_index_cash += 1

    async_track_time_interval(hass, update_sensors, UPDATE_SENSOR_SCAN_INTERVAL)


class SharesightSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, sensor, entry, coordinator, currency, portfolio_id, edge, index=0, local_name="", display_name=""):
        super().__init__(coordinator)
        self._state_class = sensor.state_class
        self._coordinator = coordinator
        self._portfolio_id = portfolio_id
        self._entity_category = sensor.entity_category
        # Use display_name if provided, otherwise use sensor.name
        self._name = display_name if display_name else str(sensor.name)
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

        base_entity_id = f"{self._name.lower().replace(' ', '_')}_{self._portfolio_id}"
        self.entity_id = f"sensor.{base_entity_id}"

        if edge:
            edge_name = " Edge "
            edge_url = "edge-"
        else:
            edge_name = " "
            edge_url = ""

        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, self._portfolio_id)},
            configuration_url=f"https://{edge_url}portfolio.sharesight.com/portfolios/{self._portfolio_id}",
            model=f"Sharesight{edge_name}API",
            name=f"Sharesight{edge_name}Portfolio {self._portfolio_id}")

        try:
            if self._extension_key == "Extention":
                self._state = self._coordinator.data[self._sub_key][self._key]
                self._unique_id = f"{self._portfolio_id}_{self._sub_key}_{self._key}_{APP_VERSION}"
            elif self._sub_key == "report" and self._key != "sub_totals" and self._key != "cash_accounts":
                self._state = self._coordinator.data[self._sub_key][self._key]
                self._unique_id = f"{self._portfolio_id}_{self._key}_{APP_VERSION}"
            elif self._key == "user_id":
                self._state = self._coordinator.data[self._sub_key][0][self._key]
                self._unique_id = f"{self._portfolio_id}_{self._key}_{APP_VERSION}"
            elif "sub_totals" in self._key or "cash_accounts" in self._key:
                self._state = self._coordinator.data['report'][self._key][self._index][self._sub_key]
                self._unique_id = f"{self._portfolio_id}_{local_name}_VALUE_{APP_VERSION}"
            else:
                self._state = self._coordinator.data[self._sub_key][0][self._key]
                self._unique_id = f"{self._portfolio_id}_{self._key}_{APP_VERSION}"

        except (ValueError, KeyError, IndexError, TypeError) as e:
            _LOGGER.debug(f"Could not initialize sensor '{self._key}': {type(e).__name__}: {e}")
            self._state = None
            self._unique_id = f"{self._portfolio_id}_{self._sub_key}_{self._key}_{APP_VERSION}"

    @property
    def native_value(self):
        try:
            if self._extension_key == "Extention":
                # Used for one-day, one-week and current financial year
                data = self._coordinator.data.get(self._sub_key, {})
                if not data or not isinstance(data, dict):
                    return None

                # annualised_return_percent is not a direct field in the v2
                # performance response.  When the API returns percentages as
                # annualised (percentages_annualised == True) we use
                # total_gain_percent.  For the financial-year period, the v2
                # API does not include a percentages_annualised flag so we
                # fall back to total_gain_percent as the best available value.
                if self._key == 'annualised_return_percent':
                    _LOGGER.debug(
                        f"annualised_return_percent lookup in '{self._sub_key}': "
                        f"percentages_annualised={data.get('percentages_annualised')}, "
                        f"total_gain_percent={data.get('total_gain_percent')}, "
                        f"keys={list(data.keys())}"
                    )
                    val = data.get('total_gain_percent')
                    if val is not None:
                        try:
                            return round(float(val), 2)
                        except (ValueError, TypeError):
                            return None
                    return None

                return data.get(self._key)
            elif self._sub_key == "report" and self._key != "sub_totals" and self._key != "cash_accounts":
                # Used for direct report fields (cost_base, unrealised_gain, etc.)
                report_data = self._coordinator.data.get('report', {})

                # Try exact key first
                if self._key in report_data:
                    val = report_data[self._key]
                    # Don't return list/dict values as sensor state
                    if not isinstance(val, (list, dict)):
                        return val

                # Compute derived fields from available report data
                try:
                    if self._key == 'cost_base':
                        value = report_data.get('value')
                        capital_gain = report_data.get('capital_gain')
                        if value is not None and capital_gain is not None:
                            return round(float(value) - float(capital_gain), 2)
                        # Fallback: try summing from holdings
                        holdings_data = self._coordinator.data.get('holdings', {})
                        holdings_list = holdings_data.get('holdings', []) if isinstance(holdings_data, dict) else []
                        if holdings_list:
                            total_cost = 0
                            for h in holdings_list:
                                for f in ('cost_base', 'cost_basis', 'cost'):
                                    cv = h.get(f)
                                    if cv is not None:
                                        try:
                                            total_cost += float(cv)
                                        except (ValueError, TypeError):
                                            pass
                                        break
                            if total_cost:
                                return round(total_cost, 2)
                        return None

                    elif self._key == 'unrealised_gain':
                        capital_gain = report_data.get('capital_gain')
                        if capital_gain is not None:
                            return round(float(capital_gain), 2)
                        return None

                    elif self._key == 'unrealised_gain_percent':
                        capital_gain_percent = report_data.get('capital_gain_percent')
                        if capital_gain_percent is not None:
                            return round(float(capital_gain_percent), 2)
                        return None

                    elif self._key == 'annualised_return_percent':
                        # Try explicit annualised fields first
                        for field in ('annualised_return_percent', 'annualised_return', 'annualised_percent'):
                            val = report_data.get(field)
                            if val is not None:
                                try:
                                    return round(float(val), 2)
                                except (ValueError, TypeError):
                                    continue
                        # Fall back to total_gain_percent when annualised
                        if report_data.get('percentages_annualised'):
                            val = report_data.get('total_gain_percent')
                            if val is not None:
                                try:
                                    return round(float(val), 2)
                                except (ValueError, TypeError):
                                    pass
                        return None

                    elif self._key == 'start_value':
                        value = report_data.get('value')
                        total_gain = report_data.get('total_gain')
                        if value is not None and total_gain is not None:
                            return round(float(value) - float(total_gain), 2)
                        return None
                except (ValueError, TypeError) as e:
                    _LOGGER.debug(f"Error computing '{self._key}': {e}")
                    return None

                _LOGGER.debug(f"Field '{self._key}' not available in report data")
                return None
            elif self._key == "user_id":
                # Used to get the userID
                return self._coordinator.data[self._sub_key][0][self._key]
            elif "sub_totals" in self._key or "cash_accounts" in self._key:
                # Used for cash accounts or market data
                return self._coordinator.data['report'][self._key][self._index][self._sub_key]
            # Holdings sensors
            elif self._sub_key == "holdings":
                holdings_data = self._coordinator.data.get('holdings', {})
                if self._key == "holding_count":
                    return len(holdings_data.get('holdings', []))
                elif self._key == "largest_holding_symbol":
                    largest = _get_largest_holding(holdings_data)
                    return largest.get('symbol') if largest else None
                elif self._key == "largest_holding_value":
                    largest = _get_largest_holding(holdings_data)
                    return largest.get('value') if largest else None
                elif self._key == "largest_holding_percent":
                    largest = _get_largest_holding(holdings_data)
                    return largest.get('percent') if largest else None
                elif self._key == "top_gain_symbol":
                    top_gain = _get_top_gain_holding(holdings_data)
                    return top_gain.get('symbol') if top_gain else None
                elif self._key == "top_gain_amount":
                    top_gain = _get_top_gain_holding(holdings_data)
                    return top_gain.get('amount') if top_gain else None
                elif self._key == "top_gain_percent":
                    top_gain = _get_top_gain_holding(holdings_data)
                    return top_gain.get('percent') if top_gain else None
                elif self._key == "worst_gain_symbol":
                    worst_gain = _get_worst_gain_holding(holdings_data)
                    return worst_gain.get('symbol') if worst_gain else None
                elif self._key == "worst_gain_amount":
                    worst_gain = _get_worst_gain_holding(holdings_data)
                    return worst_gain.get('amount') if worst_gain else None
                elif self._key == "worst_gain_percent":
                    worst_gain = _get_worst_gain_holding(holdings_data)
                    return worst_gain.get('percent') if worst_gain else None
            # Income Report sensors
            elif self._sub_key == "income_report":
                income_data = self._coordinator.data.get('income_report', {})
                report_data = self._coordinator.data.get('report', {})
                income_summary = _get_income_summary(income_data, report_data)
                if self._key == "total_income":
                    return income_summary.get('total_income')
                elif self._key == "dividend_count":
                    return income_summary.get('dividend_count')
            # Diversity sensors
            elif self._sub_key == "diversity":
                diversity_data = self._coordinator.data.get('diversity', {})
                market_1, market_2, market_3 = _get_diversity_top_markets(diversity_data)
                if self._key == "market_1_name":
                    return market_1.get('name') if market_1 else None
                elif self._key == "market_1_percent":
                    return market_1.get('percent') if market_1 else None
                elif self._key == "market_1_value":
                    return market_1.get('value') if market_1 else None
                elif self._key == "market_2_name":
                    return market_2.get('name') if market_2 else None
                elif self._key == "market_2_percent":
                    return market_2.get('percent') if market_2 else None
                elif self._key == "market_2_value":
                    return market_2.get('value') if market_2 else None
                elif self._key == "market_3_name":
                    return market_3.get('name') if market_3 else None
                elif self._key == "market_3_percent":
                    return market_3.get('percent') if market_3 else None
                elif self._key == "market_3_value":
                    return market_3.get('value') if market_3 else None
            else:
                return self._coordinator.data[self._sub_key][0][self._key]

        except (KeyError, IndexError, TypeError) as e:
            _LOGGER.debug(f"Error accessing data for key '{self._key}': {type(e).__name__}: {e}")
            return None

    @property
    def name(self):
        return self._name

    @property
    def state_class(self):
        return self._state_class

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
    def suggested_display_precision(self):
        return self._suggested_display_precision

    @property
    def device_class(self):
        return self._device_class

    @property
    def native_unit_of_measurement(self):
        """Return the native unit of measurement of the sensor."""
        return self._native_unit_of_measurement
