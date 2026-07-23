"""Pure aggregation helpers for the Sharesight integration.

These functions take already-fetched Sharesight payloads and derive the
per-holding and portfolio-level analytics that several sensors consume.
They perform no I/O and no Home Assistant calls, so they are cheap to run
once per poll in the coordinator and trivially unit-testable.

The design goal is to mine data the coordinator ALREADY fetches (the
portfolio payouts list, the trades list, and the user_instruments feed)
without adding any API requests.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def _f(value: Any) -> float | None:
    """Best-effort float coercion (None on failure)."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def holding_symbol(holding: dict[str, Any]) -> str | None:
    """Instrument symbol/code for a holding, tolerating shape differences."""
    if not isinstance(holding, dict):
        return None
    instrument = holding.get("instrument") or {}
    return (
        holding.get("instrument_code")
        or instrument.get("code")
        or holding.get("code")
        or holding.get("symbol")
    )


def holding_market(holding: dict[str, Any]) -> str | None:
    """Market code for a holding, tolerating shape differences."""
    if not isinstance(holding, dict):
        return None
    instrument = holding.get("instrument") or {}
    return (
        holding.get("market")
        or instrument.get("market_code")
        or holding.get("market_code")
    )


def _holding_cost_base(holding: dict[str, Any]) -> float | None:
    """Cost base for a holding: use the field if present, else value - gain."""
    cost_base = _f(holding.get("cost_base"))
    if cost_base is not None:
        return cost_base
    value = _f(holding.get("value"))
    gain = _f(holding.get("capital_gain"))
    if value is not None and gain is not None:
        return value - gain
    return None


def build_instrument_lookup(user_instruments_data: Any) -> dict[str, dict[str, Any]]:
    """Index the user_instruments feed by "CODE.MARKET", "CODE" and "id:<n>".

    Returns a flat dict of string keys -> a compact instrument-detail dict so
    per-holding sensors can join without re-scanning the list.  String keys
    keep coordinator.data JSON-serialisable (diagnostics dumps it).
    """
    lookup: dict[str, dict[str, Any]] = {}
    if not isinstance(user_instruments_data, dict):
        return lookup
    for inst in user_instruments_data.get("instruments", []) or []:
        if not isinstance(inst, dict):
            continue
        subset = {
            "pe_ratio": inst.get("pe_ratio"),
            "eps": inst.get("eps"),
            "nta": inst.get("nta"),
            "current_price": inst.get("current_price"),
            "current_price_updated_at": inst.get("current_price_updated_at"),
            "sector": inst.get("sector_classification_name"),
            "industry": inst.get("industry_classification_name"),
            "instrument_type": inst.get("friendly_instrument_description"),
            "registry_name": inst.get("registry_name"),
            "country_code": inst.get("country_code"),
            "currency_code": inst.get("currency_code"),
            "name": inst.get("name"),
        }
        code = inst.get("code")
        market = inst.get("market_code")
        if code and market:
            lookup[f"{code}.{market}"] = subset
        if code and code not in lookup:
            lookup[code] = subset
        inst_id = inst.get("id")
        if inst_id is not None:
            lookup.setdefault(f"id:{inst_id}", subset)
    return lookup


def lookup_instrument(
    lookup: dict[str, dict[str, Any]], holding: dict[str, Any]
) -> dict[str, Any] | None:
    """Resolve a holding to its instrument-detail subset via code/market/id."""
    if not lookup:
        return None
    symbol = holding_symbol(holding)
    market = holding_market(holding)
    if symbol and market and f"{symbol}.{market}" in lookup:
        return lookup[f"{symbol}.{market}"]
    if symbol and symbol in lookup:
        return lookup[symbol]
    instrument = holding.get("instrument") or {}
    inst_id = instrument.get("id") or holding.get("instrument_id")
    if inst_id is not None:
        return lookup.get(f"id:{inst_id}")
    return None


def _holding_id_to_symbol(holdings: list[dict[str, Any]]) -> dict[str, str]:
    """Map str(holding_id) -> symbol for joining payouts/trades to holdings."""
    mapping: dict[str, str] = {}
    for holding in holdings or []:
        if not isinstance(holding, dict):
            continue
        symbol = holding_symbol(holding)
        holding_id = holding.get("id")
        if holding_id is not None and symbol:
            mapping[str(holding_id)] = symbol
    return mapping


def build_holding_income(
    payouts: list[dict[str, Any]],
    holdings: list[dict[str, Any]],
    today: date,
) -> dict[str, dict[str, Any]]:
    """Per-holding dividend income keyed by symbol.

    Derives, from the already-fetched portfolio payouts list, each holding's
    trailing-12-month income, franking credits (AU), last dividend
    amount/date, count and yield-on-cost (TTM income / cost base).

    Currency caveat: payout amounts are in the payout currency while cost
    base is in portfolio currency, so yield-on-cost is approximate for
    foreign holdings — good enough for a glanceable sensor.
    """
    id_to_symbol = _holding_id_to_symbol(holdings)
    cost_by_symbol: dict[str, float | None] = {}
    for holding in holdings or []:
        symbol = holding_symbol(holding)
        if symbol:
            cost_by_symbol[symbol] = _holding_cost_base(holding)

    cutoff = (today - timedelta(days=365)).isoformat()
    result: dict[str, dict[str, Any]] = {}
    for payout in payouts or []:
        if not isinstance(payout, dict):
            continue
        holding_id = payout.get("holding_id")
        symbol = id_to_symbol.get(str(holding_id)) if holding_id is not None else None
        if not symbol:
            symbol = payout.get("symbol")
        if not symbol:
            continue
        entry = result.setdefault(
            symbol,
            {
                "ttm_income": 0.0,
                "franking_ttm": 0.0,
                "count": 0,
                "last_dividend_date": None,
                "last_dividend_amount": None,
            },
        )
        pay_date = payout.get("paid_on") or payout.get("date") or payout.get("ex_date")
        amount = _f(payout.get("amount")) or 0.0
        entry["count"] += 1
        if pay_date and (
            entry["last_dividend_date"] is None
            or str(pay_date)[:10] > entry["last_dividend_date"]
        ):
            entry["last_dividend_date"] = str(pay_date)[:10]
            entry["last_dividend_amount"] = round(amount, 2)
        if pay_date and str(pay_date)[:10] >= cutoff:
            entry["ttm_income"] += amount
            franking = _f(payout.get("franking_credits"))
            if franking is not None:
                entry["franking_ttm"] += franking

    for symbol, entry in result.items():
        entry["ttm_income"] = round(entry["ttm_income"], 2)
        entry["franking_ttm"] = round(entry["franking_ttm"], 2)
        cost_base = cost_by_symbol.get(symbol)
        if cost_base and cost_base != 0:
            entry["yield_on_cost"] = round(entry["ttm_income"] / cost_base * 100, 2)
        else:
            entry["yield_on_cost"] = None
    return result


def build_holding_trades(
    trades: list[dict[str, Any]],
    holdings: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Per-holding trade activity keyed by symbol.

    From the already-fetched trades list: trade count, last trade date,
    brokerage paid, net shares traded and a volume-weighted average BUY
    price (sum(qty*price)/sum(qty) over BUY + OPENING BALANCE rows).  The
    VWAP is an approximation in instrument currency and does not adjust for
    splits/DRP the way Sharesight's official average purchase price does.
    """
    id_to_symbol = _holding_id_to_symbol(holdings)
    result: dict[str, dict[str, Any]] = {}
    for trade in trades or []:
        if not isinstance(trade, dict):
            continue
        holding_id = trade.get("holding_id")
        symbol = id_to_symbol.get(str(holding_id)) if holding_id is not None else None
        if not symbol:
            symbol = trade.get("symbol")
        if not symbol:
            continue
        entry = result.setdefault(
            symbol,
            {
                "count": 0,
                "last_date": None,
                "brokerage": 0.0,
                "net_shares": 0.0,
                "_buy_qty": 0.0,
                "_buy_cost": 0.0,
            },
        )
        entry["count"] += 1
        trade_date = trade.get("transaction_date") or trade.get("date")
        if trade_date and (
            entry["last_date"] is None or str(trade_date)[:10] > entry["last_date"]
        ):
            entry["last_date"] = str(trade_date)[:10]
        brokerage = _f(trade.get("brokerage"))
        if brokerage is not None:
            entry["brokerage"] += brokerage
        qty = _f(trade.get("quantity")) or 0.0
        price = _f(trade.get("price")) or 0.0
        trade_type = str(trade.get("transaction_type") or "").upper()
        if trade_type in ("BUY", "OPENING BALANCE"):
            entry["_buy_qty"] += qty
            entry["_buy_cost"] += qty * price
            entry["net_shares"] += qty
        elif trade_type == "SELL":
            entry["net_shares"] -= qty

    for entry in result.values():
        entry["brokerage"] = round(entry["brokerage"], 2)
        entry["net_shares"] = round(entry["net_shares"], 4)
        buy_qty = entry.pop("_buy_qty", 0.0)
        buy_cost = entry.pop("_buy_cost", 0.0)
        entry["vwap_buy_price"] = round(buy_cost / buy_qty, 4) if buy_qty > 0 else None
    return result


def build_sector_allocation(
    holdings: list[dict[str, Any]],
    instrument_lookup: dict[str, dict[str, Any]],
    axis: str = "sector",
) -> dict[str, Any]:
    """Value-weighted sector or industry allocation.

    Joins each holding's value to its FactSet sector/industry classification
    (from the free user_instruments feed) and returns a diversity-style
    ``breakdown`` list, so the existing top-N helper can consume it.  ``axis``
    is "sector" or "industry".  Unclassified holdings land in "Unknown".
    """
    buckets: dict[str, float] = {}
    total = 0.0
    for holding in holdings or []:
        if not isinstance(holding, dict):
            continue
        instrument = lookup_instrument(instrument_lookup, holding) or {}
        name = instrument.get(axis) or "Unknown"
        value = _f(holding.get("value")) or 0.0
        buckets[name] = buckets.get(name, 0.0) + value
        total += value

    breakdown: list[dict[str, Any]] = []
    for name, value in buckets.items():
        percentage = round(value / total * 100, 2) if total else 0
        breakdown.append(
            {"group_name": name, "value": round(value, 2), "percentage": percentage}
        )
    breakdown.sort(key=lambda item: item["value"], reverse=True)
    return {"breakdown": breakdown, "total": round(total, 2)}


def portfolio_currency_codes(data: dict[str, Any]) -> list[str]:
    """Distinct currency codes seen across holdings + instruments (upper-case).

    Used to build the exchange_rates request.  Reads whatever data is already
    present (typically the previous poll's), because holdings for the current
    poll are not built yet when the request list is assembled.
    """
    codes: set[str] = set()
    if not isinstance(data, dict):
        return []

    portfolios = data.get("portfolios")
    if isinstance(portfolios, list) and portfolios and isinstance(portfolios[0], dict):
        base = portfolios[0].get("currency_code")
        if base:
            codes.add(str(base).upper())

    detail = data.get("portfolio_detail")
    if isinstance(detail, dict) and detail.get("currency_code"):
        codes.add(str(detail["currency_code"]).upper())

    instruments = data.get("user_instruments")
    if isinstance(instruments, dict):
        for inst in instruments.get("instruments", []) or []:
            if isinstance(inst, dict) and inst.get("currency_code"):
                codes.add(str(inst["currency_code"]).upper())

    holdings = data.get("holdings")
    if isinstance(holdings, dict):
        for holding in holdings.get("holdings", []) or []:
            if not isinstance(holding, dict):
                continue
            currency = holding.get("currency_code") or (
                holding.get("instrument") or {}
            ).get("currency_code")
            if currency:
                codes.add(str(currency).upper())

    return sorted(codes)


def _parse_date(value: Any) -> date | None:
    """Parse a leading ``YYYY-MM-DD`` out of a value into a date (None on fail)."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _holding_yield(holding: dict[str, Any], instrument: dict[str, Any]) -> float | None:
    """Best-effort dividend yield (%) for a holding.

    Sharesight does not carry a yield on every payload, so try the fields
    that sometimes hold it (on the holding first, then the instrument) and
    give up with None when none are present — the caller weights only over
    holdings that return a figure.
    """
    for source in (holding, instrument):
        if not isinstance(source, dict):
            continue
        for field in ("yield", "dividend_yield", "gross_yield", "current_yield"):
            candidate = _f(source.get(field))
            if candidate is not None:
                return candidate
    return None


def _trailing_yield(
    holding: dict[str, Any],
    value: float,
    holding_income: dict[str, Any] | None,
) -> float | None:
    """Trailing-12-month dividend yield (%) for a holding, from its payouts.

    Sharesight carries no yield field on either the holding or the instrument
    payload, so ``_holding_yield`` returns None for every holding and the
    value-weighted portfolio yield would be unknown forever.
    ``build_holding_income`` has already derived each holding's TTM income from
    the payouts feed, so divide that by the holding's current market value.

    Approximate for foreign holdings — payout amounts are in the payout
    currency while ``value`` is in portfolio currency — the same caveat
    yield-on-cost already carries.
    """
    if not isinstance(holding_income, dict) or value <= 0:
        return None
    symbol = holding_symbol(holding)
    if not symbol:
        return None
    entry = holding_income.get(str(symbol))
    if not isinstance(entry, dict):
        return None
    ttm_income = _f(entry.get("ttm_income"))
    if ttm_income is None or ttm_income < 0:
        return None
    return ttm_income / value * 100


def _price_is_stale(updated_at: Any, today: date, max_age_days: int = 3) -> bool:
    """Whether an instrument price timestamp is older than ``max_age_days``.

    A missing/unparseable timestamp is treated as NOT stale, so a shape
    change can't inflate the stale count.
    """
    updated_date = _parse_date(updated_at)
    if updated_date is None:
        return False
    return (today - updated_date).days > max_age_days


def build_portfolio_analytics(
    holdings_list: list[dict[str, Any]],
    instrument_lookup: dict[str, dict[str, Any]],
    report: dict[str, Any],
    today: date,
    holding_income: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Portfolio-level concentration, quality and composition metrics.

    Pure join of the holdings list against the already-fetched
    user_instruments feed (via ``instrument_lookup``) plus the performance
    ``report`` for cash.  Every field degrades to ``None``/``0`` when its
    inputs are missing rather than raising, so it is safe to call every poll.

    - ``hhi``: Herfindahl-Hirschman concentration over equity weights
      (→0 perfectly diversified, →1 a single holding).
    - ``effective_holdings``: inverse-Simpson effective holding count (1/HHI).
    - ``weighted_yield`` / ``weighted_pe``: value-weighted over ONLY the
      holdings that expose a valid figure; ``pe_coverage_percent`` reports how
      much of the equity value backs the P/E number.  No Sharesight payload
      carries a yield field, so the yield falls back to each holding's
      trailing-12-month income (``holding_income``) over its market value.
    - ``fx_exposure_percent``: share of equity value NOT in the largest
      (assumed base) currency bucket.
    - ``cash_drag_percent``: cash / (equity + cash).
    - ``stale_price_count``: holdings whose instrument price is >3 days old.
    """
    result: dict[str, Any] = {
        "hhi": None,
        "effective_holdings": None,
        "weighted_yield": None,
        "weighted_pe": None,
        "pe_coverage_percent": None,
        "fx_exposure_percent": None,
        "cash_drag_percent": None,
        "stale_price_count": 0,
    }
    if not isinstance(holdings_list, list):
        return result
    if not isinstance(instrument_lookup, dict):
        instrument_lookup = {}
    if not isinstance(report, dict):
        report = {}

    values: list[float] = []
    equity_value = 0.0
    currency_buckets: dict[str, float] = {}
    pe_weight = 0.0
    pe_weighted_sum = 0.0
    yield_weight = 0.0
    yield_weighted_sum = 0.0
    stale_count = 0

    for holding in holdings_list:
        if not isinstance(holding, dict):
            continue
        instrument = lookup_instrument(instrument_lookup, holding) or {}
        value = _f(holding.get("value"))
        if value is not None and value > 0:
            values.append(value)
            equity_value += value

            currency = (
                holding.get("currency_code")
                or instrument.get("currency_code")
                or "UNKNOWN"
            )
            currency = str(currency).upper()
            currency_buckets[currency] = currency_buckets.get(currency, 0.0) + value

            pe = _f(instrument.get("pe_ratio"))
            if pe is not None and pe > 0:
                pe_weight += value
                pe_weighted_sum += value * pe

            holding_yield = _holding_yield(holding, instrument)
            if holding_yield is None:
                holding_yield = _trailing_yield(holding, value, holding_income)
            if holding_yield is not None and holding_yield >= 0:
                yield_weight += value
                yield_weighted_sum += value * holding_yield

        if _price_is_stale(instrument.get("current_price_updated_at"), today):
            stale_count += 1

    result["stale_price_count"] = stale_count

    if equity_value > 0 and values:
        hhi = sum((value / equity_value) ** 2 for value in values)
        result["hhi"] = round(hhi, 4)
        if hhi > 0:
            result["effective_holdings"] = round(1.0 / hhi, 2)
        base_value = max(currency_buckets.values()) if currency_buckets else 0.0
        result["fx_exposure_percent"] = round(
            (equity_value - base_value) / equity_value * 100, 2
        )

    if pe_weight > 0:
        result["weighted_pe"] = round(pe_weighted_sum / pe_weight, 2)
        if equity_value > 0:
            result["pe_coverage_percent"] = round(pe_weight / equity_value * 100, 2)
    if yield_weight > 0:
        result["weighted_yield"] = round(yield_weighted_sum / yield_weight, 2)

    cash_value = 0.0
    for account in report.get("cash_accounts", []) or []:
        if not isinstance(account, dict):
            continue
        amount = _f(account.get("value"))
        if amount is None:
            amount = _f(account.get("balance"))
        if amount is not None:
            cash_value += amount
    total_value = equity_value + cash_value
    if total_value > 0:
        result["cash_drag_percent"] = round(cash_value / total_value * 100, 2)

    return result


def build_income_forecast(
    upcoming_payouts: list[dict[str, Any]],
    holding_income: dict[str, dict[str, Any]],
    portfolio_value: Any,
    today: date,
) -> dict[str, Any]:
    """Forward dividend-income projection.

    Confirmed leg: announced-but-unpaid payouts (``upcoming_payouts``) grouped
    by pay month.  Projected leg: each dividend-paying holding's
    trailing-12-month income run-rate (``holding_income``) for payers that do
    NOT yet have an announcement, so an announced payer is counted from its
    confirmed figure and everyone else from history (no double counting).

    Returns zero/None fields rather than raising when inputs are missing.
    """
    result: dict[str, Any] = {
        "forward_annual_income": 0.0,
        "forward_yield_percent": None,
        "income_30d": 0.0,
        "income_90d": 0.0,
        "days_to_next": None,
        "announced_income": 0.0,
        "income_by_month": {},
    }
    if not isinstance(upcoming_payouts, list):
        upcoming_payouts = []
    if not isinstance(holding_income, dict):
        holding_income = {}

    income_by_month: dict[str, float] = {}
    announced_total = 0.0
    income_30d = 0.0
    income_90d = 0.0
    announced_symbols: set[str] = set()
    next_date: date | None = None
    horizon_30 = today + timedelta(days=30)
    horizon_90 = today + timedelta(days=90)

    for payout in upcoming_payouts:
        if not isinstance(payout, dict):
            continue
        amount = _f(payout.get("amount"))
        if amount is None:
            amount = _f(payout.get("gross_amount"))
        amount = amount or 0.0
        announced_total += amount

        symbol = payout.get("symbol")
        if symbol:
            announced_symbols.add(symbol)

        pay_date = _parse_date(
            payout.get("pay_date") or payout.get("paid_on") or payout.get("date")
        )
        upcoming_date = pay_date or _parse_date(payout.get("ex_date"))

        if pay_date is not None:
            month_key = pay_date.strftime("%Y-%m")
            income_by_month[month_key] = round(
                income_by_month.get(month_key, 0.0) + amount, 2
            )
            if today <= pay_date <= horizon_30:
                income_30d += amount
            if today <= pay_date <= horizon_90:
                income_90d += amount

        if upcoming_date is not None and upcoming_date >= today and (
            next_date is None or upcoming_date < next_date
        ):
            next_date = upcoming_date

    # Projected leg: TTM run-rate for payers without an announcement.
    projected_total = 0.0
    for symbol, entry in holding_income.items():
        if symbol in announced_symbols or not isinstance(entry, dict):
            continue
        ttm = _f(entry.get("ttm_income"))
        if ttm and ttm > 0:
            projected_total += ttm

    forward_annual_income = announced_total + projected_total
    result["announced_income"] = round(announced_total, 2)
    result["income_30d"] = round(income_30d, 2)
    result["income_90d"] = round(income_90d, 2)
    result["forward_annual_income"] = round(forward_annual_income, 2)
    result["income_by_month"] = income_by_month
    if next_date is not None:
        result["days_to_next"] = (next_date - today).days

    pv = _f(portfolio_value)
    if pv and pv > 0:
        result["forward_yield_percent"] = round(forward_annual_income / pv * 100, 2)
    return result


def _value_series_points(payload: Any) -> list[tuple[str, float]]:
    """Normalise a portfolio value-series payload into sorted (date, value) points.

    Sharesight's mobile value endpoints are documented loosely (the apiDoc
    example is boilerplate), so tolerate every plausible shape: a
    ``portfolio_value_data`` wrapper, a ``chart.data`` list, a
    ``values``/``data`` list, a nested ``values.values`` wrapper, or a
    ``{date: value}`` mapping.  Anything that cannot be parsed into a dated
    numeric point is skipped rather than raising, and duplicate dates keep the
    last value seen.

    Note the V3 ``/value`` endpoint is NOT a source here: its only parameters
    are ``consolidated``/``currency_code`` and it answers with a single
    point-in-time balance, which yields no points at all.
    """
    raw: Any = payload
    # Peel known container keys until we reach the actual list/mapping of
    # points.  Bounded so a self-referential shape can never loop forever.
    for _ in range(4):
        if not isinstance(raw, dict):
            break
        chart = raw.get("chart")
        if isinstance(chart, dict) and isinstance(chart.get("data"), list):
            raw = chart["data"]
            break
        if isinstance(raw.get("data"), list):
            raw = raw["data"]
            break
        # portfolio_value_data.json wraps the daily series one level deeper
        # ({portfolio_value_data: {chart: {data: [...]}}}); peel it and let the
        # next pass find the chart/data list.
        value_data = raw.get("portfolio_value_data")
        if isinstance(value_data, dict):
            raw = value_data
            continue
        if "values" in raw:
            raw = raw["values"]
            continue
        break

    points: list[tuple[str, float]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            parsed = _parse_date(
                item.get("date") or item.get("timestamp") or item.get("as_at")
            )
            value = _f(item.get("value"))
            if value is None:
                value = _f((item.get("in_portfolio_currency") or {}).get("value"))
            if parsed is not None and value is not None:
                points.append((parsed.isoformat(), value))
    elif isinstance(raw, dict):
        for key, val in raw.items():
            parsed = _parse_date(key)
            if parsed is None:
                continue
            value = _f(val if not isinstance(val, dict) else val.get("value"))
            if value is not None:
                points.append((parsed.isoformat(), value))

    dedup: dict[str, float] = {}
    for date_str, value in points:
        dedup[date_str] = value
    return sorted(dedup.items(), key=lambda point: point[0])


def build_value_trend(series: Any) -> dict[str, Any]:
    """30-day portfolio value trend from the optional V3 ``/value`` payload.

    Returns ``{change_7d_percent, change_30d_percent, series}`` where ``series``
    is a chronologically sorted list of ``{date, value}`` capped at the most
    recent 31 points (for an ApexCharts sparkline).  The percentage changes
    compare the latest value against the last point on-or-before 7 / 30 days
    earlier, falling back to the earliest available point when the series does
    not quite reach that far back (weekend/holiday gaps), and are ``None`` when
    the series is too short or the baseline is zero.  Accepts the raw endpoint
    payload and degrades to empty/``None`` fields rather than raising.
    """
    result: dict[str, Any] = {
        "change_7d_percent": None,
        "change_30d_percent": None,
        "series": [],
    }
    points = _value_series_points(series)
    if not points:
        return result

    points = points[-31:]
    result["series"] = [
        {"date": date_str, "value": round(value, 2)} for date_str, value in points
    ]

    latest_date_str, latest_value = points[-1]
    latest_date = _parse_date(latest_date_str)
    earliest_date = _parse_date(points[0][0])

    def _change(lookback_days: int) -> float | None:
        if latest_date is None:
            return None
        target = latest_date - timedelta(days=lookback_days)
        base: float | None = None
        for date_str, value in points:
            parsed = _parse_date(date_str)
            if parsed is not None and parsed <= target:
                base = value
        if base is None:
            # No point reaches back far enough — only use the earliest point as
            # the baseline when the series roughly spans the window (a few days
            # slack for weekends), otherwise the change is not meaningful.
            if (
                earliest_date is not None
                and (latest_date - earliest_date).days >= lookback_days - 4
            ):
                base = points[0][1]
        if not base:
            return None
        return round((latest_value - base) / base * 100, 2)

    result["change_7d_percent"] = _change(7)
    result["change_30d_percent"] = _change(30)
    return result


def build_label_allocation(
    holdings_list: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Value-weighted allocation across the labels assigned to holdings.

    Each holding in the V3 combined report may carry an inline ``labels`` array
    (``{id, name, color, ...}``); this sums holding value into each distinct
    label it carries and expresses that as a percentage of the WHOLE portfolio
    value (all holdings, labelled or not).  Labels are non-exclusive, so a
    holding with several labels contributes to each and the percentages can sum
    to more than 100% — the figure is "share of portfolio value carrying this
    label".  Returns an empty list (so the caller can skip assignment) when no
    holding carries a label, and is fully tolerant of the field's absence.
    """
    total = 0.0
    buckets: dict[str, dict[str, Any]] = {}
    for holding in holdings_list or []:
        if not isinstance(holding, dict):
            continue
        value = _f(holding.get("value")) or 0.0
        total += value
        labels = holding.get("labels")
        if not isinstance(labels, list) or not labels:
            continue
        seen_names: set[str] = set()
        for label in labels:
            if isinstance(label, dict):
                name = label.get("name")
            elif isinstance(label, str):
                name = label
            else:
                name = None
            # A holding should count once per distinct label name even if the
            # payload duplicates it.
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            entry = buckets.setdefault(name, {"value": 0.0, "holding_count": 0})
            entry["value"] += value
            entry["holding_count"] += 1

    allocation: list[dict[str, Any]] = []
    for name, entry in buckets.items():
        percentage = round(entry["value"] / total * 100, 2) if total else 0
        allocation.append(
            {
                "label": name,
                "value": round(entry["value"], 2),
                "percentage": percentage,
                "holding_count": entry["holding_count"],
            }
        )
    allocation.sort(key=lambda item: item["value"], reverse=True)
    return allocation
