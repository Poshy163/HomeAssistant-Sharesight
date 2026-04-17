from homeassistant.const import CURRENCY_DOLLAR
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.components.sensor import (
    SensorEntity
)
from .const import APP_VERSION, DOMAIN
import logging
import time
from datetime import datetime, timedelta
from homeassistant.util import dt as dt_util
from .enum import SENSOR_DESCRIPTIONS, MARKET_SENSOR_DESCRIPTIONS, CASH_SENSOR_DESCRIPTIONS, HOLDING_SENSOR_DESCRIPTIONS
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .coordinator import SharesightCoordinator

_LOGGER: logging.Logger = logging.getLogger(__package__)


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
        _LOGGER.debug("No holdings in holdings_data. Keys: %s", list(holdings_data.keys()))
        return None

    try:
        # Log sample holding keys for debugging
        if holdings:
            _LOGGER.debug("Sample holding keys: %s", list(holdings[0].keys()))

        largest = max(holdings, key=_get_holding_value)
        portfolio_value = float(holdings_data.get('value', 0) or 1)
        largest_value = _get_holding_value(largest)
        percent = (largest_value / portfolio_value * 100) if portfolio_value else 0
        symbol = _get_holding_symbol(largest)
        _LOGGER.debug("Found largest holding: %s with value %s", symbol, largest_value)
        return {
            'symbol': symbol,
            'value': largest_value,
            'percent': round(percent, 2)
        }
    except (ValueError, TypeError, KeyError) as e:
        _LOGGER.debug("Error in _get_largest_holding: %s, first holding sample: %s", e, holdings[0] if holdings else 'no holdings')
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
        _LOGGER.debug("Error in _get_top_gain_holding: %s", e)
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
        _LOGGER.debug("Error in _get_worst_gain_holding: %s", e)
        return None


def _get_smallest_holding(holdings_data):
    """Get the smallest holding by value."""
    if not holdings_data:
        return None
    holdings = holdings_data.get('holdings', [])
    if not holdings:
        return None
    try:
        smallest = min(holdings, key=_get_holding_value)
        portfolio_value = float(holdings_data.get('value', 0) or 1)
        smallest_value = _get_holding_value(smallest)
        return {
            'symbol': _get_holding_symbol(smallest),
            'value': smallest_value,
        }
    except (ValueError, TypeError, KeyError):
        return None


def _find_holding_by_symbol(holdings_list, symbol):
    """Find a holding dict by its instrument code/symbol."""
    for h in holdings_list:
        if _get_holding_symbol(h) == symbol:
            return h
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
            _LOGGER.debug("Error in _get_income_summary from income_data: %s", e)

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
        _LOGGER.debug("diversity_data is empty")
        return {}, {}, {}

    try:
        breakdown = sorted(
            diversity_data.get('breakdown', []),
            key=lambda x: float(x.get('percentage', 0)),
            reverse=True
        )

        if not breakdown:
            _LOGGER.debug("No breakdown in diversity_data. Keys: %s", list(diversity_data.keys()))
            return {}, {}, {}

        result = [{}, {}, {}]
        for i in range(min(3, len(breakdown))):
            result[i] = {
                'name': breakdown[i].get('group_name'),
                'percent': breakdown[i].get('percentage'),
                'value': breakdown[i].get('value')
            }
            _LOGGER.debug("Market %s: %s - %s%%", i + 1, breakdown[i].get('group_name'), breakdown[i].get('percentage'))

        return result[0], result[1], result[2]
    except (ValueError, TypeError, KeyError) as e:
        _LOGGER.debug(
            "Error in _get_diversity_top_markets: %s, sample breakdown: %s",
            e,
            diversity_data.get('breakdown', [{}])[0] if diversity_data.get('breakdown') else 'no breakdown',
        )
        return {}, {}, {}


def _calculate_annualised_percent(
    total_gain_percent,
    start_date_str,
    end_date_str,
    percentages_annualised=False,
):
    """Calculate annualised return percent from total return percent and date range."""
    if total_gain_percent is None:
        return None

    try:
        total_gain_percent = float(total_gain_percent)
    except (ValueError, TypeError):
        return None

    if percentages_annualised:
        return round(total_gain_percent, 2)

    if not start_date_str or not end_date_str:
        return round(total_gain_percent, 2)

    try:
        start = datetime.strptime(str(start_date_str)[:10], "%Y-%m-%d").date()
        end = datetime.strptime(str(end_date_str)[:10], "%Y-%m-%d").date()
        days = (end - start).days
        if days <= 0:
            return round(total_gain_percent, 2)

        growth_ratio = 1 + (total_gain_percent / 100)
        if growth_ratio <= 0:
            return None

        annualised = (growth_ratio ** (365 / days) - 1) * 100
        return round(annualised, 2)
    except (ValueError, TypeError, OverflowError):
        return round(total_gain_percent, 2)


def _get_contributions_summary(cash_transactions_data):
    """Compute contribution summary from cash account transactions."""
    transactions = []
    if isinstance(cash_transactions_data, dict):
        transactions = cash_transactions_data.get("cash_account_transactions", [])

    total_contributions = 0.0
    total_withdrawals = 0.0
    latest = None

    for tx in transactions:
        if not isinstance(tx, dict):
            continue

        tx_type = tx.get("type_name")
        if not tx_type:
            tx_type_obj = tx.get("cash_account_transaction_type")
            if isinstance(tx_type_obj, dict):
                tx_type = tx_type_obj.get("name")
        tx_type = str(tx_type or "").upper()
        if tx_type not in {"DEPOSIT", "WITHDRAWAL", "OPENING BALANCE"}:
            continue

        amount = tx.get("amount")
        try:
            amount = float(amount)
        except (ValueError, TypeError):
            continue

        if amount > 0:
            total_contributions += amount
        elif amount < 0:
            total_withdrawals += abs(amount)

        dt = tx.get("date_time") or tx.get("date")
        if dt:
            dt_value = str(dt)
            if latest is None or dt_value > latest.get("date_time", ""):
                latest = {"date_time": dt_value, "amount": amount}

    return {
        "total_contributions": round(total_contributions, 2),
        "total_withdrawals": round(total_withdrawals, 2),
        "net_contributions": round(total_contributions - total_withdrawals, 2),
        "last_contribution_date": latest.get("date_time", "")[:10] if latest else None,
        "last_contribution_amount": round(float(latest.get("amount")), 2) if latest else None,
    }


def _get_cash_accounts_summary(report_data):
    """Compute aggregate cash account stats from report payload."""
    cash_accounts = report_data.get("cash_accounts", []) if isinstance(report_data, dict) else []
    if not cash_accounts:
        return {"cash_accounts_count": 0, "total_cash_value": 0.0}

    total_value = 0.0
    for account in cash_accounts:
        if not isinstance(account, dict):
            continue
        try:
            total_value += float(account.get("value", 0) or 0)
        except (ValueError, TypeError):
            continue

    return {
        "cash_accounts_count": len(cash_accounts),
        "total_cash_value": round(total_value, 2),
    }


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]

    coordinator: SharesightCoordinator = data["coordinator"]
    portfolio_id = data["portfolio_id"]
    edge = data["edge"]
    portfolios = coordinator.data.get("portfolios", [])
    local_currency = "USD"
    if portfolios and isinstance(portfolios[0], dict):
        local_currency = portfolios[0].get("currency_code", "USD")
    elif isinstance(coordinator.data.get("report", {}).get("currency"), dict):
        local_currency = coordinator.data.get("report", {}).get("currency", {}).get("code", "USD")

    entry_id = entry.entry_id  # noqa: F841 — retained for logging/debug parity
    # Per-entry tracking lives in hass.data so it is cleared automatically on
    # unload.  It's populated by __init__.async_setup_entry.
    market_sensors: list[str] = data.setdefault("market_sensors", [])
    cash_sensors: list[str] = data.setdefault("cash_sensors", [])
    holding_sensors: list[str] = data.setdefault("holding_sensors", [])

    sensors = []
    seen_unique_ids: set[str] = set()

    for sensor in SENSOR_DESCRIPTIONS:
        sensors.append(SharesightSensor(sensor, entry, coordinator,
                                        local_currency, portfolio_id, edge))

    # Deduplicate sub_totals by group_name (API may return duplicates)
    seen_markets: set[str] = set()
    __index_market = 0
    report = coordinator.data.get("report", {})
    for market in report.get('sub_totals', []):
        local_name = market.get('group_name', 'Unknown Market')
        if local_name in seen_markets:
            _LOGGER.debug("Skipping duplicate market sub_total: %s", local_name)
            __index_market += 1
            continue
        seen_markets.add(local_name)
        for market_sensor in MARKET_SENSOR_DESCRIPTIONS:
            display_name = f"{local_name} {market_sensor.sub_key.replace('_', ' ')}"
            uid = f"{portfolio_id}_{local_name}_{market_sensor.sub_key}_{market_sensor.key}_{APP_VERSION}"
            if uid in seen_unique_ids:
                _LOGGER.debug("Skipping duplicate market sensor unique_id: %s", uid)
                continue
            seen_unique_ids.add(uid)
            new_sensor = SharesightSensor(market_sensor, entry, coordinator,
                                          local_currency, portfolio_id, edge, __index_market, local_name, display_name)
            sensors.append(new_sensor)
            market_sensors.append(display_name)
        __index_market += 1

    # Deduplicate cash_accounts by name
    seen_cash: set[str] = set()
    __index_cash = 0
    for cash in report.get('cash_accounts', []):
        local_name = cash.get('name', 'Unknown Cash Account')
        if local_name in seen_cash:
            _LOGGER.debug("Skipping duplicate cash account: %s", local_name)
            __index_cash += 1
            continue
        seen_cash.add(local_name)
        for cash_sensor in CASH_SENSOR_DESCRIPTIONS:
            display_name = f"{local_name} cash balance"
            uid = f"{portfolio_id}_{local_name}_{cash_sensor.sub_key}_{cash_sensor.key}_{APP_VERSION}"
            if uid in seen_unique_ids:
                _LOGGER.debug("Skipping duplicate cash sensor unique_id: %s", uid)
                continue
            seen_unique_ids.add(uid)
            new_sensor = SharesightSensor(cash_sensor, entry, coordinator,
                                          local_currency, portfolio_id, edge, __index_cash, local_name, display_name)
            sensors.append(new_sensor)
            cash_sensors.append(display_name)
        __index_cash += 1

    # Create per-holding individual sensors
    seen_holding_symbols: set[str] = set()
    holdings_data = coordinator.data.get("holdings", {})
    holdings_list = holdings_data.get("holdings", []) if isinstance(holdings_data, dict) else []
    for holding in holdings_list:
        symbol = _get_holding_symbol(holding)
        if not symbol or symbol in seen_holding_symbols:
            continue
        seen_holding_symbols.add(symbol)
        for holding_sensor in HOLDING_SENSOR_DESCRIPTIONS:
            display_name = f"{symbol} {holding_sensor.sub_key.replace('_', ' ')}"
            uid = f"{portfolio_id}_{symbol}_{holding_sensor.sub_key}_{holding_sensor.key}_{APP_VERSION}"
            if uid in seen_unique_ids:
                continue
            seen_unique_ids.add(uid)
            new_sensor = SharesightSensor(holding_sensor, entry, coordinator,
                                          local_currency, portfolio_id, edge, 0, symbol, display_name)
            sensors.append(new_sensor)
            holding_sensors.append(display_name)

    async_add_entities(sensors, True)

    @callback
    def update_sensors() -> None:
        """Discover new markets/cash accounts/holdings after a coordinator refresh."""
        _LOGGER.debug("Checking for new market/cash/holding sensors")
        update_data = hass.data[DOMAIN].get(entry.entry_id)
        if not update_data:
            return
        update_coordinator: SharesightCoordinator = update_data["coordinator"]
        if not update_coordinator.data:
            return
        update_report = update_coordinator.data.get("report", {})

        # Deduplicate by group_name when checking for new markets
        seen_update_markets: set[str] = set()
        __update_index_market = 0
        for update_market in update_report.get('sub_totals', []):
            __local_name = update_market.get('group_name', 'Unknown Market')
            if __local_name in seen_update_markets:
                __update_index_market += 1
                continue
            seen_update_markets.add(__local_name)
            for update_market_sensor in MARKET_SENSOR_DESCRIPTIONS:
                update_display_name = f"{__local_name} {update_market_sensor.sub_key.replace('_', ' ')}"
                if update_display_name not in market_sensors:
                    local_market_currency = local_currency
                    update_new_sensor = SharesightSensor(update_market_sensor, entry, update_coordinator,
                                                         local_market_currency, portfolio_id, edge,
                                                         __update_index_market, __local_name, update_display_name)
                    async_add_entities([update_new_sensor], True)
                    market_sensors.append(update_display_name)
            __update_index_market += 1

        # Deduplicate by name when checking for new cash accounts
        seen_update_cash: set[str] = set()
        __update_index_cash = 0
        for update_cash in update_report.get('cash_accounts', []):
            __local_name = update_cash.get('name', 'Unknown Cash Account')
            if __local_name in seen_update_cash:
                __update_index_cash += 1
                continue
            seen_update_cash.add(__local_name)
            for update_cash_sensor in CASH_SENSOR_DESCRIPTIONS:
                update_display_name = f"{__local_name} cash balance"
                if update_display_name not in cash_sensors:
                    local_cash_currency = local_currency
                    update_new_sensor = SharesightSensor(update_cash_sensor, entry, update_coordinator,
                                                         local_cash_currency, portfolio_id, edge, __update_index_cash,
                                                         __local_name, update_display_name)
                    cash_sensors.append(update_display_name)
                    async_add_entities([update_new_sensor], True)
            __update_index_cash += 1

        # Check for new holdings
        update_holdings_data = update_coordinator.data.get("holdings", {})
        update_holdings_list = update_holdings_data.get("holdings", []) if isinstance(update_holdings_data, dict) else []
        for update_holding in update_holdings_list:
            __holding_symbol = _get_holding_symbol(update_holding)
            if not __holding_symbol:
                continue
            for update_holding_sensor in HOLDING_SENSOR_DESCRIPTIONS:
                update_holding_display_name = f"{__holding_symbol} {update_holding_sensor.sub_key.replace('_', ' ')}"
                if update_holding_display_name not in holding_sensors:
                    update_new_holding_sensor = SharesightSensor(
                        update_holding_sensor, entry, update_coordinator,
                        local_currency, portfolio_id, edge, 0, __holding_symbol,
                        update_holding_display_name)
                    async_add_entities([update_new_holding_sensor], True)
                    holding_sensors.append(update_holding_display_name)

    # Piggy-back on the coordinator's own update cycle rather than running a
    # second time interval.  New markets/holdings appear as soon as the next
    # successful poll brings them in.
    unsub = coordinator.async_add_listener(update_sensors)
    hass.data[DOMAIN][entry.entry_id]["update_sensors_unsub"] = unsub


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
        self._device_group = getattr(sensor, 'device_group', 'portfolio')
        self._local_name = local_name

        # Propagate entity_registry_enabled_default from description
        if hasattr(sensor, 'entity_registry_enabled_default') and not sensor.entity_registry_enabled_default:
            self._attr_entity_registry_enabled_default = False
        else:
            self._attr_entity_registry_enabled_default = True

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

        base_config_url = f"https://{edge_url}portfolio.sharesight.com/portfolios/{self._portfolio_id}"
        base_model = f"Sharesight{edge_name}API"

        # Device group labels and identifiers for separate HA devices
        device_group_config = {
            "portfolio": {
                "name": f"Sharesight{edge_name}Portfolio {self._portfolio_id}",
                "identifier": f"{self._portfolio_id}_portfolio",
                "model": f"{base_model} - Portfolio",
            },
            "daily": {
                "name": f"Sharesight{edge_name}Daily Performance",
                "identifier": f"{self._portfolio_id}_daily",
                "model": f"{base_model} - Daily Performance",
            },
            "weekly": {
                "name": f"Sharesight{edge_name}Weekly Performance",
                "identifier": f"{self._portfolio_id}_weekly",
                "model": f"{base_model} - Weekly Performance",
            },
            "financial_year": {
                "name": f"Sharesight{edge_name}Financial Year",
                "identifier": f"{self._portfolio_id}_financial_year",
                "model": f"{base_model} - Financial Year",
            },
            "holdings": {
                "name": f"Sharesight{edge_name}Holdings",
                "identifier": f"{self._portfolio_id}_holdings",
                "model": f"{base_model} - Holdings",
            },
            "income": {
                "name": f"Sharesight{edge_name}Income",
                "identifier": f"{self._portfolio_id}_income",
                "model": f"{base_model} - Income",
            },
            "diversity": {
                "name": f"Sharesight{edge_name}Diversity",
                "identifier": f"{self._portfolio_id}_diversity",
                "model": f"{base_model} - Diversity",
            },
            "trades": {
                "name": f"Sharesight{edge_name}Trades",
                "identifier": f"{self._portfolio_id}_trades",
                "model": f"{base_model} - Trades",
            },
            "contributions": {
                "name": f"Sharesight{edge_name}Contributions",
                "identifier": f"{self._portfolio_id}_contributions",
                "model": f"{base_model} - Contributions",
            },
            "monthly": {
                "name": f"Sharesight{edge_name}Monthly Performance",
                "identifier": f"{self._portfolio_id}_monthly",
                "model": f"{base_model} - Monthly Performance",
            },
            "ytd": {
                "name": f"Sharesight{edge_name}YTD Performance",
                "identifier": f"{self._portfolio_id}_ytd",
                "model": f"{base_model} - YTD Performance",
            },
        }

        if self._device_group == "market" and local_name:
            device_id = f"{self._portfolio_id}_market_{local_name}"
            device_name = f"Sharesight{edge_name}{local_name}"
            device_model = f"{base_model} - Market: {local_name}"
        elif self._device_group == "cash" and local_name:
            device_id = f"{self._portfolio_id}_cash_{local_name}"
            device_name = f"Sharesight{edge_name}Cash: {local_name}"
            device_model = f"{base_model} - Cash: {local_name}"
        elif self._device_group == "holding" and local_name:
            device_id = f"{self._portfolio_id}_holding_{local_name}"
            device_name = f"Sharesight{edge_name}Holding: {local_name}"
            device_model = f"{base_model} - Holding: {local_name}"
        else:
            cfg = device_group_config.get(self._device_group, device_group_config["portfolio"])
            device_id = cfg["identifier"]
            device_name = cfg["name"]
            device_model = cfg["model"]

        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, device_id)},
            configuration_url=base_config_url,
            model=device_model,
            name=device_name)

        try:
            if self._extension_key == "Extension":
                self._state = self._coordinator.data[self._sub_key][self._key]
                self._unique_id = f"{self._portfolio_id}_{self._sub_key}_{self._key}_{APP_VERSION}"
            elif self._key == "holdings_list":
                # Per-holding individual sensor
                holdings_list = self._coordinator.data.get('holdings', {}).get('holdings', [])
                holding = _find_holding_by_symbol(holdings_list, local_name)
                if holding and self._sub_key == "cost_base":
                    val = _get_holding_value(holding)
                    cg = _get_holding_gain(holding)
                    self._state = round(val - cg, 2) if val else None
                elif holding and self._sub_key == "annualised_return_percent":
                    report_data = self._coordinator.data.get('report', {})
                    self._state = _calculate_annualised_percent(
                        holding.get("total_gain_percent"),
                        report_data.get("start_date"),
                        report_data.get("end_date"),
                        bool(report_data.get("percentages_annualised", False)),
                    )
                elif holding:
                    self._state = holding.get(self._sub_key)
                else:
                    self._state = None
                self._unique_id = f"{self._portfolio_id}_holding_{local_name}_{self._sub_key}_{self._key}_{APP_VERSION}"
            elif self._sub_key == "report" and self._key != "sub_totals" and self._key != "cash_accounts":
                self._state = self._coordinator.data[self._sub_key][self._key]
                self._unique_id = f"{self._portfolio_id}_{self._key}_{APP_VERSION}"
            elif self._sub_key == "user_setting":
                user_setting = self._coordinator.data.get("user_setting", {})
                if isinstance(user_setting, dict):
                    portfolio_user_setting = user_setting.get("portfolio_user_setting", {})
                    if isinstance(portfolio_user_setting, dict):
                        self._state = portfolio_user_setting.get(self._key)
                    else:
                        self._state = None
                else:
                    self._state = None
                self._unique_id = f"{self._portfolio_id}_{self._sub_key}_{self._key}_{APP_VERSION}"
            elif self._sub_key == "portfolio_detail":
                detail = self._coordinator.data.get("portfolio_detail", {})
                self._state = detail.get(self._key) if isinstance(detail, dict) else None
                self._unique_id = f"{self._portfolio_id}_{self._sub_key}_{self._key}_{APP_VERSION}"
            elif self._key == "user_id":
                self._state = self._coordinator.data[self._sub_key][0][self._key]
                self._unique_id = f"{self._portfolio_id}_{self._key}_{APP_VERSION}"
            elif "sub_totals" in self._key or "cash_accounts" in self._key:
                sub_entry = self._coordinator.data['report'][self._key][self._index]
                if self._sub_key == "holding_count":
                    self._state = len(sub_entry.get('holdings', []))
                elif self._sub_key == "cost_base":
                    val = sub_entry.get('value')
                    cg = sub_entry.get('capital_gain')
                    if val is not None and cg is not None:
                        self._state = round(float(val) - float(cg), 2)
                    else:
                        self._state = None
                elif self._sub_key == "annualised_return_percent":
                    self._state = _calculate_annualised_percent(
                        sub_entry.get("total_gain_percent"),
                        self._coordinator.data.get("report", {}).get("start_date"),
                        self._coordinator.data.get("report", {}).get("end_date"),
                        bool(self._coordinator.data.get("report", {}).get("percentages_annualised", False)),
                    )
                else:
                    self._state = sub_entry.get(self._sub_key)
                self._unique_id = f"{self._portfolio_id}_{local_name}_{self._sub_key}_{self._key}_{APP_VERSION}"
            else:
                self._state = self._coordinator.data[self._sub_key][0][self._key]
                self._unique_id = f"{self._portfolio_id}_{self._key}_{APP_VERSION}"

        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("Could not initialize sensor '%s': %s: %s", self._key, type(e).__name__, e)
            self._state = None
            if local_name and ("sub_totals" in self._key or "cash_accounts" in self._key):
                self._unique_id = f"{self._portfolio_id}_{local_name}_{self._sub_key}_{self._key}_{APP_VERSION}"
            elif self._key == "holdings_list" and local_name:
                self._unique_id = f"{self._portfolio_id}_holding_{local_name}_{self._sub_key}_{self._key}_{APP_VERSION}"
            else:
                self._unique_id = f"{self._portfolio_id}_{self._sub_key}_{self._key}_{APP_VERSION}"

    @property
    def native_value(self):
        try:
            if self._extension_key == "Extension":
                # Used for one-day, one-week, one-month, ytd and current financial year
                data = self._coordinator.data.get(self._sub_key, {})
                if not data or not isinstance(data, dict):
                    return None
                if self._key == "annualised_return_percent":
                    return _calculate_annualised_percent(
                        data.get("total_gain_percent"),
                        data.get("start_date"),
                        data.get("end_date"),
                        bool(data.get("percentages_annualised", False)),
                    )
                if self._key == "start_value":
                    value = data.get("value")
                    total_gain = data.get("total_gain")
                    if value is not None and total_gain is not None:
                        try:
                            return round(float(value) - float(total_gain), 2)
                        except (ValueError, TypeError):
                            pass
                    return None
                if self._key == "end_value":
                    value = data.get("value")
                    if value is not None:
                        try:
                            return round(float(value), 2)
                        except (ValueError, TypeError):
                            pass
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

                    elif self._key == 'start_value':
                        value = report_data.get('value')
                        total_gain = report_data.get('total_gain')
                        if value is not None and total_gain is not None:
                            return round(float(value) - float(total_gain), 2)
                        return None
                    elif self._key == 'annualised_return_percent':
                        return _calculate_annualised_percent(
                            report_data.get('total_gain_percent'),
                            report_data.get('start_date'),
                            report_data.get('end_date'),
                            bool(report_data.get('percentages_annualised', False)),
                        )
                    elif self._key in ('cash_accounts_count', 'total_cash_value'):
                        cash_summary = _get_cash_accounts_summary(report_data)
                        return cash_summary.get(self._key)
                    elif self._key == 'market_count':
                        return len(report_data.get('sub_totals', []))
                    elif self._key in (
                        'largest_market_name',
                        'largest_market_value',
                        'largest_market_percent',
                    ):
                        sub_totals = report_data.get('sub_totals', [])
                        if not sub_totals:
                            return None
                        largest_market = max(
                            sub_totals,
                            key=lambda s: float(s.get('value', 0) or 0),
                        )
                        if self._key == 'largest_market_name':
                            return largest_market.get('group_name')
                        if self._key == 'largest_market_value':
                            return largest_market.get('value')
                        total_value = float(report_data.get('value', 0) or 0)
                        largest_value = float(largest_market.get('value', 0) or 0)
                        return round((largest_value / total_value * 100), 2) if total_value else None
                    elif self._key == 'equity_value':
                        total_value = report_data.get('value')
                        cash_summary = _get_cash_accounts_summary(report_data)
                        total_cash = cash_summary.get('total_cash_value', 0)
                        if total_value is not None:
                            return round(float(total_value) - float(total_cash), 2)
                        return None
                    elif self._key == 'cash_allocation_percent':
                        total_value = report_data.get('value')
                        cash_summary = _get_cash_accounts_summary(report_data)
                        total_cash = cash_summary.get('total_cash_value', 0)
                        if total_value and float(total_value) != 0:
                            return round(float(total_cash) / float(total_value) * 100, 2)
                        return None
                    elif self._key == 'equity_allocation_percent':
                        total_value = report_data.get('value')
                        cash_summary = _get_cash_accounts_summary(report_data)
                        total_cash = cash_summary.get('total_cash_value', 0)
                        if total_value and float(total_value) != 0:
                            equity = float(total_value) - float(total_cash)
                            return round(equity / float(total_value) * 100, 2)
                        return None
                except (ValueError, TypeError) as e:
                    _LOGGER.debug("Error computing '%s': %s", self._key, e)
                    return None

                _LOGGER.debug("Field '%s' not available in report data", self._key)
                return None
            elif self._key == "holdings_list":
                # Per-holding individual sensor - look up by symbol
                holdings_list = self._coordinator.data.get('holdings', {}).get('holdings', [])
                holding = _find_holding_by_symbol(holdings_list, self._local_name)
                if holding is None:
                    return None
                if self._sub_key == "cost_base":
                    val = _get_holding_value(holding)
                    cg = _get_holding_gain(holding)
                    if val:
                        return round(val - cg, 2)
                    return None
                if self._sub_key == "annualised_return_percent":
                    report_data = self._coordinator.data.get('report', {})
                    return _calculate_annualised_percent(
                        holding.get("total_gain_percent"),
                        report_data.get("start_date"),
                        report_data.get("end_date"),
                        bool(report_data.get("percentages_annualised", False)),
                    )
                return holding.get(self._sub_key)
            elif self._key == "user_id":
                # Used to get the userID
                return self._coordinator.data[self._sub_key][0][self._key]
            elif self._sub_key == "user_setting":
                user_setting = self._coordinator.data.get("user_setting", {})
                if not isinstance(user_setting, dict):
                    return None
                portfolio_user_setting = user_setting.get("portfolio_user_setting", {})
                if not isinstance(portfolio_user_setting, dict):
                    return None
                value = portfolio_user_setting.get(self._key)
                if isinstance(value, bool):
                    return "Enabled" if value else "Disabled"
                return value
            elif "sub_totals" in self._key or "cash_accounts" in self._key:
                # Used for cash accounts or market data
                sub_entry = self._coordinator.data['report'][self._key][self._index]
                if self._sub_key == "holding_count":
                    # Count holdings nested inside this sub_total or from report holdings
                    holdings = sub_entry.get('holdings', [])
                    if holdings:
                        return len(holdings)
                    report_holdings = self._coordinator.data.get('report', {}).get('holdings', [])
                    if report_holdings and self._local_name:
                        count = 0
                        for h in report_holdings:
                            group_name = h.get('group_name')
                            if not group_name:
                                instrument = h.get('instrument', {}) or {}
                                group_name = instrument.get('market_code') or h.get('market')
                            if group_name == self._local_name:
                                count += 1
                        return count
                    return 0
                if self._sub_key == "cost_base":
                    # cost_base is not in the API response; derive it
                    val = sub_entry.get('value')
                    cg = sub_entry.get('capital_gain')
                    if val is not None and cg is not None:
                        try:
                            return round(float(val) - float(cg), 2)
                        except (ValueError, TypeError):
                            return None
                    return None
                if self._sub_key == "annualised_return_percent":
                    return _calculate_annualised_percent(
                        sub_entry.get("total_gain_percent"),
                        self._coordinator.data.get("report", {}).get("start_date"),
                        self._coordinator.data.get("report", {}).get("end_date"),
                        bool(self._coordinator.data.get("report", {}).get("percentages_annualised", False)),
                    )
                return sub_entry.get(self._sub_key)

            elif self._sub_key == "holdings":
                holdings_data = self._coordinator.data.get('holdings', {})
                if self._key == "holding_count":
                    return len(holdings_data.get('holdings', []))
                elif self._key == "unconfirmed_transactions":
                    # Sum unconfirmed transactions across all report holdings
                    report_holdings = self._coordinator.data.get('report', {}).get('holdings', [])
                    total = 0
                    for h in report_holdings:
                        val = h.get('number_of_unconfirmed_transactions', 0)
                        if val:
                            try:
                                total += int(val)
                            except (ValueError, TypeError):
                                pass
                    return total
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
                elif self._key == "positive_holdings_count":
                    holdings_list = holdings_data.get('holdings', [])
                    return sum(1 for h in holdings_list if _get_holding_gain(h) > 0)
                elif self._key == "negative_holdings_count":
                    holdings_list = holdings_data.get('holdings', [])
                    return sum(1 for h in holdings_list if _get_holding_gain(h) < 0)
                elif self._key == "average_holding_value":
                    holdings_list = holdings_data.get('holdings', [])
                    if not holdings_list:
                        return None
                    total_val = sum(_get_holding_value(h) for h in holdings_list)
                    return round(total_val / len(holdings_list), 2)
                elif self._key == "total_holdings_value":
                    holdings_list = holdings_data.get('holdings', [])
                    if not holdings_list:
                        return 0
                    return round(sum(_get_holding_value(h) for h in holdings_list), 2)
                elif self._key == "total_holdings_gain":
                    holdings_list = holdings_data.get('holdings', [])
                    if not holdings_list:
                        return 0
                    return round(sum(_get_holding_gain(h) for h in holdings_list), 2)
                elif self._key == "smallest_holding_symbol":
                    smallest = _get_smallest_holding(holdings_data)
                    return smallest.get('symbol') if smallest else None
                elif self._key == "smallest_holding_value":
                    smallest = _get_smallest_holding(holdings_data)
                    return smallest.get('value') if smallest else None
            # Income Report sensors
            elif self._sub_key == "income_report":
                income_data = self._coordinator.data.get('income_report', {})
                report_data = self._coordinator.data.get('report', {})
                income_summary = _get_income_summary(income_data, report_data)
                if self._key == "total_income":
                    return income_summary.get('total_income')
                elif self._key == "dividend_count":
                    return income_summary.get('dividend_count')
                elif self._key == "last_dividend_date":
                    payouts = income_data.get('payouts', [])
                    if payouts:
                        # Sort by date descending, return the most recent
                        try:
                            sorted_payouts = sorted(
                                [
                                    p
                                    for p in payouts
                                    if p.get('paid_on') or p.get('date') or p.get('ex_date')
                                ],
                                key=lambda p: p.get('paid_on') or p.get('date') or p.get('ex_date', ''),
                                reverse=True
                            )
                            if sorted_payouts:
                                return (
                                    sorted_payouts[0].get('paid_on')
                                    or sorted_payouts[0].get('date')
                                    or sorted_payouts[0].get('ex_date')
                                )
                        except (TypeError, ValueError):
                            pass
                    return None
                elif self._key == "average_dividend_amount":
                    income_summary = _get_income_summary(income_data, report_data)
                    total = income_summary.get('total_income')
                    count = income_summary.get('dividend_count', 0)
                    if total is not None and count and count > 0:
                        try:
                            return round(float(total) / count, 2)
                        except (ValueError, TypeError, ZeroDivisionError):
                            pass
                    return None
                elif self._key == "largest_dividend_symbol":
                    payouts = income_data.get('payouts', [])
                    if payouts:
                        try:
                            largest = max(payouts, key=lambda p: float(p.get('amount', 0) or 0))
                            return (
                                largest.get('symbol')
                                or largest.get('instrument_code')
                                or (largest.get('holding', {}) or {}).get('instrument', {}).get('code', '')
                                or largest.get('company_name', '')
                            )
                        except (ValueError, TypeError):
                            pass
                    return None
                elif self._key == "largest_dividend_amount":
                    payouts = income_data.get('payouts', [])
                    if payouts:
                        try:
                            largest = max(payouts, key=lambda p: float(p.get('amount', 0) or 0))
                            return round(float(largest.get('amount', 0) or 0), 2)
                        except (ValueError, TypeError):
                            pass
                    return None
                # Payout tax detail aggregate sensors
                elif self._key in (
                    "total_gross_income",
                    "total_resident_withholding_tax",
                    "total_non_resident_withholding_tax",
                    "total_tax_credits",
                    "total_franked_amount",
                    "total_unfranked_amount",
                    "total_foreign_source_income",
                    "total_capital_gains_distributions",
                    "drp_reinvestment_count",
                ):
                    payouts = income_data.get('payouts', [])
                    if not payouts:
                        return 0 if self._key == "drp_reinvestment_count" else None
                    field_map = {
                        "total_gross_income": "gross_amount",
                        "total_resident_withholding_tax": "resident_withholding_tax",
                        "total_non_resident_withholding_tax": "non_resident_withholding_tax",
                        "total_tax_credits": "tax_credit",
                        "total_franked_amount": "franked_amount",
                        "total_unfranked_amount": "unfranked_amount",
                        "total_foreign_source_income": "foreign_source_income",
                        "total_capital_gains_distributions": "capital_gains",
                    }
                    if self._key == "drp_reinvestment_count":
                        count = 0
                        for p in payouts:
                            drp = p.get('drp_trade_attributes')
                            if isinstance(drp, dict) and drp.get('dividend_reinvested'):
                                count += 1
                        return count
                    payout_field = field_map.get(self._key)
                    total = 0.0
                    has_any = False
                    for p in payouts:
                        if not isinstance(p, dict):
                            continue
                        val = p.get(payout_field)
                        if val is not None:
                            try:
                                total += float(val)
                                has_any = True
                            except (ValueError, TypeError):
                                pass
                    return round(total, 2) if has_any else None
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
                elif self._key == "diversity_group_count":
                    breakdown = diversity_data.get('breakdown', [])
                    return len(breakdown)
            # Trades sensors
            elif self._sub_key == "trades":
                trades_data = self._coordinator.data.get('trades', {})
                trades_list = trades_data.get('trades', [])

                def _trade_value(t):
                    """Best-effort extraction of a trade's monetary value."""
                    for f in ('value', 'cost_base', 'amount'):
                        v = t.get(f)
                        if v is not None:
                            try:
                                return float(v)
                            except (ValueError, TypeError):
                                pass
                    price = t.get('price', 0)
                    quantity = t.get('quantity', 0)
                    if price and quantity:
                        try:
                            return float(price) * float(quantity)
                        except (ValueError, TypeError):
                            pass
                    return 0.0

                def _trade_type(t):
                    return (
                        t.get('trade_type')
                        or t.get('type')
                        or t.get('transaction_type')
                        or ''
                    ).upper()

                def _trade_symbol(t):
                    return (
                        t.get('symbol')
                        or t.get('code')
                        or t.get('instrument_code')
                        or (t.get('instrument', {}) or {}).get('code', '')
                        or (t.get('instrument', {}) or {}).get('symbol', '')
                    )

                if self._key == "total_trades":
                    return len(trades_list)
                elif self._key == "buy_count":
                    count = 0
                    for t in trades_list:
                        tt = (t.get('trade_type') or t.get('type') or t.get('transaction_type') or '').upper()
                        if tt in ('BUY', 'OPENING BALANCE', 'SPLIT'):
                            count += 1
                    return count
                elif self._key == "sell_count":
                    count = 0
                    for t in trades_list:
                        tt = (t.get('trade_type') or t.get('type') or t.get('transaction_type') or '').upper()
                        if tt in ('SELL',):
                            count += 1
                    return count
                elif self._key == "trade_count_30d":
                    if not trades_list:
                        return 0
                    cutoff = (dt_util.now().date() - timedelta(days=30)).isoformat()
                    count = 0
                    for t in trades_list:
                        trade_date = (
                            t.get('transaction_date')
                            or t.get('trade_date')
                            or t.get('date')
                            or t.get('traded_at', '')
                        )
                        if trade_date and str(trade_date)[:10] >= cutoff:
                            count += 1
                    return count
                elif self._key == "total_buy_value":
                    if not trades_list:
                        return 0
                    total = 0.0
                    for t in trades_list:
                        if _trade_type(t) in ('BUY', 'OPENING BALANCE'):
                            total += _trade_value(t)
                    return round(total, 2)
                elif self._key == "total_sell_value":
                    if not trades_list:
                        return 0
                    total = 0.0
                    for t in trades_list:
                        if _trade_type(t) == 'SELL':
                            total += _trade_value(t)
                    return round(total, 2)
                elif self._key == "net_trade_flow":
                    if not trades_list:
                        return 0
                    buy_total = sum(
                        _trade_value(t) for t in trades_list
                        if _trade_type(t) in ('BUY', 'OPENING BALANCE')
                    )
                    sell_total = sum(
                        _trade_value(t) for t in trades_list
                        if _trade_type(t) == 'SELL'
                    )
                    return round(buy_total - sell_total, 2)
                elif self._key in ("largest_trade_value", "largest_trade_symbol"):
                    if not trades_list:
                        return None
                    try:
                        largest = max(trades_list, key=_trade_value)
                    except (ValueError, TypeError):
                        return None
                    if self._key == "largest_trade_value":
                        return round(_trade_value(largest), 2)
                    return _trade_symbol(largest) or None
                else:
                    # last_trade_date, last_trade_symbol, last_trade_type, last_trade_value
                    if not trades_list:
                        return None
                    try:
                        sorted_trades = sorted(
                            trades_list,
                            key=lambda t: (
                                t.get('transaction_date')
                                or t.get('trade_date')
                                or t.get('date')
                                or t.get('traded_at', '')
                            ),
                            reverse=True
                        )
                        last = sorted_trades[0]
                        if self._key == "last_trade_date":
                            return (
                                last.get('transaction_date')
                                or last.get('trade_date')
                                or last.get('date')
                                or last.get('traded_at')
                            )
                        elif self._key == "last_trade_symbol":
                            return (
                                last.get('symbol')
                                or last.get('code')
                                or last.get('instrument_code')
                                or (last.get('instrument', {}) or {}).get('code', '')
                                or (last.get('instrument', {}) or {}).get('symbol', '')
                            )
                        elif self._key == "last_trade_type":
                            return last.get('trade_type') or last.get('type') or last.get('transaction_type')
                        elif self._key == "last_trade_value":
                            val = last.get('value') or last.get('cost_base') or last.get('amount')
                            if val is not None:
                                try:
                                    return round(float(val), 2)
                                except (ValueError, TypeError):
                                    pass
                            # Compute from price * quantity
                            price = last.get('price', 0)
                            quantity = last.get('quantity', 0)
                            if price and quantity:
                                try:
                                    return round(float(price) * float(quantity), 2)
                                except (ValueError, TypeError):
                                    pass
                            return None
                    except (TypeError, ValueError, IndexError):
                        return None
            # Contributions sensors
            elif self._sub_key == "contributions":
                summary = _get_contributions_summary(
                    self._coordinator.data.get("cash_account_transactions", {})
                )
                if self._key == "net_investment_gain":
                    # Portfolio value minus net contributions
                    portfolio_value = self._coordinator.data.get('report', {}).get('value')
                    net_contrib = summary.get('net_contributions', 0)
                    if portfolio_value is not None:
                        try:
                            return round(float(portfolio_value) - float(net_contrib), 2)
                        except (ValueError, TypeError):
                            pass
                    return None
                elif self._key == "net_investment_gain_percent":
                    portfolio_value = self._coordinator.data.get('report', {}).get('value')
                    net_contrib = summary.get('net_contributions', 0)
                    if portfolio_value is not None and net_contrib and float(net_contrib) != 0:
                        try:
                            gain = float(portfolio_value) - float(net_contrib)
                            return round(gain / float(net_contrib) * 100, 2)
                        except (ValueError, TypeError, ZeroDivisionError):
                            pass
                    return None
                return summary.get(self._key)
            # Portfolio metadata sensors
            elif self._sub_key == "portfolio_detail":
                detail = self._coordinator.data.get('portfolio_detail', {})
                if not isinstance(detail, dict):
                    return None
                if self._key == "portfolio_age_days":
                    inception = detail.get("inception_date")
                    if not inception:
                        return None
                    try:
                        start = datetime.strptime(str(inception)[:10], "%Y-%m-%d").date()
                        return (dt_util.now().date() - start).days
                    except (ValueError, TypeError):
                        return None
                return detail.get(self._key)
            # Cash account transaction analytics
            elif self._sub_key == "cash_account_transactions":
                cash_tx_data = self._coordinator.data.get('cash_account_transactions', {})
                transactions = []
                if isinstance(cash_tx_data, dict):
                    transactions = cash_tx_data.get('cash_account_transactions', []) or []
                if self._key == "cash_transaction_count":
                    return len(transactions)
                if not transactions:
                    return None
                try:
                    sorted_tx = sorted(
                        transactions,
                        key=lambda t: str(t.get('date_time') or t.get('date') or ''),
                        reverse=True,
                    )
                    last = sorted_tx[0] if sorted_tx else None
                except (TypeError, ValueError):
                    last = None
                if not last:
                    return None
                if self._key == "last_cash_transaction_date":
                    dt = last.get('date_time') or last.get('date')
                    return str(dt)[:10] if dt else None
                if self._key == "last_cash_transaction_amount":
                    amt = last.get('amount')
                    try:
                        return round(float(amt), 2) if amt is not None else None
                    except (ValueError, TypeError):
                        return None
                return None
            # Integration diagnostics
            elif self._sub_key == "_integration":
                if self._key == "last_update_timestamp":
                    ts = getattr(self._coordinator, 'last_update_success_time', None)
                    if ts is None:
                        ts = getattr(self._coordinator, 'last_update_time', None)
                    return ts
                if self._key == "update_interval_seconds":
                    interval = getattr(self._coordinator, 'update_interval', None)
                    if interval is None:
                        return None
                    try:
                        return int(interval.total_seconds())
                    except AttributeError:
                        return None
                if self._key == "optional_endpoints_on_cooldown":
                    cooldown = getattr(self._coordinator, '_optional_endpoint_cooldowns', None)
                    cash_cooldown = getattr(self._coordinator, '_cash_tx_account_cooldowns', None)
                    now = time.monotonic()
                    active = 0
                    if isinstance(cooldown, dict):
                        for info in cooldown.values():
                            if isinstance(info, dict) and info.get("next_retry", 0) > now:
                                active += 1
                    if isinstance(cash_cooldown, dict):
                        for info in cash_cooldown.values():
                            if isinstance(info, dict) and info.get("next_retry", 0) > now:
                                active += 1
                    return active
                return None
            else:
                return self._coordinator.data[self._sub_key][0][self._key]

        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("Error accessing data for key '%s': %s: %s", self._key, type(e).__name__, e)
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

    @property
    def available(self) -> bool:
        """Remain available when stale-but-valid coordinator data exists.

        The coordinator is designed to return old data on transient failures,
        so as long as data has ever been populated, entities stay available.
        This prevents the "entity is unavailable, remove it" prompt caused by
        transient API hiccups during polling.
        """
        if self._coordinator.data:
            return True
        # Fall back to HA's default: check last_update_success.  This only
        # matters on the very first poll cycle before any data exists.
        return self._coordinator.last_update_success

