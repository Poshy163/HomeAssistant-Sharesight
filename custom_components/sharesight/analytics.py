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
