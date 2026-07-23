from homeassistant.const import CURRENCY_DOLLAR
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.components.sensor import (
    SensorEntity
)
from .const import APP_VERSION
from .data import SharesightConfigEntry
import logging
from time import monotonic
from datetime import datetime, time, timedelta
from homeassistant.util import dt as dt_util
from .enum import (
    SENSOR_DESCRIPTIONS,
    MARKET_SENSOR_DESCRIPTIONS,
    CASH_SENSOR_DESCRIPTIONS,
    ALL_HOLDING_DESCRIPTIONS,
    TAX_SENSOR_DESCRIPTIONS,
    BENCHMARK_SENSOR_DESCRIPTIONS,
    SECTOR_SENSOR_DESCRIPTIONS,
    ACCOUNT_SENSOR_DESCRIPTIONS,
    WATCHLIST_SENSOR_DESCRIPTIONS,
    FX_SENSOR_DESCRIPTIONS,
    MARKET_HOURS_SENSOR_DESCRIPTIONS,
    ANALYTICS_SENSOR_DESCRIPTIONS,
    TOTALS_SENSOR_DESCRIPTIONS,
    WATCHLIST_INSTRUMENT_SENSOR_DESCRIPTIONS,
    NEWS_SENSOR_DESCRIPTIONS,
    VALUE_TREND_SENSOR_DESCRIPTIONS,
    LABEL_SENSOR_DESCRIPTIONS,
)
from . import analytics
from .coordinator import SharesightCoordinator
from .entity import SharesightBaseEntity

_LOGGER: logging.Logger = logging.getLogger(__package__)

# Entities are updated from the DataUpdateCoordinator (no per-entity I/O), so
# no parallel-update limit is needed. Declaring this satisfies the HA quality
# scale's parallel-updates rule.
PARALLEL_UPDATES = 0

# Cap the per-watchlist-instrument sensor fan-out (W1) so a large watchlist
# can't spawn an unbounded number of entities.
WATCHLIST_INSTRUMENT_CAP = 50


def _slug_symbol(value):
    """Slugify an instrument code / label into a registry-safe unique_id token.

    Mirrors the entity_id slug rules used in SharesightSensor.__init__ so codes
    like "BRK.B" or labels with spaces/punctuation collapse to a stable
    lowercase [a-z0-9_] token.
    """
    slug = "".join(
        c if c.isalnum() else "_"
        for c in str(value).lower().replace(" ", "_")
    )
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")


def _watchlist_instruments_for_discovery(data, cap=WATCHLIST_INSTRUMENT_CAP):
    """Distinct (code, currency_code) tuples for watchlist-instrument sensors.

    Reads the V3 watchlist.json items already fetched by the coordinator,
    de-duplicates by instrument code, and stops after ``cap`` instruments (W1).
    currency_code comes from the item's price currency so the price sensor can
    render in the instrument's own currency; it is None when absent.
    """
    if not isinstance(data, dict):
        return []
    watchlist_data = data.get("watchlist", {})
    items = watchlist_data.get("watchlist", []) if isinstance(watchlist_data, dict) else []
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        instrument = item.get("instrument") or {}
        code = instrument.get("code") or item.get("code")
        if not code or code in seen:
            continue
        seen.add(code)
        price = item.get("price") or {}
        currency = (price.get("currency") or {}).get("code")
        out.append((str(code), currency))
        if len(out) >= cap:
            break
    return out


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


def _get_diversity_top_markets(diversity_data, n=5):
    """Get top N markets by percentage as a list of dicts (length always == n)."""
    empty: list[dict] = [{} for _ in range(n)]
    if not diversity_data:
        _LOGGER.debug("diversity_data is empty")
        return empty

    try:
        breakdown = sorted(
            diversity_data.get('breakdown', []),
            key=lambda x: float(x.get('percentage', 0)),
            reverse=True
        )

        if not breakdown:
            _LOGGER.debug("No breakdown in diversity_data. Keys: %s", list(diversity_data.keys()))
            return empty

        result = [{} for _ in range(n)]
        for i in range(min(n, len(breakdown))):
            result[i] = {
                'name': breakdown[i].get('group_name'),
                'percent': breakdown[i].get('percentage'),
                'value': breakdown[i].get('value')
            }
            _LOGGER.debug("Market %s: %s - %s%%", i + 1, breakdown[i].get('group_name'), breakdown[i].get('percentage'))

        return result
    except (ValueError, TypeError, KeyError) as e:
        _LOGGER.debug(
            "Error in _get_diversity_top_markets: %s, sample breakdown: %s",
            e,
            diversity_data.get('breakdown', [{}])[0] if diversity_data.get('breakdown') else 'no breakdown',
        )
        return empty


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
    contribution_count = 0
    withdrawal_count = 0
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
            contribution_count += 1
        elif amount < 0:
            total_withdrawals += abs(amount)
            withdrawal_count += 1

        dt = tx.get("date_time") or tx.get("date")
        if dt:
            dt_value = str(dt)
            if latest is None or dt_value > latest.get("date_time", ""):
                latest = {"date_time": dt_value, "amount": amount}

    avg_contribution = (
        round(total_contributions / contribution_count, 2)
        if contribution_count
        else None
    )

    return {
        "total_contributions": round(total_contributions, 2),
        "total_withdrawals": round(total_withdrawals, 2),
        "net_contributions": round(total_contributions - total_withdrawals, 2),
        "last_contribution_date": latest.get("date_time", "")[:10] if latest else None,
        "last_contribution_amount": round(float(latest.get("amount")), 2) if latest else None,
        "contribution_count": contribution_count,
        "withdrawal_count": withdrawal_count,
        "average_contribution_amount": avg_contribution,
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


def _watchlist_metric(items, key):
    """Compute a watchlist overview metric from the watchlist.json items."""
    parsed = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        instrument = item.get('instrument') or {}
        price = item.get('price') or {}
        code = instrument.get('code') or item.get('code')
        diff = price.get('diff_percent')
        try:
            diff = float(diff) if diff is not None else None
        except (ValueError, TypeError):
            diff = None
        parsed.append({'code': code, 'diff': diff})

    if key == "watchlist_count":
        return len(parsed)

    with_diff = [p for p in parsed if p['diff'] is not None]
    if key == "watchlist_up_count":
        return sum(1 for p in with_diff if p['diff'] > 0)
    if key == "watchlist_down_count":
        return sum(1 for p in with_diff if p['diff'] < 0)
    if key == "watchlist_average_percent":
        if not with_diff:
            return None
        return round(sum(p['diff'] for p in with_diff) / len(with_diff), 2)
    if not with_diff:
        return None
    if key == "watchlist_top_gainer_symbol":
        return max(with_diff, key=lambda p: p['diff'])['code']
    if key == "watchlist_top_gainer_percent":
        return round(max(with_diff, key=lambda p: p['diff'])['diff'], 2)
    if key == "watchlist_top_loser_symbol":
        return min(with_diff, key=lambda p: p['diff'])['code']
    if key == "watchlist_top_loser_percent":
        return round(min(with_diff, key=lambda p: p['diff'])['diff'], 2)
    return None


def _foreign_currency_codes(data, base_currency):
    """Foreign currency codes held (for FX rate sensor discovery)."""
    if not isinstance(data, dict):
        return []
    base = str(base_currency or "").upper()
    return [c for c in analytics.portfolio_currency_codes(data) if c and c != base]


def _held_market_codes(data):
    """Distinct market codes across current holdings (sorted, de-duplicated)."""
    if not isinstance(data, dict):
        return []
    holdings = data.get("holdings", {})
    holdings_list = holdings.get("holdings", []) if isinstance(holdings, dict) else []
    codes: set[str] = set()
    for holding in holdings_list:
        market = analytics.holding_market(holding)
        if market:
            codes.add(str(market))
    return sorted(codes)


def _market_hours_status(market, now):
    """Compute (is_open, next_open, next_close) for a market from its hours.

    ``market`` is a Sharesight markets[] entry (tz_name + trading_start_time +
    trading_end_time as HH:MM).  ``now`` is an aware datetime.  Weekends are
    treated as closed; public holidays and half-days are NOT modelled, so the
    signal is approximate.  Returns aware datetimes (or None) for the next
    open/close boundaries.
    """
    tz_name = market.get("tz_name")
    start_raw = market.get("trading_start_time")
    end_raw = market.get("trading_end_time")
    if not (tz_name and start_raw and end_raw):
        return None, None, None
    tz = dt_util.get_time_zone(tz_name)
    if tz is None:
        return None, None, None
    try:
        start_h, start_m = (int(x) for x in str(start_raw).split(":")[:2])
        end_h, end_m = (int(x) for x in str(end_raw).split(":")[:2])
    except (ValueError, TypeError):
        return None, None, None

    local_now = now.astimezone(tz)
    start_t = time(start_h, start_m)
    end_t = time(end_h, end_m)

    def _is_trading_day(d):
        return d.weekday() < 5

    is_open = _is_trading_day(local_now) and start_t <= local_now.time() <= end_t

    next_open = None
    next_close = None
    for offset in range(0, 9):
        day = (local_now + timedelta(days=offset)).date()
        if not _is_trading_day(day):
            continue
        open_dt = datetime.combine(day, start_t, tzinfo=tz)
        close_dt = datetime.combine(day, end_t, tzinfo=tz)
        if next_open is None and open_dt > local_now:
            next_open = open_dt
        if next_close is None and close_dt > local_now:
            next_close = close_dt
        if next_open and next_close:
            break

    return (
        is_open,
        next_open.astimezone(dt_util.UTC) if next_open else None,
        next_close.astimezone(dt_util.UTC) if next_close else None,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SharesightConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime_data = entry.runtime_data

    coordinator: SharesightCoordinator = runtime_data.coordinator
    portfolio_id = runtime_data.portfolio_id
    edge = runtime_data.edge
    portfolios = coordinator.data.get("portfolios", [])
    local_currency = "USD"
    if portfolios and isinstance(portfolios[0], dict):
        local_currency = portfolios[0].get("currency_code", "USD")
    elif isinstance(coordinator.data.get("report", {}).get("currency"), dict):
        local_currency = coordinator.data.get("report", {}).get("currency", {}).get("code", "USD")

    entry_id = entry.entry_id  # noqa: F841 — retained for logging/debug parity
    # runtime-data (Bronze): the three fan-out tracking lists live on
    # entry.runtime_data (created in __init__.async_setup_entry, cleared with
    # the entry on unload).  The update_sensors closure below appends the
    # display names of dynamically-discovered entities to them.
    market_sensors: list[str] = runtime_data.market_sensors
    cash_sensors: list[str] = runtime_data.cash_sensors
    holding_sensors: list[str] = runtime_data.holding_sensors

    sensors = []
    seen_unique_ids: set[str] = set()

    for sensor in SENSOR_DESCRIPTIONS:
        sensors.append(SharesightSensor(sensor, entry, coordinator,
                                        local_currency, portfolio_id, edge))

    # Capital gains tax sensors: the underlying V2 reports are AU-only, so
    # only create them for Australian portfolios.
    portfolio_detail = coordinator.data.get("portfolio_detail", {})
    if str(portfolio_detail.get("country_code", "")).upper() == "AU":
        for sensor in TAX_SENSOR_DESCRIPTIONS:
            sensors.append(SharesightSensor(sensor, entry, coordinator,
                                            local_currency, portfolio_id, edge))

    # Benchmark sensors: only once the benchmark report has returned data
    # (requires a benchmark to be configured on the portfolio).  If it shows
    # up later, update_sensors() below adds them dynamically.
    benchmark_added: list[str] = []

    def _has_benchmark_data(coordinator_data) -> bool:
        bench = coordinator_data.get("benchmark")
        return isinstance(bench, dict) and bool(bench.get("instrument"))

    if _has_benchmark_data(coordinator.data):
        for sensor in BENCHMARK_SENSOR_DESCRIPTIONS:
            sensors.append(SharesightSensor(sensor, entry, coordinator,
                                            local_currency, portfolio_id, edge))
            benchmark_added.append(str(sensor.name))

    # Portfolio Totals sensors: only once the v3 /totals endpoint has returned
    # data.  It parks via the optional-endpoint backoff for scope-gated tokens,
    # so gating on key-presence (like benchmark) avoids phantom entities.  If it
    # comes online later, update_sensors() below adds them dynamically.
    totals_added: list[str] = []

    def _has_totals_data(coordinator_data) -> bool:
        totals = coordinator_data.get("totals")
        return isinstance(totals, dict) and bool(totals)

    if _has_totals_data(coordinator.data):
        for sensor in TOTALS_SENSOR_DESCRIPTIONS:
            sensors.append(SharesightSensor(sensor, entry, coordinator,
                                            local_currency, portfolio_id, edge))
            totals_added.append(str(sensor.name))

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
        for holding_sensor in ALL_HOLDING_DESCRIPTIONS:
            display_name = f"{symbol} {holding_sensor.sub_key.replace('_', ' ')}"
            uid = f"{portfolio_id}_{symbol}_{holding_sensor.sub_key}_{holding_sensor.key}_{APP_VERSION}"
            if uid in seen_unique_ids:
                continue
            seen_unique_ids.add(uid)
            new_sensor = SharesightSensor(holding_sensor, entry, coordinator,
                                          local_currency, portfolio_id, edge, 0, symbol, display_name)
            sensors.append(new_sensor)
            holding_sensors.append(display_name)

    # Portfolio sector/industry allocation + account + watchlist + analytics
    # devices.  These are fixed sensor sets (no per-item fan-out).  Analytics
    # is derived every poll at zero API cost, so it is always created.
    # NEWS + VALUE_TREND are portfolio-device sensors that read the optional
    # instrument_news / value_trend keys; like the watchlist overview they are
    # always created and simply report None until their endpoint comes online.
    for sensor in (
        SECTOR_SENSOR_DESCRIPTIONS
        + ACCOUNT_SENSOR_DESCRIPTIONS
        + WATCHLIST_SENSOR_DESCRIPTIONS
        + ANALYTICS_SENSOR_DESCRIPTIONS
        + NEWS_SENSOR_DESCRIPTIONS
        + VALUE_TREND_SENSOR_DESCRIPTIONS
    ):
        sensors.append(SharesightSensor(sensor, entry, coordinator,
                                        local_currency, portfolio_id, edge))

    # Dynamic FX rate sensors — one per foreign currency held.
    fx_sensors: list[str] = []
    base_currency = local_currency
    for code in _foreign_currency_codes(coordinator.data, base_currency):
        for fx_sensor in FX_SENSOR_DESCRIPTIONS:
            display_name = f"{code} to {base_currency} rate"
            uid = f"{portfolio_id}_fx_{code}_{fx_sensor.key}_{APP_VERSION}"
            if uid in seen_unique_ids:
                continue
            seen_unique_ids.add(uid)
            sensors.append(SharesightSensor(fx_sensor, entry, coordinator,
                                            local_currency, portfolio_id, edge, 0, code, display_name))
            fx_sensors.append(display_name)

    # Dynamic market trading-hours sensors — one set per held market.
    market_hours_sensors: list[str] = []
    for market_code in _held_market_codes(coordinator.data):
        for mh_sensor in MARKET_HOURS_SENSOR_DESCRIPTIONS:
            display_name = f"{market_code} {mh_sensor.name}"
            uid = f"{portfolio_id}_market_hours_{market_code}_{mh_sensor.key}_{APP_VERSION}"
            if uid in seen_unique_ids:
                continue
            seen_unique_ids.add(uid)
            sensors.append(SharesightSensor(mh_sensor, entry, coordinator,
                                            local_currency, portfolio_id, edge, 0, market_code, display_name))
            market_hours_sensors.append(display_name)

    # Dynamic per-watchlist-instrument price + day-change sensors (W1) — one set
    # per watched instrument (capped), sharing the single watchlist device.
    watchlist_instrument_sensors: list[str] = []
    for wl_code, wl_currency in _watchlist_instruments_for_discovery(coordinator.data):
        inst_currency = wl_currency or local_currency
        for wl_sensor in WATCHLIST_INSTRUMENT_SENSOR_DESCRIPTIONS:
            display_name = f"Watchlist {wl_code} {wl_sensor.name}"
            uid = f"{portfolio_id}_watchlist_{_slug_symbol(wl_code)}_{wl_sensor.key}_{APP_VERSION}"
            if uid in seen_unique_ids:
                continue
            seen_unique_ids.add(uid)
            sensors.append(SharesightSensor(wl_sensor, entry, coordinator,
                                            inst_currency, portfolio_id, edge, 0, wl_code, display_name))
            watchlist_instrument_sensors.append(display_name)

    # Dynamic per-label value + percent sensors (W7) in the "labels" device
    # group.  Gated on the coordinator emitting label_allocation (present only
    # when at least one holding carries a label).
    label_sensors: list[str] = []
    label_allocation = coordinator.data.get("label_allocation")
    if isinstance(label_allocation, list):
        for label_entry in label_allocation:
            if not isinstance(label_entry, dict):
                continue
            label_name = label_entry.get("label")
            if not label_name:
                continue
            for label_sensor in LABEL_SENSOR_DESCRIPTIONS:
                display_name = f"{label_name} {label_sensor.name}"
                uid = f"{portfolio_id}_label_{_slug_symbol(label_name)}_{label_sensor.key}_{APP_VERSION}"
                if uid in seen_unique_ids:
                    continue
                seen_unique_ids.add(uid)
                sensors.append(SharesightSensor(label_sensor, entry, coordinator,
                                                local_currency, portfolio_id, edge, 0, label_name, display_name))
                label_sensors.append(display_name)

    async_add_entities(sensors, True)

    @callback
    def update_sensors() -> None:
        """Discover new markets/cash accounts/holdings after a coordinator refresh."""
        _LOGGER.debug("Checking for new market/cash/holding sensors")
        # This listener is detached via entry.async_on_unload below, so it never
        # fires after the entry unloads; read the coordinator captured at setup.
        update_coordinator: SharesightCoordinator = coordinator
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

        # Benchmark sensors appear once the user configures a benchmark
        if _has_benchmark_data(update_coordinator.data) and not benchmark_added:
            new_benchmark_sensors = []
            for benchmark_sensor in BENCHMARK_SENSOR_DESCRIPTIONS:
                new_benchmark_sensors.append(
                    SharesightSensor(benchmark_sensor, entry, update_coordinator,
                                     local_currency, portfolio_id, edge))
                benchmark_added.append(str(benchmark_sensor.name))
            async_add_entities(new_benchmark_sensors, True)

        # Portfolio Totals sensors appear once the /totals endpoint comes online
        if _has_totals_data(update_coordinator.data) and not totals_added:
            new_totals_sensors = []
            for totals_sensor in TOTALS_SENSOR_DESCRIPTIONS:
                new_totals_sensors.append(
                    SharesightSensor(totals_sensor, entry, update_coordinator,
                                     local_currency, portfolio_id, edge))
                totals_added.append(str(totals_sensor.name))
            async_add_entities(new_totals_sensors, True)

        # Check for new holdings
        update_holdings_data = update_coordinator.data.get("holdings", {})
        update_holdings_list = update_holdings_data.get("holdings", []) if isinstance(update_holdings_data, dict) else []
        for update_holding in update_holdings_list:
            __holding_symbol = _get_holding_symbol(update_holding)
            if not __holding_symbol:
                continue
            for update_holding_sensor in ALL_HOLDING_DESCRIPTIONS:
                update_holding_display_name = f"{__holding_symbol} {update_holding_sensor.sub_key.replace('_', ' ')}"
                if update_holding_display_name not in holding_sensors:
                    update_new_holding_sensor = SharesightSensor(
                        update_holding_sensor, entry, update_coordinator,
                        local_currency, portfolio_id, edge, 0, __holding_symbol,
                        update_holding_display_name)
                    async_add_entities([update_new_holding_sensor], True)
                    holding_sensors.append(update_holding_display_name)

        # New foreign currencies (FX rate sensors)
        for __code in _foreign_currency_codes(update_coordinator.data, local_currency):
            __fx_display = f"{__code} to {local_currency} rate"
            if __fx_display not in fx_sensors:
                for fx_sensor in FX_SENSOR_DESCRIPTIONS:
                    async_add_entities([SharesightSensor(
                        fx_sensor, entry, update_coordinator, local_currency,
                        portfolio_id, edge, 0, __code, __fx_display)], True)
                    fx_sensors.append(__fx_display)

        # New held markets (trading-hours sensors)
        for __market_code in _held_market_codes(update_coordinator.data):
            for mh_sensor in MARKET_HOURS_SENSOR_DESCRIPTIONS:
                __mh_display = f"{__market_code} {mh_sensor.name}"
                if __mh_display not in market_hours_sensors:
                    async_add_entities([SharesightSensor(
                        mh_sensor, entry, update_coordinator, local_currency,
                        portfolio_id, edge, 0, __market_code, __mh_display)], True)
                    market_hours_sensors.append(__mh_display)

        # New watchlist instruments (per-instrument price + day-change sensors, W1).
        # Dedupe on the computed unique_id (as setup does) — distinct codes can
        # slugify to the same token (e.g. "BRK.B" vs "BRK-B"), so a display-name
        # guard would let a colliding unique_id through and HA would reject it.
        for __wl_code, __wl_currency in _watchlist_instruments_for_discovery(update_coordinator.data):
            __inst_currency = __wl_currency or local_currency
            for wl_sensor in WATCHLIST_INSTRUMENT_SENSOR_DESCRIPTIONS:
                __wl_display = f"Watchlist {__wl_code} {wl_sensor.name}"
                __wl_uid = f"{portfolio_id}_watchlist_{_slug_symbol(__wl_code)}_{wl_sensor.key}_{APP_VERSION}"
                if __wl_uid not in seen_unique_ids:
                    seen_unique_ids.add(__wl_uid)
                    async_add_entities([SharesightSensor(
                        wl_sensor, entry, update_coordinator, __inst_currency,
                        portfolio_id, edge, 0, __wl_code, __wl_display)], True)
                    watchlist_instrument_sensors.append(__wl_display)

        # New labels (per-label value + percent sensors, W7).  Gated on the
        # coordinator emitting label_allocation, so a portfolio that only later
        # gains a label grows the "labels" device on the first poll it appears.
        __update_label_allocation = update_coordinator.data.get("label_allocation")
        if isinstance(__update_label_allocation, list):
            for __label_entry in __update_label_allocation:
                if not isinstance(__label_entry, dict):
                    continue
                __label_name = __label_entry.get("label")
                if not __label_name:
                    continue
                for label_sensor in LABEL_SENSOR_DESCRIPTIONS:
                    __label_display = f"{__label_name} {label_sensor.name}"
                    # Dedupe on the computed unique_id (as setup does): distinct
                    # labels can slugify to the same token, so a display-name
                    # guard would let a colliding unique_id through.
                    __label_uid = f"{portfolio_id}_label_{_slug_symbol(__label_name)}_{label_sensor.key}_{APP_VERSION}"
                    if __label_uid not in seen_unique_ids:
                        seen_unique_ids.add(__label_uid)
                        async_add_entities([SharesightSensor(
                            label_sensor, entry, update_coordinator, local_currency,
                            portfolio_id, edge, 0, __label_name, __label_display)], True)
                        label_sensors.append(__label_display)

    # Piggy-back on the coordinator's own update cycle rather than running a
    # second time interval.  New markets/holdings appear as soon as the next
    # successful poll brings them in.  entry.async_on_unload detaches the
    # listener when the entry unloads (runtime-data / async-on-unload, Bronze).
    entry.async_on_unload(coordinator.async_add_listener(update_sensors))


class SharesightSensor(SharesightBaseEntity, SensorEntity):
    def __init__(self, sensor, entry, coordinator, currency, portfolio_id, edge, index=0, local_name="", display_name=""):
        super().__init__(coordinator, portfolio_id, edge)
        self._state_class = sensor.state_class
        self._coordinator = coordinator
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
        self._currency_code = currency

        # Modern HA naming: the display name is the device name plus this
        # description's translated entity name.  entity_id is still hand-assigned
        # below (slug of self._name), so switching to translations renames no
        # existing entity.  Per-item families that share one device with their
        # siblings (FX rates, market hours, watchlist instruments, labels) carry
        # the varying item in the name through a per-entity placeholder; the
        # per-item-device families (market/cash/holding) need none because their
        # own device already names the item.
        self._attr_translation_key = sensor.translation_key
        if self._device_group == "fx":
            self._attr_translation_placeholders = {
                "code": local_name,
                "base": self._currency_code,
            }
        elif self._device_group == "market_hours":
            self._attr_translation_placeholders = {"market": local_name}
        elif self._sub_key == "watchlist_instrument":
            self._attr_translation_placeholders = {"code": local_name}
        elif self._sub_key == "label_allocation":
            self._attr_translation_placeholders = {"label": local_name}

        # Propagate entity_registry_enabled_default from description
        if hasattr(sensor, 'entity_registry_enabled_default') and not sensor.entity_registry_enabled_default:
            self._attr_entity_registry_enabled_default = False
        else:
            self._attr_entity_registry_enabled_default = True

        if sensor.native_unit_of_measurement == CURRENCY_DOLLAR:
            self._native_unit_of_measurement = currency
        else:
            self._native_unit_of_measurement = sensor.native_unit_of_measurement

        # Sanitise to a valid entity_id slug: lowercase, only [a-z0-9_], no
        # leading/trailing/duplicate underscores. Names like
        # "Total Non-Resident Withholding Tax" or "Update Interval (s)" would
        # otherwise produce invalid IDs (rejected by HA from 2027.2.0).
        slug = "".join(
            c if c.isalnum() else "_"
            for c in self._name.lower().replace(" ", "_")
        )
        while "__" in slug:
            slug = slug.replace("__", "_")
        slug = slug.strip("_")
        base_entity_id = f"{slug}_{self._portfolio_id}"
        self.entity_id = f"sensor.{base_entity_id}"

        # edge_name / base_model mirror the base class's edge infix and are
        # consumed unchanged by the device_group_config table and the dynamic
        # market/cash/holding branches below.  The configuration URL now comes
        # from the base class (self._make_device_info), so the local edge_url /
        # base_config_url are no longer needed here.
        edge_name = self._edge_name
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
            "tax": {
                "name": f"Sharesight{edge_name}Tax (CGT)",
                "identifier": f"{self._portfolio_id}_tax",
                "model": f"{base_model} - Capital Gains Tax",
            },
            "benchmark": {
                "name": f"Sharesight{edge_name}Benchmark",
                "identifier": f"{self._portfolio_id}_benchmark",
                "model": f"{base_model} - Benchmark",
            },
            "sector": {
                "name": f"Sharesight{edge_name}Sector Allocation",
                "identifier": f"{self._portfolio_id}_sector",
                "model": f"{base_model} - Sector Allocation",
            },
            "account": {
                "name": f"Sharesight{edge_name}Account",
                "identifier": f"{self._portfolio_id}_account",
                "model": f"{base_model} - Account",
            },
            "watchlist": {
                "name": f"Sharesight{edge_name}Watchlist",
                "identifier": f"{self._portfolio_id}_watchlist",
                "model": f"{base_model} - Watchlist",
            },
            "fx": {
                "name": f"Sharesight{edge_name}Exchange Rates",
                "identifier": f"{self._portfolio_id}_fx",
                "model": f"{base_model} - Exchange Rates",
            },
            "market_hours": {
                "name": f"Sharesight{edge_name}Market Hours",
                "identifier": f"{self._portfolio_id}_market_hours",
                "model": f"{base_model} - Market Hours",
            },
            "analytics": {
                "name": f"Sharesight{edge_name}Analytics",
                "identifier": f"{self._portfolio_id}_analytics",
                "model": f"{base_model} - Analytics",
            },
            "totals": {
                "name": f"Sharesight{edge_name}Portfolio Totals",
                "identifier": f"{self._portfolio_id}_totals",
                "model": f"{base_model} - Portfolio Totals",
            },
            "labels": {
                "name": f"Sharesight{edge_name}Labels",
                "identifier": f"{self._portfolio_id}_labels",
                "model": f"{base_model} - Labels",
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

        self._attr_device_info = self._make_device_info(
            identifier=device_id, name=device_name, model=device_model
        )

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
            elif self._key in ("holding_fundamental", "holding_income", "holding_trade"):
                # Per-holding derived sensor — state computed in native_value.
                self._state = None
                self._unique_id = f"{self._portfolio_id}_holding_{local_name}_{self._sub_key}_{self._key}_{APP_VERSION}"
            elif self._device_group in ("fx", "market_hours"):
                # Dynamic per-currency / per-market sensor.
                self._state = None
                self._unique_id = f"{self._portfolio_id}_{self._device_group}_{local_name}_{self._key}_{APP_VERSION}"
            elif self._sub_key == "watchlist_instrument":
                # Per-watchlist-instrument sensor (W1). Slugify the instrument
                # code so codes like "BRK.B" produce a registry-safe unique_id.
                self._state = None
                self._unique_id = f"{self._portfolio_id}_watchlist_{_slug_symbol(local_name)}_{self._key}_{APP_VERSION}"
            elif self._sub_key == "label_allocation":
                # Per-label allocation sensor (W7). Slugify the (user-defined)
                # label name for a registry-safe unique_id.
                self._state = None
                self._unique_id = f"{self._portfolio_id}_label_{_slug_symbol(local_name)}_{self._key}_{APP_VERSION}"
            elif self._sub_key in ("sector_allocation", "industry_allocation", "my_user", "watchlist", "value_trend", "instrument_news"):
                self._state = None
                self._unique_id = f"{self._portfolio_id}_{self._sub_key}_{self._key}_{APP_VERSION}"
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
                elif self._key in ("positive_holdings_percent", "negative_holdings_percent"):
                    holdings_list = holdings_data.get('holdings', [])
                    if not holdings_list:
                        return None
                    total_count = len(holdings_list)
                    if total_count == 0:
                        return None
                    if self._key == "positive_holdings_percent":
                        matching = sum(1 for h in holdings_list if _get_holding_gain(h) > 0)
                    else:
                        matching = sum(1 for h in holdings_list if _get_holding_gain(h) < 0)
                    return round(matching / total_count * 100, 2)
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
                elif self._key == "median_holding_value":
                    holdings_list = holdings_data.get('holdings', [])
                    if not holdings_list:
                        return None
                    values = sorted(_get_holding_value(h) for h in holdings_list)
                    n = len(values)
                    if n == 0:
                        return None
                    if n % 2 == 1:
                        median = values[n // 2]
                    else:
                        median = (values[n // 2 - 1] + values[n // 2]) / 2
                    return round(median, 2)
                elif self._key in ("top_3_holdings_percent", "top_5_holdings_percent"):
                    n = 3 if self._key == "top_3_holdings_percent" else 5
                    holdings_list = holdings_data.get('holdings', [])
                    if not holdings_list:
                        return None
                    portfolio_value = float(holdings_data.get('value', 0) or 0)
                    if portfolio_value <= 0:
                        return None
                    top_n = sorted(holdings_list, key=_get_holding_value, reverse=True)[:n]
                    top_value = sum(_get_holding_value(h) for h in top_n)
                    return round(top_value / portfolio_value * 100, 2)
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
                elif self._key == "dividend_yield_percent":
                    total = income_summary.get('total_income')
                    portfolio_value = report_data.get('value')
                    if total is None or portfolio_value in (None, 0, 0.0):
                        return None
                    try:
                        return round(float(total) / float(portfolio_value) * 100, 2)
                    except (ValueError, TypeError, ZeroDivisionError):
                        return None
                elif self._key in (
                    "dividends_30d",
                    "dividends_ytd",
                    "dividends_ttm",
                    "dividends_prev_year",
                ):
                    payouts = income_data.get('payouts', [])
                    if not payouts:
                        return 0
                    today = dt_util.now().date()
                    cutoff_low = None
                    cutoff_high = None
                    if self._key == "dividends_30d":
                        cutoff_low = (today - timedelta(days=30)).isoformat()
                    elif self._key == "dividends_ytd":
                        cutoff_low = f"{today.year}-01-01"
                    elif self._key == "dividends_ttm":
                        cutoff_low = (today - timedelta(days=365)).isoformat()
                    else:  # dividends_prev_year
                        cutoff_low = f"{today.year - 1}-01-01"
                        cutoff_high = f"{today.year}-01-01"
                    total = 0.0
                    for p in payouts:
                        if not isinstance(p, dict):
                            continue
                        date = p.get('paid_on') or p.get('date') or p.get('ex_date')
                        if not date:
                            continue
                        d = str(date)[:10]
                        if cutoff_low and d < cutoff_low:
                            continue
                        if cutoff_high and d >= cutoff_high:
                            continue
                        try:
                            total += float(p.get('amount', 0) or 0)
                        except (ValueError, TypeError):
                            continue
                    return round(total, 2)
                elif self._key == "dividend_yield_ttm_percent":
                    payouts = income_data.get('payouts', [])
                    portfolio_value = report_data.get('value')
                    if not payouts or portfolio_value in (None, 0, 0.0):
                        return None
                    cutoff = (dt_util.now().date() - timedelta(days=365)).isoformat()
                    total = 0.0
                    for p in payouts:
                        if not isinstance(p, dict):
                            continue
                        date = p.get('paid_on') or p.get('date') or p.get('ex_date')
                        if not date or str(date)[:10] < cutoff:
                            continue
                        try:
                            total += float(p.get('amount', 0) or 0)
                        except (ValueError, TypeError):
                            continue
                    try:
                        return round(total / float(portfolio_value) * 100, 2)
                    except (ValueError, TypeError, ZeroDivisionError):
                        return None
                elif self._key == "upcoming_dividends_count":
                    # Historic payouts (inception→today) plus the dedicated
                    # forward window (today→+1y) the coordinator fetches.
                    payouts = (income_data.get('payouts', []) or []) + (
                        income_data.get('upcoming_payouts', []) or []
                    )
                    if not payouts:
                        return 0
                    today_iso = dt_util.now().date().isoformat()
                    seen: set[tuple] = set()
                    count = 0
                    for p in payouts:
                        if not isinstance(p, dict):
                            continue
                        ex = (
                            p.get('goes_ex_on')
                            or p.get('ex_date')
                            or p.get('paid_on')
                        )
                        if not ex or str(ex)[:10] < today_iso:
                            continue
                        dedupe_key = (p.get('id'), str(ex)[:10], p.get('amount'))
                        if dedupe_key in seen:
                            continue
                        seen.add(dedupe_key)
                        count += 1
                    return count
                elif self._key == "dividends_received_cash":
                    cash_tx_data = self._coordinator.data.get('cash_account_transactions', {})
                    transactions = []
                    if isinstance(cash_tx_data, dict):
                        transactions = cash_tx_data.get('cash_account_transactions', [])
                    if not transactions:
                        return 0
                    total = 0.0
                    for tx in transactions:
                        if not isinstance(tx, dict):
                            continue
                        tx_type = tx.get('type_name')
                        if not tx_type:
                            tx_type_obj = tx.get('cash_account_transaction_type')
                            if isinstance(tx_type_obj, dict):
                                tx_type = tx_type_obj.get('name')
                        tx_type = str(tx_type or "").upper()
                        if 'DIVIDEND' not in tx_type and 'INTEREST' not in tx_type:
                            continue
                        try:
                            total += float(tx.get('amount') or 0)
                        except (ValueError, TypeError):
                            continue
                    return round(total, 2)
                elif self._key in ("next_dividend_date", "next_dividend_amount", "next_dividend_symbol"):
                    payouts = (income_data.get('payouts', []) or []) + (
                        income_data.get('upcoming_payouts', []) or []
                    )
                    if not payouts:
                        return None
                    today_iso = dt_util.now().date().isoformat()
                    upcoming = []
                    for p in payouts:
                        if not isinstance(p, dict):
                            continue
                        ex = p.get('goes_ex_on') or p.get('ex_date') or p.get('paid_on')
                        if ex and str(ex)[:10] >= today_iso:
                            upcoming.append((str(ex)[:10], p))
                    if not upcoming:
                        return None
                    upcoming.sort(key=lambda x: x[0])
                    next_date, next_payout = upcoming[0]
                    if self._key == "next_dividend_date":
                        return next_date
                    if self._key == "next_dividend_amount":
                        try:
                            return round(float(next_payout.get('amount', 0) or 0), 2)
                        except (ValueError, TypeError):
                            return None
                    return (
                        next_payout.get('symbol')
                        or next_payout.get('instrument_code')
                        or (next_payout.get('holding', {}) or {}).get('instrument', {}).get('code', '')
                        or next_payout.get('company_name', '')
                        or None
                    )
                elif self._key in (
                    "forward_annual_income",
                    "forward_yield_percent",
                    "income_30d",
                    "income_90d",
                    "days_to_next",
                    "announced_income",
                ):
                    # Forward income forecast keys merged into income_report by
                    # the coordinator's derived-analytics block (Feature 6).
                    return income_data.get(self._key)
            # Diversity sensors
            elif self._sub_key == "diversity":
                diversity_data = self._coordinator.data.get('diversity', {})
                top_markets = _get_diversity_top_markets(diversity_data, n=5)
                # Pattern: market_<N>_<field>
                if self._key.startswith("market_") and "_" in self._key[7:]:
                    parts = self._key.split("_", 2)
                    try:
                        idx = int(parts[1]) - 1
                    except (ValueError, IndexError):
                        idx = -1
                    field = parts[2] if len(parts) >= 3 else ""
                    if 0 <= idx < len(top_markets):
                        m = top_markets[idx]
                        return m.get(field) if m else None
                    return None
                if self._key == "diversity_group_count":
                    breakdown = diversity_data.get('breakdown', [])
                    return len(breakdown)
                if self._key in ("top_3_markets_percent", "top_5_markets_percent"):
                    n = 3 if self._key == "top_3_markets_percent" else 5
                    breakdown = sorted(
                        diversity_data.get('breakdown', []),
                        key=lambda x: float(x.get('percentage', 0) or 0),
                        reverse=True,
                    )[:n]
                    if not breakdown:
                        return None
                    try:
                        return round(sum(float(m.get('percentage', 0) or 0) for m in breakdown), 2)
                    except (ValueError, TypeError):
                        return None
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
                elif self._key == "trade_count_7d":
                    if not trades_list:
                        return 0
                    cutoff = (dt_util.now().date() - timedelta(days=7)).isoformat()
                    count = 0
                    for t in trades_list:
                        td = (
                            t.get('transaction_date')
                            or t.get('trade_date')
                            or t.get('date')
                            or t.get('traded_at', '')
                        )
                        if td and str(td)[:10] >= cutoff:
                            count += 1
                    return count
                elif self._key == "trade_count_ytd":
                    if not trades_list:
                        return 0
                    cutoff = f"{dt_util.now().year}-01-01"
                    count = 0
                    for t in trades_list:
                        td = (
                            t.get('transaction_date')
                            or t.get('trade_date')
                            or t.get('date')
                            or t.get('traded_at', '')
                        )
                        if td and str(td)[:10] >= cutoff:
                            count += 1
                    return count
                elif self._key in ("average_trade_value", "average_buy_value", "average_sell_value"):
                    if not trades_list:
                        return None
                    if self._key == "average_buy_value":
                        relevant = [t for t in trades_list if _trade_type(t) in ('BUY', 'OPENING BALANCE')]
                    elif self._key == "average_sell_value":
                        relevant = [t for t in trades_list if _trade_type(t) == 'SELL']
                    else:
                        relevant = trades_list
                    if not relevant:
                        return None
                    total = sum(_trade_value(t) for t in relevant)
                    return round(total / len(relevant), 2)
                elif self._key == "total_brokerage":
                    if not trades_list:
                        return 0
                    total = 0.0
                    has_any = False
                    for t in trades_list:
                        for f in ('brokerage', 'fee', 'commission', 'fees'):
                            v = t.get(f)
                            if v is not None:
                                try:
                                    total += float(v)
                                    has_any = True
                                    break
                                except (ValueError, TypeError):
                                    pass
                    return round(total, 2) if has_any else None
                elif self._key == "most_traded_symbol":
                    if not trades_list:
                        return None
                    counts: dict[str, int] = {}
                    for t in trades_list:
                        sym = _trade_symbol(t) or ""
                        if sym:
                            counts[sym] = counts.get(sym, 0) + 1
                    if not counts:
                        return None
                    return max(counts.items(), key=lambda kv: kv[1])[0]
                elif self._key == "trades_per_month":
                    if not trades_list:
                        return None
                    # Span from portfolio inception (preferred) or earliest trade to today.
                    inception = (self._coordinator.data.get('portfolio_detail', {}) or {}).get('inception_date')
                    start_date = None
                    if inception:
                        try:
                            start_date = datetime.strptime(str(inception)[:10], "%Y-%m-%d").date()
                        except (ValueError, TypeError):
                            start_date = None
                    if start_date is None:
                        earliest = None
                        for t in trades_list:
                            td = (
                                t.get('transaction_date')
                                or t.get('trade_date')
                                or t.get('date')
                                or t.get('traded_at', '')
                            )
                            td10 = str(td or "")[:10]
                            if td10 and (earliest is None or td10 < earliest):
                                earliest = td10
                        if earliest:
                            try:
                                start_date = datetime.strptime(earliest, "%Y-%m-%d").date()
                            except (ValueError, TypeError):
                                start_date = None
                    if start_date is None:
                        return None
                    days = max((dt_util.now().date() - start_date).days, 1)
                    months = days / 30.4375
                    if months <= 0:
                        return None
                    return round(len(trades_list) / months, 2)
                elif self._key in (
                    "last_buy_date", "last_buy_symbol", "last_buy_value",
                    "last_sell_date", "last_sell_symbol", "last_sell_value",
                ):
                    target = 'BUY' if self._key.startswith('last_buy') else 'SELL'
                    if target == 'BUY':
                        relevant = [t for t in trades_list if _trade_type(t) in ('BUY', 'OPENING BALANCE')]
                    else:
                        relevant = [t for t in trades_list if _trade_type(t) == 'SELL']
                    if not relevant:
                        return None
                    try:
                        sorted_rel = sorted(
                            relevant,
                            key=lambda t: (
                                t.get('transaction_date')
                                or t.get('trade_date')
                                or t.get('date')
                                or t.get('traded_at', '')
                            ),
                            reverse=True,
                        )
                        last = sorted_rel[0]
                    except (TypeError, ValueError, IndexError):
                        return None
                    if self._key.endswith('_date'):
                        return (
                            last.get('transaction_date')
                            or last.get('trade_date')
                            or last.get('date')
                            or last.get('traded_at')
                        )
                    if self._key.endswith('_symbol'):
                        return _trade_symbol(last) or None
                    return round(_trade_value(last), 2)
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
            # Per-holding fundamentals (joined from user_instruments)
            elif self._key == "holding_fundamental":
                lookup = self._coordinator.data.get('instrument_lookup', {})
                holdings_list = self._coordinator.data.get('holdings', {}).get('holdings', [])
                holding = _find_holding_by_symbol(holdings_list, self._local_name)
                if holding is None:
                    return None
                instrument = analytics.lookup_instrument(lookup, holding) or {}
                field = "current_price_updated_at" if self._sub_key == "price_updated_at" else self._sub_key
                return instrument.get(field)
            # Per-holding dividend income (grouped from payouts)
            elif self._key == "holding_income":
                income_map = self._coordinator.data.get('holding_income', {})
                entry = income_map.get(self._local_name)
                if not isinstance(entry, dict):
                    return None
                field = "count" if self._sub_key == "dividend_count" else self._sub_key
                return entry.get(field)
            # Per-holding trade activity (grouped from trades)
            elif self._key == "holding_trade":
                trades_map = self._coordinator.data.get('holding_trades', {})
                entry = trades_map.get(self._local_name)
                if not isinstance(entry, dict):
                    return None
                trade_field_map = {
                    "last_trade_date": "last_date",
                    "trade_count": "count",
                    "brokerage_paid": "brokerage",
                    "vwap_buy_price": "vwap_buy_price",
                    "net_shares": "net_shares",
                }
                return entry.get(trade_field_map.get(self._sub_key, self._sub_key))
            # Portfolio sector / industry allocation
            elif self._sub_key in ("sector_allocation", "industry_allocation"):
                alloc = self._coordinator.data.get(self._sub_key, {})
                breakdown = alloc.get('breakdown', []) if isinstance(alloc, dict) else []
                prefix = "sector" if self._sub_key == "sector_allocation" else "industry"
                if self._key == f"{prefix}_count":
                    return len(breakdown)
                if self._key in ("top_3_sectors_percent", "top_5_sectors_percent"):
                    n = 3 if "3" in self._key else 5
                    return round(sum(float(b.get('percentage', 0) or 0) for b in breakdown[:n]), 2)
                # Ranked entries: <prefix>_<rank>_<name|percent|value>
                for rank in range(1, 6):
                    for attr, out in (("name", "group_name"), ("percent", "percentage"), ("value", "value")):
                        if self._key == f"{prefix}_{rank}_{attr}":
                            if len(breakdown) >= rank:
                                return breakdown[rank - 1].get(out)
                            return None
                return None
            # Account / subscription (my_user.json)
            elif self._sub_key == "my_user":
                my_user = self._coordinator.data.get('my_user', {})
                if not isinstance(my_user, dict):
                    return None
                user = my_user.get('user') if isinstance(my_user.get('user'), dict) else my_user
                if self._key == "subscription_status":
                    if user.get('is_expired'):
                        return "Expired"
                    if user.get('is_cancelled'):
                        return "Cancelled"
                    return "Active"
                return user.get(self._key)
            # Watchlist overview
            elif self._sub_key == "watchlist":
                watchlist_data = self._coordinator.data.get('watchlist', {})
                items = watchlist_data.get('watchlist', []) if isinstance(watchlist_data, dict) else []
                return _watchlist_metric(items, self._key)
            # Watchlist per-instrument price + day change (W1)
            elif self._sub_key == "watchlist_instrument":
                watchlist_data = self._coordinator.data.get('watchlist', {})
                items = watchlist_data.get('watchlist', []) if isinstance(watchlist_data, dict) else []
                item = next(
                    (
                        it for it in items
                        if isinstance(it, dict)
                        and ((it.get('instrument') or {}).get('code') or it.get('code')) == self._local_name
                    ),
                    None,
                )
                if not isinstance(item, dict):
                    return None
                price = item.get('price') or {}
                if self._key == "watchlist_instrument_price":
                    return price.get('value')
                if self._key == "watchlist_instrument_day_change_percent":
                    return price.get('diff_percent')
                return None
            # Latest instrument-news headline (W2), truncated to HA's 255-char cap
            elif self._sub_key == "instrument_news":
                news_container = self._coordinator.data.get('instrument_news', {})
                articles = news_container.get('instrument_news', []) if isinstance(news_container, dict) else []
                articles = [a for a in articles if isinstance(a, dict)]
                if not articles:
                    return None
                latest = max(articles, key=lambda a: str(a.get('published_at') or ''))
                title = latest.get('title')
                if title is None:
                    return None
                return str(title)[:255]
            # Portfolio value-trend 7d/30d change (W6)
            elif self._sub_key == "value_trend":
                trend = self._coordinator.data.get('value_trend', {})
                if not isinstance(trend, dict):
                    return None
                return trend.get(self._key)
            # Per-label allocation value / percent (W7)
            elif self._sub_key == "label_allocation":
                allocation = self._coordinator.data.get('label_allocation', [])
                if not isinstance(allocation, list):
                    return None
                entry = next(
                    (e for e in allocation if isinstance(e, dict) and e.get('label') == self._local_name),
                    None,
                )
                if not isinstance(entry, dict):
                    return None
                if self._key == "label_value":
                    return entry.get('value')
                if self._key == "label_percent":
                    return entry.get('percentage')
                return None
            # FX rate (foreign currency -> base currency)
            elif self._device_group == "fx":
                fx_data = self._coordinator.data.get('exchange_rates', {})
                rates = fx_data.get('exchange_rates', {}) if isinstance(fx_data, dict) else {}
                # local_name is the foreign code; pair is FOREIGN/BASE
                pair = f"{self._local_name}/{str(self._currency_code).upper()}"
                entry = rates.get(pair) if isinstance(rates, dict) else None
                if isinstance(entry, dict):
                    return entry.get('rate')
                return None
            # Market trading hours
            elif self._device_group == "market_hours":
                markets_data = self._coordinator.data.get('markets', {})
                markets = markets_data.get('markets', []) if isinstance(markets_data, dict) else []
                market = next(
                    (m for m in markets if isinstance(m, dict) and str(m.get('code')) == str(self._local_name)),
                    None,
                )
                if market is None:
                    return None
                is_open, next_open, next_close = _market_hours_status(market, dt_util.now())
                if self._key == "market_status":
                    if is_open is None:
                        return None
                    return "Open" if is_open else "Closed"
                if self._key == "market_next_open":
                    return next_open
                if self._key == "market_next_close":
                    return next_close
                return None
            # Capital gains tax reports (AU portfolios)
            elif self._sub_key in ("capital_gains", "unrealised_cgt"):
                tax_data = self._coordinator.data.get(self._sub_key, {})
                if not isinstance(tax_data, dict) or 'error' in tax_data:
                    return None
                val = tax_data.get(self._key)
                return val if isinstance(val, (int, float, str)) else None
            elif self._sub_key == "benchmark":
                bench = self._coordinator.data.get('benchmark', {})
                if not isinstance(bench, dict):
                    return None
                instrument = bench.get('instrument') or {}
                if self._key == "benchmark_name":
                    return instrument.get('name')
                if self._key == "benchmark_code":
                    code = instrument.get('code')
                    market = instrument.get('market_code')
                    if code and market:
                        return f"{code}.{market}"
                    return code
                if self._key == "benchmark_total_gain_percent":
                    return bench.get('total_gain_percent')
                if self._key == "benchmark_capital_gain_percent":
                    # The API names this one field 'percentage', not 'percent'
                    return bench.get('capital_gain_percentage')
                if self._key == "benchmark_payout_gain_percent":
                    return bench.get('payout_gain_percent')
                if self._key == "benchmark_currency_gain_percent":
                    return bench.get('currency_gain_percent')
                if self._key == "benchmark_excess_return_percent":
                    # Both percentages are since-inception (the benchmark call
                    # uses the portfolio inception date as start_date), so a
                    # simple difference is meaningful.
                    report_data = self._coordinator.data.get('report', {})
                    try:
                        portfolio_pct = float(report_data.get('total_gain_percent'))
                        benchmark_pct = float(bench.get('total_gain_percent'))
                    except (TypeError, ValueError):
                        return None
                    return round(portfolio_pct - benchmark_pct, 2)
                return None
            # Portfolio analytics (concentration / quality / composition)
            elif self._sub_key == "portfolio_analytics":
                analytics_data = self._coordinator.data.get('portfolio_analytics', {})
                if not isinstance(analytics_data, dict):
                    return None
                return analytics_data.get(self._key)
            # All-time totals incl. sold positions (raw v3 /totals payload)
            elif self._sub_key == "totals":
                totals_data = self._coordinator.data.get('totals', {})
                if not isinstance(totals_data, dict):
                    return None
                # Tolerate both {"portfolio": {...}} and a flat payload.
                portfolio = totals_data.get('portfolio')
                source = portfolio if isinstance(portfolio, dict) else totals_data
                if self._key == "percentage_annualised":
                    # The API example spells this correctly; the doc field
                    # table misspells it as "percentage_annulaised" — accept both.
                    flag = source.get('percentage_annualised')
                    if flag is None:
                        flag = source.get('percentage_annulaised')
                    if flag is None:
                        return None
                    return "Yes" if flag else "No"
                val = source.get(self._key)
                return val if isinstance(val, (int, float)) else None
            # Integration diagnostics
            elif self._sub_key == "_integration":
                if self._key == "last_update_timestamp":
                    # Stamped by TimestampDataUpdateCoordinator after every
                    # successful poll; None until the first one lands.  The old
                    # getattr() chain silently returned None forever, because
                    # neither attribute exists on a plain DataUpdateCoordinator.
                    return self._coordinator.last_update_success_time
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
                    now = monotonic()
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
    def extra_state_attributes(self):
        """Rich attributes on a handful of anchor sensors only (Feature 1).

        Dispatches on (self._sub_key, self._key) exactly like native_value.
        Only the anchor sensors enumerated here expose attributes; every other
        sensor stays attribute-free to protect the recorder.  Lists are capped
        at 25 and the whole build is wrapped defensively so an attribute error
        can never break the entity.
        """
        try:
            data = self._coordinator.data or {}

            # Portfolio value — holdings ranking, movers, cash and day change.
            if self._sub_key == "report" and self._key == "value":
                report_data = data.get("report", {}) if isinstance(data.get("report"), dict) else {}
                holdings_data = data.get("holdings", {})
                holdings_list = holdings_data.get("holdings", []) if isinstance(holdings_data, dict) else []
                portfolio_value = float(report_data.get("value", 0) or 0)
                ranked = sorted(holdings_list, key=_get_holding_value, reverse=True)[:25]
                holdings_attr = []
                for h in ranked:
                    value = _get_holding_value(h)
                    holdings_attr.append({
                        "symbol": _get_holding_symbol(h),
                        "value": round(value, 2),
                        "percent": round(value / portfolio_value * 100, 2) if portfolio_value else None,
                        "gain": round(_get_holding_gain(h), 2),
                        "gain_percent": _get_holding_gain_percent(h),
                    })
                by_gain = sorted(holdings_list, key=_get_holding_gain, reverse=True)
                top_gainers = [
                    {"symbol": _get_holding_symbol(h), "gain": round(_get_holding_gain(h), 2),
                     "gain_percent": _get_holding_gain_percent(h)}
                    for h in by_gain[:5]
                ]
                top_losers = [
                    {"symbol": _get_holding_symbol(h), "gain": round(_get_holding_gain(h), 2),
                     "gain_percent": _get_holding_gain_percent(h)}
                    for h in reversed(by_gain[-5:])
                ]
                cash_summary = _get_cash_accounts_summary(report_data)
                total_cash = cash_summary.get("total_cash_value", 0) or 0
                capital_gain = report_data.get("capital_gain")
                cost_base = None
                if capital_gain is not None:
                    try:
                        cost_base = round(portfolio_value - float(capital_gain), 2)
                    except (ValueError, TypeError):
                        cost_base = None
                one_day = data.get("one-day", {}) if isinstance(data.get("one-day"), dict) else {}
                return {
                    "holdings": holdings_attr,
                    "top_gainers": top_gainers,
                    "top_losers": top_losers,
                    "cash_accounts": cash_summary.get("cash_accounts_count"),
                    "equity_value": round(portfolio_value - float(total_cash), 2),
                    "total_cash_value": cash_summary.get("total_cash_value"),
                    "cost_base": cost_base,
                    "day_change_amount": one_day.get("total_gain"),
                    "day_change_percent": one_day.get("total_gain_percent"),
                    "as_of": report_data.get("end_date") or report_data.get("as_at"),
                }

            # Number of Holdings — the ranked holdings list.
            if self._sub_key == "holdings" and self._key == "holding_count":
                holdings_data = data.get("holdings", {})
                holdings_list = holdings_data.get("holdings", []) if isinstance(holdings_data, dict) else []
                portfolio_value = float(holdings_data.get("value", 0) or 0)
                ranked = sorted(holdings_list, key=_get_holding_value, reverse=True)[:25]
                return {
                    "holdings": [
                        {
                            "symbol": _get_holding_symbol(h),
                            "value": round(_get_holding_value(h), 2),
                            "percent": round(_get_holding_value(h) / portfolio_value * 100, 2) if portfolio_value else None,
                            "gain": round(_get_holding_gain(h), 2),
                        }
                        for h in ranked
                    ]
                }

            # Next Dividend Amount — the next payout's detail plus upcoming list.
            if self._sub_key == "income_report" and self._key == "next_dividend_amount":
                income_data = data.get("income_report", {}) if isinstance(data.get("income_report"), dict) else {}
                payouts = (income_data.get("payouts", []) or []) + (income_data.get("upcoming_payouts", []) or [])
                today_iso = dt_util.now().date().isoformat()
                upcoming = []
                for p in payouts:
                    if not isinstance(p, dict):
                        continue
                    ex = p.get("goes_ex_on") or p.get("ex_date") or p.get("paid_on")
                    if ex and str(ex)[:10] >= today_iso:
                        upcoming.append((str(ex)[:10], p))
                if not upcoming:
                    return None
                upcoming.sort(key=lambda x: x[0])
                _, nxt = upcoming[0]
                return {
                    "ex_date": nxt.get("goes_ex_on") or nxt.get("ex_date"),
                    "pay_date": nxt.get("pay_date") or nxt.get("paid_on"),
                    "franking_credits": nxt.get("franking_credits"),
                    "gross_amount": nxt.get("gross_amount") or nxt.get("amount"),
                    "state": nxt.get("state") or nxt.get("status"),
                    "company": (
                        nxt.get("company_name")
                        or nxt.get("symbol")
                        or nxt.get("instrument_code")
                    ),
                    "all_upcoming": [
                        {
                            "symbol": p.get("symbol") or p.get("instrument_code"),
                            "ex_date": ex,
                            "amount": p.get("amount") or p.get("gross_amount"),
                        }
                        for ex, p in upcoming[:25]
                    ],
                }

            # Diversity top-1 — the full market breakdown.
            if self._sub_key == "diversity" and self._key == "market_1_name":
                breakdown = (data.get("diversity", {}) or {}).get("breakdown", [])
                ordered = sorted(
                    breakdown,
                    key=lambda x: float(x.get("percentage", 0) or 0),
                    reverse=True,
                )[:25]
                return {
                    "breakdown": [
                        {"name": b.get("group_name"), "percent": b.get("percentage"), "value": b.get("value")}
                        for b in ordered
                    ]
                }

            # Sector / industry top-1 — the full allocation breakdown.
            if self._sub_key in ("sector_allocation", "industry_allocation") and self._key in (
                "sector_1_name",
                "industry_1_name",
            ):
                breakdown = (data.get(self._sub_key, {}) or {}).get("breakdown", [])
                return {
                    "breakdown": [
                        {"name": b.get("group_name"), "percent": b.get("percentage"), "value": b.get("value")}
                        for b in breakdown[:25]
                    ]
                }

            # Last Trade — the most recent trade's detail.
            if self._sub_key == "trades" and self._key == "last_trade_value":
                trades_data = data.get("trades", {})
                trades_list = trades_data.get("trades", []) if isinstance(trades_data, dict) else []
                if not trades_list:
                    return None
                last = sorted(
                    trades_list,
                    key=lambda t: (
                        t.get("transaction_date")
                        or t.get("trade_date")
                        or t.get("date")
                        or t.get("traded_at", "")
                    ),
                    reverse=True,
                )[0]
                return {
                    "quantity": last.get("quantity"),
                    "price": last.get("price"),
                    "brokerage": last.get("brokerage") or last.get("fee") or last.get("commission"),
                    "market": last.get("market") or (last.get("instrument", {}) or {}).get("market_code"),
                    "value": last.get("value") or last.get("cost_base") or last.get("amount"),
                    "type": last.get("transaction_type") or last.get("trade_type") or last.get("type"),
                }

            # Latest News — the capped list of recent articles (W2).
            if self._sub_key == "instrument_news" and self._key == "latest_news":
                news_container = data.get("instrument_news", {})
                articles = news_container.get("instrument_news", []) if isinstance(news_container, dict) else []
                articles = [a for a in articles if isinstance(a, dict)]
                if not articles:
                    return None
                # Cap at 10 (matching the news event cap) so the serialized
                # attribute payload stays well under HA's 16 KiB recorder limit;
                # 25 long-title/long-URL articles can overflow it and the
                # recorder then stores {} instead.
                ordered = sorted(articles, key=lambda a: str(a.get("published_at") or ""), reverse=True)[:10]
                latest = ordered[0]
                return {
                    "source": latest.get("source"),
                    "published_at": latest.get("published_at"),
                    "link": latest.get("link"),
                    "author": latest.get("author"),
                    "instrument_id": latest.get("instrument_id"),
                    "articles": [
                        {
                            "title": (str(a.get("title"))[:255] if a.get("title") is not None else None),
                            "source": a.get("source"),
                            "published_at": a.get("published_at"),
                            "link": a.get("link"),
                            "author": a.get("author"),
                            "instrument_id": a.get("instrument_id"),
                        }
                        for a in ordered
                    ],
                }

            # Value Change 30d — the value-trend sparkline series (W6).
            if self._sub_key == "value_trend" and self._key == "change_30d_percent":
                trend = data.get("value_trend", {})
                if not isinstance(trend, dict):
                    return None
                series = trend.get("series")
                if not isinstance(series, list):
                    return None
                return {"series": series[:31]}

            return None
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("Error building attributes for '%s': %s: %s", self._key, type(e).__name__, e)
            return None

    # No ``name`` property: the display name now comes from HA's translation
    # machinery (has_entity_name + the description's translation_key, plus any
    # per-entity translation_placeholders set in __init__).  ``self._name`` is
    # still used above solely to derive the hand-assigned entity_id slug, so
    # entity_ids are unchanged.

    @property
    def state_class(self):
        return self._state_class

    @property
    def icon(self):
        """An ``icon=`` on the description wins; otherwise icons.json.

        Without the fallback this override shadowed
        ``SharesightBaseEntity.icon`` and pinned every sensor to ``None``
        (no description sets ``icon=``), leaving ``attributes.icon`` unset.
        """
        return self._icon or super().icon

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

        Diagnostic sensors under sub_key '_integration' (last successful
        update, update interval, optional endpoints on cooldown) are *always*
        available — their whole point is to surface state about the
        integration itself, including during failures.
        """
        if self._sub_key == "_integration":
            return True
        if self._coordinator.data:
            return True
        # Fall back to HA's default: check last_update_success.  This only
        # matters on the very first poll cycle before any data exists.
        return self._coordinator.last_update_success

