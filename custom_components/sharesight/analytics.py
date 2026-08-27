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
import itertools
from typing import Any


def _f(value: Any) -> float | None:
    """Best-effort float coercion (None on failure)."""
    if value is None:
        return None
    try:
        return float(value)
    except TypeError, ValueError:
        return None


def _fx_rate(record: dict[str, Any]) -> float | None:
    """The usable ``exchange_rate`` on a payout or trade, if any.

    Sharesight stamps every payout and trade with the rate that converts its
    native amount into the portfolio currency.  Zero, negative and unparseable
    rates are rejected so a bad row falls back to the raw figure rather than
    producing an infinity.
    """
    rate = _f(record.get("exchange_rate"))
    if rate is None or rate <= 0:
        return None
    return rate


def to_portfolio_currency(record: dict[str, Any], amount: Any) -> float | None:
    """Convert ``amount`` from a record's native currency to the portfolio's.

    V2 payouts and trades carry ``amount``/``price``/``brokerage`` in their own
    currency plus an ``exchange_rate``.  Empirically - and consistent with
    Sharesight's own converted ``trades[].value`` - the rate is *native units
    per portfolio unit*, so the conversion is a division::

        0.65 USD / 0.6633 = 0.98 AUD
        128.00 USD / 0.666356 = 192.09 AUD   (matches trades[].value)

    Summing the raw amounts instead - which the income, brokerage and tax
    aggregates all used to do - produces a number in no currency at all as
    soon as a portfolio holds anything foreign.

    Returns None when ``amount`` is not a number; falls back to the raw amount
    when the record carries no usable rate.
    """
    raw = _f(amount)
    if raw is None:
        return None
    rate = _fx_rate(record)
    return raw / rate if rate is not None else raw


def record_currency_code(record: dict[str, Any]) -> str | None:
    """Return a monetary record's native three-letter currency, when present."""
    for candidate in (
        record.get("currency_code"),
        record.get("currency"),
        record.get("native_currency"),
        record.get("instrument_currency"),
    ):
        if isinstance(candidate, dict):
            candidate = candidate.get("code")
        if candidate:
            return str(candidate).upper()
    return None


def monetary_amount_details(
    record: dict[str, Any], amount: Any, portfolio_currency: str | None
) -> dict[str, Any]:
    """Describe a native amount and its safe portfolio-currency equivalent.

    A foreign amount without a usable exchange rate is deliberately not
    relabelled as portfolio currency. Callers still receive the raw amount and
    native currency for display or audit, while the converted ``amount`` is
    ``None`` so automations cannot accidentally sum mixed currencies.
    """
    raw = _f(amount)
    native_currency = record_currency_code(record)
    target_currency = str(portfolio_currency or "").upper() or None
    rate = _fx_rate(record)

    converted: float | None = None
    if raw is not None:
        if native_currency and target_currency:
            if native_currency == target_currency:
                converted = raw
            elif rate is not None:
                converted = raw / rate
        else:
            # Older payloads omit the currency code. Preserve the established
            # best effort, but publish the native currency as unknown.
            converted = raw / rate if rate is not None else raw

    return {
        "amount": converted,
        "currency": target_currency,
        "native_amount": raw,
        "native_currency": native_currency,
        "exchange_rate": rate,
    }


def brokerage_to_portfolio_currency(
    trade: dict[str, Any],
    amount: Any,
    portfolio_currency: str | None,
    instrument_currency: str | None = None,
) -> float | None:
    """Convert brokerage only when its denomination makes that safe.

    Sharesight permits brokerage in the portfolio currency, the instrument
    currency, or an unrelated third currency. ``exchange_rate`` converts the
    instrument currency only, so applying it to every brokerage value silently
    corrupts third-currency fees. Explicit ambiguous values are omitted; old
    payloads with no denomination retain the historical best-effort behavior.
    """
    raw = _f(amount)
    if raw is None:
        return None

    brokerage_code = str(trade.get("brokerage_currency_code") or "").upper()
    portfolio_code = str(portfolio_currency or "").upper()
    embedded_instrument_code = (
        trade.get("instrument_currency_code")
        or (trade.get("instrument") or {}).get("currency_code")
        or instrument_currency
    )
    instrument_code = str(embedded_instrument_code or "").upper()

    if brokerage_code and portfolio_code and brokerage_code == portfolio_code:
        return raw
    if brokerage_code and instrument_code and brokerage_code == instrument_code:
        return to_portfolio_currency(trade, raw)
    if brokerage_code and portfolio_code:
        return None
    return to_portfolio_currency(trade, raw)


def holding_currency(holding: dict[str, Any]) -> str | None:
    """The instrument's own currency code for a holding.

    The V3 report/holdings rows carry it as ``instrument_currency.code``; the
    embedded instrument repeats it as ``currency_code``.  Needed wherever a
    figure is denominated in the instrument's currency rather than the
    portfolio's - the market price, the average buy price, brokerage.
    """
    if not isinstance(holding, dict):
        return None
    for candidate in (
        (holding.get("instrument_currency") or {}).get("code"),
        holding.get("currency_code"),
        (holding.get("instrument") or {}).get("currency_code"),
    ):
        if candidate:
            return str(candidate).upper()
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
    return holding.get("market") or instrument.get("market_code") or holding.get("market_code")


# Field-name fallbacks for the two numbers that decide whether a holding row
# still represents a position: the report, the V3 holdings list and the
# diversity payload each spell them slightly differently.
_QUANTITY_FIELDS = ("quantity", "number_of_units", "units")
_VALUE_FIELDS = ("value", "market_value", "total_value", "current_value", "last_value")

# Dust thresholds.  Sharesight keeps a sold-out holding in the report when the
# sale doesn't net to exactly zero (a rounding residue such as -4e-05 shares,
# flagged ``valid_position: false``).  No real position is a ten-thousandth of
# a share, and none is worth less than half a cent.
_CLOSED_QUANTITY_EPSILON = 1e-4
_CLOSED_VALUE_EPSILON = 0.005


def _first_number(holding: dict[str, Any], fields: tuple[str, ...]) -> float | None:
    """First field of ``fields`` present on ``holding`` as a float."""
    for field in fields:
        number = _f(holding.get(field))
        if number is not None:
            return number
    return None


def is_open_position(holding: dict[str, Any]) -> bool:
    """Whether a holding row still represents a position the user holds.

    The performance report is requested with ``include_sales=false``, so a
    holding you sell out of normally drops out of it entirely.  The exception
    is a sale that leaves a rounding residue: the row survives carrying dust
    quantity, no value and ``valid_position: false``, which keeps a sold
    holding's device alive and lets a $0 ghost skew "smallest holding", the
    holding count and label allocation.

    Only dust is filtered.  A row is kept whenever it has no quantity to judge
    by, whenever its quantity is materially non-zero (so a short position —
    negative quantity, also ``valid_position: false`` — stays), and whenever it
    still carries a value despite a dust quantity.
    """
    if not isinstance(holding, dict):
        return False
    quantity = _first_number(holding, _QUANTITY_FIELDS)
    if quantity is None or abs(quantity) >= _CLOSED_QUANTITY_EPSILON:
        return True
    value = _first_number(holding, _VALUE_FIELDS)
    return value is not None and abs(value) >= _CLOSED_VALUE_EPSILON


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


# Above this, a "yield on cost" is an artefact of a shrunken denominator
# rather than a real figure (a position mostly sold during the trailing year
# keeps its income but loses its cost base).  Real yields, including the
# option-income ETFs that make this integration interesting, sit far below it.
_MAX_PLAUSIBLE_YIELD_PERCENT = 200.0


def payout_pay_date(payout: dict[str, Any]) -> str | None:
    """The date a payout was (or will be) paid, tolerating shape differences."""
    for field in ("paid_on", "pay_date", "date"):
        value = payout.get(field)
        if value:
            return str(value)
    return None


def payout_ex_date(payout: dict[str, Any]) -> str | None:
    """The date a payout goes ex-dividend.

    Sharesight's V2 payout calls this ``goes_ex_on``; only the integration's
    own derived forecast shape uses ``ex_date``.  Reading just ``ex_date`` -
    as the activity diff and the ``get_income`` service used to - resolves to
    None on every real payout, which both blanked the field in the emitted
    events and collapsed the de-duplication key for announced payouts (whose
    ``id`` is null until they are confirmed).
    """
    for field in ("goes_ex_on", "ex_date"):
        value = payout.get(field)
        if value:
            return str(value)
    return None


def holding_symbol_aliases(holding: dict[str, Any]) -> set[str]:
    """Every spelling of a holding's symbol that appears in the codebase.

    ``holding_symbol`` resolves ``instrument_code -> instrument.code -> code ->
    symbol`` while the sensor platform's own resolver walks that list in the
    opposite order.  On every payload Sharesight actually returns the two agree
    (only ``instrument.code`` is populated), but a payload carrying more than
    one of them would make a per-holding income or trade sensor look up a key
    the map was never built under.  Registering both spellings removes the
    whole class of mismatch without changing which spelling becomes the
    entity's ``local_name`` — and so without touching any unique_id.
    """
    if not isinstance(holding, dict):
        return set()
    instrument = holding.get("instrument") or {}
    candidates = (
        holding.get("instrument_code"),
        instrument.get("code"),
        holding.get("code"),
        holding.get("symbol"),
        instrument.get("symbol"),
    )
    return {str(value) for value in candidates if value}


def _alias_entries(result: dict[str, dict[str, Any]], holdings: list[dict[str, Any]]) -> None:
    """Point every alias of a held symbol at the same entry object."""
    for holding in holdings or []:
        primary = holding_symbol(holding)
        if not primary or primary not in result:
            continue
        entry = result[primary]
        for alias in holding_symbol_aliases(holding):
            result.setdefault(alias, entry)


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

    Amounts are converted to the portfolio currency with each payout's own
    ``exchange_rate`` (see ``to_portfolio_currency``) so a portfolio holding
    both AUD and USD payers produces a total in one currency.  Franking
    credits and withholding tax are left alone: Sharesight documents those as
    already being in the portfolio currency.
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
        pay_date = payout_pay_date(payout)
        amount = to_portfolio_currency(payout, payout.get("amount")) or 0.0
        entry["count"] += 1
        if pay_date and (
            entry["last_dividend_date"] is None or str(pay_date)[:10] > entry["last_dividend_date"]
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
        entry["held"] = symbol in cost_by_symbol
        cost_base = cost_by_symbol.get(symbol)
        # A cost base has to be positive to divide by, and it has to be the
        # cost base of roughly the position that earned the income: selling
        # most of a holding during the year collapses the denominator while
        # the trailing income still reflects the old position, which produced
        # figures like 8579% on real data.  Above the ceiling the number is
        # not a yield, it is an artefact, so publish nothing.
        if cost_base is not None and cost_base > 0:
            candidate = round(entry["ttm_income"] / cost_base * 100, 2)
            entry["yield_on_cost"] = (
                candidate if candidate <= _MAX_PLAUSIBLE_YIELD_PERCENT else None
            )
        else:
            entry["yield_on_cost"] = None
    _alias_entries(result, holdings)
    return result


# Transaction types that add or remove shares without changing what was paid
# for them.  Sharesight books the *difference* in share count (a 5:1
# consolidation of 241.0123 shares is a CONSOLD row of 192.80984, leaving
# 48.20246), so these are applied against the running position.
_SPLIT_TYPES = frozenset({"SPLIT", "BONUS"})
_CONSOLIDATION_TYPES = frozenset({"CONSOLD"})
# Cost-bearing purchases: the only rows that feed the VWAP numerator.
_BUY_TYPES = frozenset({"BUY", "OPENING BALANCE", "OPENING_BALANCE"})
# Plain reductions — they retire shares but leave the purchase history alone.
_SELL_TYPES = frozenset({"SELL", "CANCEL", "MERGE_CANCEL"})
# Capital returns hand money back without touching the share count, which
# lowers what the remaining shares effectively cost.
_CAPITAL_RETURN_TYPES = frozenset({"CAPITAL_RETURN"})


def _trade_order_key(trade: dict[str, Any]) -> tuple[str, float]:
    """Chronological sort key (date, then id) for replaying a holding's trades."""
    trade_date = str(trade.get("transaction_date") or trade.get("date") or "")[:10]
    return (trade_date, _f(trade.get("id")) or 0.0)


def _rescale_buy_quantity(entry: dict[str, Any], old_quantity: float, new_quantity: float) -> None:
    """Restate accumulated buy quantity across a split or consolidation.

    Neither event changes the money spent, only how many shares that money
    bought, so the buy quantity moves by the same ratio as the position and the
    VWAP comes out as a per-share cost in post-event shares.  Skipped when
    either side is non-positive — there is no meaningful ratio then.
    """
    if old_quantity <= 0 or new_quantity <= 0:
        return
    entry["_buy_qty"] *= new_quantity / old_quantity


def build_holding_trades(
    trades: list[dict[str, Any]],
    holdings: list[dict[str, Any]],
    portfolio_currency: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Per-holding trade activity keyed by symbol.

    From the already-fetched trades list: trade count, last trade date,
    brokerage paid, net shares held and a volume-weighted average BUY price
    (sum(qty*price)/sum(qty) over BUY + OPENING BALANCE rows).

    Trades are replayed in date order so share splits, bonus issues,
    consolidations and capital returns can be applied against the running
    position: without that a consolidated holding reports the
    pre-consolidation share count (and a VWAP off by the consolidation ratio)
    long after it has been sold.  The VWAP remains an approximation in
    instrument currency — it does not restate historic buys at the exchange
    rate Sharesight's official average purchase price uses.
    """
    id_to_symbol = _holding_id_to_symbol(holdings)
    symbol_currencies = {
        str(symbol): holding_currency(holding)
        for holding in holdings or []
        if isinstance(holding, dict) and (symbol := holding_symbol(holding)) is not None
    }
    result: dict[str, dict[str, Any]] = {}
    ordered_trades = sorted(
        (trade for trade in trades or [] if isinstance(trade, dict)),
        key=_trade_order_key,
    )
    for trade in ordered_trades:
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
        if trade_date and (entry["last_date"] is None or str(trade_date)[:10] > entry["last_date"]):
            entry["last_date"] = str(trade_date)[:10]
        # Brokerage can be in the portfolio currency, instrument currency, or
        # an unrelated third currency. The trade exchange_rate only converts
        # the instrument currency, so never apply it blindly to all three.
        brokerage = brokerage_to_portfolio_currency(
            trade,
            trade.get("brokerage"),
            portfolio_currency,
            symbol_currencies.get(str(symbol)),
        )
        if brokerage is not None:
            entry["brokerage"] += brokerage
        qty = _f(trade.get("quantity")) or 0.0
        price = _f(trade.get("price")) or 0.0
        trade_type = str(trade.get("transaction_type") or "").upper()
        if trade_type in _BUY_TYPES:
            entry["_buy_qty"] += qty
            entry["_buy_cost"] += qty * price
            entry["net_shares"] += qty
        elif trade_type in _SELL_TYPES:
            entry["net_shares"] -= qty
        elif trade_type in _SPLIT_TYPES:
            _rescale_buy_quantity(entry, entry["net_shares"], entry["net_shares"] + qty)
            entry["net_shares"] += qty
        elif trade_type in _CONSOLIDATION_TYPES:
            _rescale_buy_quantity(entry, entry["net_shares"], entry["net_shares"] - qty)
            entry["net_shares"] -= qty
        elif trade_type in _CAPITAL_RETURN_TYPES:
            # ``capital_return_value`` is the whole distribution for the
            # holding, in the same currency as the trade prices.
            capital_return = _f(trade.get("capital_return_value"))
            if capital_return:
                entry["_buy_cost"] = max(0.0, entry["_buy_cost"] - abs(capital_return))

    for entry in result.values():
        entry["brokerage"] = round(entry["brokerage"], 2)
        entry["net_shares"] = round(entry["net_shares"], 4)
        buy_qty = entry.pop("_buy_qty", 0.0)
        buy_cost = entry.pop("_buy_cost", 0.0)
        entry["vwap_buy_price"] = round(buy_cost / buy_qty, 4) if buy_qty > 0 else None
    _alias_entries(result, holdings)
    return result


# Where each allocation axis lives on a holding's own embedded instrument.
# Reading these first means sector/industry/type allocation no longer depends
# on the OPTIONAL user_instruments feed: the V3 report and holdings rows carry
# the classification inline, so the breakdown survives a token whose scope
# cannot reach user_instruments.
_AXIS_HOLDING_FIELDS = {
    "sector": ("sector_classification_name",),
    "industry": ("industry_classification_name",),
    "instrument_type": (
        "friendly_instrument_description",
        "security_type",
    ),
}


def _axis_value(holding: dict[str, Any], instrument: dict[str, Any], axis: str) -> str:
    """The allocation bucket a holding belongs to on ``axis``."""
    embedded = holding.get("instrument")
    if isinstance(embedded, dict):
        for field in _AXIS_HOLDING_FIELDS.get(axis, ()):
            if value := embedded.get(field):
                return str(value)
    if value := instrument.get(axis):
        return str(value)
    return "Unknown"


def _breakdown(buckets: dict[str, float], total: float) -> dict[str, Any]:
    """Sort value buckets into the ``{breakdown, total}`` shape sensors read."""
    breakdown: list[dict[str, Any]] = []
    for name, value in buckets.items():
        percentage = round(value / total * 100, 2) if total else 0
        breakdown.append({"group_name": name, "value": round(value, 2), "percentage": percentage})
    breakdown.sort(key=lambda item: item["value"], reverse=True)
    return {"breakdown": breakdown, "total": round(total, 2)}


def build_sector_allocation(
    holdings: list[dict[str, Any]],
    instrument_lookup: dict[str, dict[str, Any]],
    axis: str = "sector",
) -> dict[str, Any]:
    """Value-weighted sector, industry or investment-type allocation.

    Prefers the classification embedded in the holding itself and falls back
    to the user_instruments feed, returning a diversity-style ``breakdown``
    list so the existing top-N helper can consume it.  ``axis`` is "sector",
    "industry" or "instrument_type".  Unclassified holdings land in "Unknown".
    """
    buckets: dict[str, float] = {}
    total = 0.0
    for holding in holdings or []:
        if not isinstance(holding, dict):
            continue
        instrument = lookup_instrument(instrument_lookup, holding) or {}
        name = _axis_value(holding, instrument, axis)
        value = _f(holding.get("value")) or 0.0
        buckets[name] = buckets.get(name, 0.0) + value
        total += value
    return _breakdown(buckets, total)


def build_currency_allocation(
    holdings: list[dict[str, Any]], base_currency: str | None = None
) -> dict[str, Any]:
    """Value-weighted allocation across the currencies holdings are priced in.

    Every holding row carries ``instrument_currency.code``, so this needs no
    extra request and works even when the FX-rate endpoint is out of scope.
    ``base_currency`` is only used to label the result; the buckets are the
    currencies actually held.
    """
    buckets: dict[str, float] = {}
    total = 0.0
    for holding in holdings or []:
        if not isinstance(holding, dict):
            continue
        code = holding_currency(holding) or "UNKNOWN"
        value = _f(holding.get("value")) or 0.0
        buckets[code] = buckets.get(code, 0.0) + value
        total += value
    result = _breakdown(buckets, total)
    result["base_currency"] = (base_currency or "").upper() or None
    return result


def _parse_date(value: Any) -> date | None:
    """Parse a leading ``YYYY-MM-DD`` out of a value into a date (None on fail)."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError, TypeError:
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


# Below this share of equity value, a "portfolio P/E" says more about which
# instruments happen to publish a ratio than about the portfolio.  Sharesight
# only carries pe_ratio for a minority of instruments (2% of one real
# portfolio's value), so the figure is suppressed rather than published.
_MIN_PE_COVERAGE_PERCENT = 50.0


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
    base_currency: str | None = None,
) -> dict[str, Any]:
    """Portfolio-level concentration, quality and composition metrics.

    Pure join of the holdings list against the already-fetched
    user_instruments feed (via ``instrument_lookup``) plus the performance
    ``report`` for cash.  Every field degrades to ``None``/``0`` when its
    inputs are missing rather than raising, so it is safe to call every poll.

    - ``hhi``: Herfindahl-Hirschman concentration over equity weights
      (→0 perfectly diversified, →1 a single holding).
    - ``effective_holdings``: inverse-Simpson effective holding count (1/HHI).
    - ``weighted_yield``: value-weighted across EVERY holding, counting a
      holding that pays nothing as 0% rather than dropping it from the
      denominator (which used to inflate the figure by the share of the
      portfolio that pays no dividends).  No Sharesight payload carries a
      yield field, so it comes from each holding's trailing-12-month income.
    - ``weighted_pe``: the harmonic mean (portfolio earnings yield inverted),
      which is the arithmetically correct way to aggregate P/E ratios, and
      suppressed entirely below ``_MIN_PE_COVERAGE_PERCENT`` of equity value —
      an "average P/E" backed by 2% of the portfolio is noise.
      ``pe_coverage_percent`` reports the backing either way.
    - ``fx_exposure_percent``: share of equity value not in ``base_currency``
      (the portfolio's own currency), falling back to the largest bucket only
      when no base currency is known.  ``None`` when no holding exposes a
      currency at all, rather than a confident 0%.
    - ``cash_drag_percent``: cash / (equity + cash).
    - ``stale_price_count``: holdings whose instrument price is >3 days old,
      or ``None`` when no holding carries a parseable price timestamp (which
      is what happens when the optional user_instruments feed is out of scope
      — reporting a confident 0 there was a lie).
    """
    result: dict[str, Any] = {
        "hhi": None,
        "effective_holdings": None,
        "weighted_yield": None,
        "yield_coverage_percent": None,
        "weighted_pe": None,
        "pe_coverage_percent": None,
        "fx_exposure_percent": None,
        "cash_drag_percent": None,
        "stale_price_count": None,
        "price_timestamp_coverage_percent": None,
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
    currency_known_value = 0.0
    pe_weight = 0.0
    pe_earnings_weight = 0.0
    yield_weight = 0.0
    yield_weighted_sum = 0.0
    stale_count = 0
    priced_holdings = 0
    total_holdings = 0

    for holding in holdings_list:
        if not isinstance(holding, dict):
            continue
        total_holdings += 1
        instrument = lookup_instrument(instrument_lookup, holding) or {}
        value = _f(holding.get("value"))
        if value is not None and value > 0:
            values.append(value)
            equity_value += value

            # instrument_currency lives on every V3 report/holdings row, so
            # this no longer depends on the optional user_instruments feed.
            currency = holding_currency(holding) or instrument.get("currency_code")
            if currency:
                currency = str(currency).upper()
                currency_buckets[currency] = currency_buckets.get(currency, 0.0) + value
                currency_known_value += value

            pe = _f(instrument.get("pe_ratio"))
            if pe is not None and pe > 0:
                pe_weight += value
                # Harmonic mean: sum the earnings each holding contributes,
                # then invert.  An arithmetic mean over-weights the expensive
                # names and is not a portfolio P/E at all.
                pe_earnings_weight += value / pe

            holding_yield = _holding_yield(holding, instrument)
            if holding_yield is None:
                holding_yield = _trailing_yield(holding, value, holding_income)
            # A holding with no payouts has a KNOWN yield of 0%, not an
            # unknown one, so it belongs in the denominator.
            if holding_yield is None and isinstance(holding_income, dict):
                holding_yield = 0.0
            if holding_yield is not None and holding_yield >= 0:
                yield_weight += value
                yield_weighted_sum += value * holding_yield

        price_timestamp = instrument.get("current_price_updated_at")
        if _parse_date(price_timestamp) is not None:
            priced_holdings += 1
            if _price_is_stale(price_timestamp, today):
                stale_count += 1

    if priced_holdings:
        result["stale_price_count"] = stale_count
    if total_holdings:
        result["price_timestamp_coverage_percent"] = round(
            priced_holdings / total_holdings * 100, 2
        )

    if equity_value > 0 and values:
        hhi = sum((value / equity_value) ** 2 for value in values)
        result["hhi"] = round(hhi, 4)
        if hhi > 0:
            result["effective_holdings"] = round(1.0 / hhi, 2)
        if currency_known_value > 0:
            base = (base_currency or "").upper()
            if base and base in currency_buckets:
                base_value = currency_buckets[base]
            else:
                # No declared base currency: the largest bucket is the best
                # available guess, which is what this always did.
                base_value = max(currency_buckets.values())
            result["fx_exposure_percent"] = round(
                (currency_known_value - base_value) / currency_known_value * 100, 2
            )

    if pe_weight > 0 and pe_earnings_weight > 0 and equity_value > 0:
        coverage = round(pe_weight / equity_value * 100, 2)
        result["pe_coverage_percent"] = coverage
        if coverage >= _MIN_PE_COVERAGE_PERCENT:
            result["weighted_pe"] = round(pe_weight / pe_earnings_weight, 2)
    if yield_weight > 0:
        result["weighted_yield"] = round(yield_weighted_sum / yield_weight, 2)
        if equity_value > 0:
            result["yield_coverage_percent"] = round(yield_weight / equity_value * 100, 2)

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
    held_symbols: set[str] | None = None,
) -> dict[str, Any]:
    """Forward dividend-income projection.

    Two legs, combined without double counting:

    * **Announced** - the ``upcoming_payouts`` window Sharesight has already
      declared, grouped by pay month.  Amounts are converted to the portfolio
      currency with each payout's own ``exchange_rate``.
    * **Projected** - each *currently held* payer's trailing-12-month income
      as a run-rate.  For a payer that also has announcements, only the part
      of the year the announcements do not already cover is projected, so an
      announced payer no longer silently loses its remaining eleven months.

    ``held_symbols`` is what keeps the projection honest.  ``holding_income``
    is keyed by every symbol that appears in the payouts feed, which includes
    positions sold years ago - projecting their run-rate forward produced a
    "forward annual income" that was mostly income from holdings the user no
    longer owns.  Pass the symbols currently held and exited positions drop
    out; pass None and the old (over-stated) behaviour is preserved for
    callers that cannot supply it.

    The 30- and 90-day windows blend the same two legs, so they stop being
    exact duplicates of "announced income unpaid" for portfolios whose payers
    announce only a few weeks ahead.

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
    announced_30d = 0.0
    announced_90d = 0.0
    announced_by_symbol: dict[str, float] = {}
    next_date: date | None = None
    horizon_30 = today + timedelta(days=30)
    horizon_90 = today + timedelta(days=90)

    for payout in upcoming_payouts:
        if not isinstance(payout, dict):
            continue
        amount = to_portfolio_currency(payout, payout.get("amount"))
        if amount is None:
            amount = to_portfolio_currency(payout, payout.get("gross_amount"))
        amount = amount or 0.0
        announced_total += amount

        symbol = payout.get("symbol")
        if symbol:
            announced_by_symbol[str(symbol)] = announced_by_symbol.get(str(symbol), 0.0) + amount

        pay_date = _parse_date(payout_pay_date(payout))
        upcoming_date = pay_date or _parse_date(payout_ex_date(payout))

        if pay_date is not None:
            month_key = pay_date.strftime("%Y-%m")
            income_by_month[month_key] = round(income_by_month.get(month_key, 0.0) + amount, 2)
            if today <= pay_date <= horizon_30:
                announced_30d += amount
            if today <= pay_date <= horizon_90:
                announced_90d += amount

        if (
            upcoming_date is not None
            and upcoming_date >= today
            and (next_date is None or upcoming_date < next_date)
        ):
            next_date = upcoming_date

    # Projected leg: the TTM run-rate of holdings still owned, net of whatever
    # each has already announced.
    projected_annual = 0.0
    projected_30d = 0.0
    projected_90d = 0.0
    for symbol, entry in holding_income.items():
        if not isinstance(entry, dict):
            continue
        if held_symbols is not None and symbol not in held_symbols:
            continue
        # build_holding_income marks entries it could match to a live holding.
        if held_symbols is None and entry.get("held") is False:
            continue
        ttm = _f(entry.get("ttm_income"))
        if not ttm or ttm <= 0:
            continue
        announced = announced_by_symbol.get(symbol, 0.0)
        projected_annual += max(0.0, ttm - announced)
        daily = ttm / 365.0
        projected_30d += max(0.0, daily * 30 - announced)
        projected_90d += max(0.0, daily * 90 - announced)

    forward_annual_income = announced_total + projected_annual
    result["announced_income"] = round(announced_total, 2)
    result["income_30d"] = round(announced_30d + projected_30d, 2)
    result["income_90d"] = round(announced_90d + projected_90d, 2)
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
            parsed = _parse_date(item.get("date") or item.get("timestamp") or item.get("as_at"))
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
    result["series"] = [{"date": date_str, "value": round(value, 2)} for date_str, value in points]

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


def build_value_analytics(series: Any) -> dict[str, Any]:
    """Risk metrics derived from the daily portfolio value series.

    Every figure here comes from a payload the integration already fetches for
    the value-trend sensors, so it costs no extra request.  Sharesight's own
    ``/benchmark`` report publishes a maximum drawdown for the *benchmark*;
    this is the portfolio's own, computed the same way.

    * ``all_time_high`` / ``all_time_high_date`` - the peak in the window.
    * ``max_drawdown_percent`` - the largest peak-to-trough fall in the window.
    * ``current_drawdown_percent`` - how far below the peak the latest value
      sits (0 when at a new high).
    * ``days_since_high`` - days since the peak was set.
    * ``volatility_percent`` - the standard deviation of the percentage moves
      between consecutive points, annualised.  Sharesight thins the series
      (points can be three or more days apart), so the annualisation scales by
      the observed average spacing rather than assuming one point per trading
      day; assuming daily points inflates the figure several-fold.  None when
      there are fewer than three points to measure.

    The window is whatever the caller fetched: the trend sensors request ~45
    days, so these are "in the last 45 days" figures unless a longer series is
    supplied.  Degrades to all-None rather than raising.
    """
    result: dict[str, Any] = {
        "all_time_high": None,
        "all_time_high_date": None,
        "max_drawdown_percent": None,
        "current_drawdown_percent": None,
        "days_since_high": None,
        "volatility_percent": None,
        "point_count": 0,
    }
    points = _value_series_points(series)
    if not points:
        return result
    result["point_count"] = len(points)

    peak = float("-inf")
    peak_date: str | None = None
    max_drawdown = 0.0
    for date_str, value in points:
        if value > peak:
            peak, peak_date = value, date_str
        elif peak > 0:
            drawdown = (peak - value) / peak * 100
            max_drawdown = max(max_drawdown, drawdown)

    latest_date_str, latest_value = points[-1]
    if peak_date is not None and peak > 0:
        result["all_time_high"] = round(peak, 2)
        result["all_time_high_date"] = peak_date
        result["max_drawdown_percent"] = round(max_drawdown, 2)
        result["current_drawdown_percent"] = round(max(0.0, (peak - latest_value) / peak * 100), 2)
        high_date = _parse_date(peak_date)
        latest_date = _parse_date(latest_date_str)
        if high_date is not None and latest_date is not None:
            result["days_since_high"] = (latest_date - high_date).days

    returns: list[float] = []
    for (_, previous), (_, current) in itertools.pairwise(points):
        if previous:
            returns.append((current - previous) / previous)
    if len(returns) >= 2:
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        first_date = _parse_date(points[0][0])
        last_date = _parse_date(latest_date_str)
        span_days = (
            (last_date - first_date).days if first_date is not None and last_date is not None else 0
        )
        # Periods per year implied by the series' own spacing, capped at the
        # 252-trading-day convention so a same-day duplicate cannot blow the
        # figure up.
        average_gap = span_days / len(returns) if span_days > 0 else 1.0
        periods_per_year = min(252.0, 365.0 / max(average_gap, 1.0))
        result["volatility_percent"] = round((variance**0.5) * (periods_per_year**0.5) * 100, 2)

    return result


def _parcel_gain(parcel: dict[str, Any]) -> float | None:
    """The unrealised gain on a CGT parcel, tolerating field-name drift."""
    for field in ("unrealised_gain", "gain", "capital_gain"):
        value = _f(parcel.get(field))
        if value is not None:
            return value
    # Fall back to the arithmetic the report is doing anyway.
    market_value = _f(parcel.get("market_value"))
    cost_base = _f(parcel.get("cost_base"))
    if market_value is not None and cost_base is not None:
        return market_value - cost_base
    return None


def build_cgt_analytics(capital_gains: Any, unrealised_cgt: Any) -> dict[str, Any]:
    """Tax figures the CGT reports return but the integration never surfaced.

    Both reports are already fetched every poll for Australian portfolios and
    carry per-parcel arrays alongside the headline scalars.  The parcels are
    what make tax-loss harvesting answerable: how much unrealised loss is
    sitting there, across how many parcels, and how much short-term gain is
    close enough to the twelve-month mark to be worth waiting for.

    Every field degrades to None/0 when its report is absent (a non-AU
    portfolio never fetches them at all).
    """
    result: dict[str, Any] = {
        "claimable_loss": None,
        "short_term_losses": None,
        "long_term_losses": None,
        "cgt_concession_rate": None,
        "realised_market_value": None,
        "harvestable_loss": None,
        "harvestable_parcel_count": None,
        "unrealised_short_term_parcels": None,
        "unrealised_long_term_parcels": None,
        "unrealised_balance_date": None,
        "largest_loss_symbol": None,
        "largest_loss_amount": None,
        "largest_loss_purchased_on": None,
    }

    if isinstance(capital_gains, dict):
        result["claimable_loss"] = _f(capital_gains.get("claimable_loss"))
        result["short_term_losses"] = _f(capital_gains.get("short_term_losses"))
        result["long_term_losses"] = _f(capital_gains.get("long_term_losses"))
        result["cgt_concession_rate"] = _f(capital_gains.get("cgt_concession_rate"))
        result["realised_market_value"] = _f(capital_gains.get("market_value"))

    if isinstance(unrealised_cgt, dict):
        losses = unrealised_cgt.get("losses")
        if isinstance(losses, list):
            harvestable = 0.0
            worst: dict[str, Any] | None = None
            for parcel in losses:
                if not isinstance(parcel, dict):
                    continue
                # Verified against a live payload: a parcel is
                # {market, symbol, name, allocation_method, purchase_date,
                #  quantity, cost_base, market_value, unrealised_gain}, and a
                # loss is a negative unrealised_gain.
                gain = _parcel_gain(parcel)
                if gain is not None and gain < 0:
                    harvestable += abs(gain)
                    if worst is None or gain < (_parcel_gain(worst) or 0.0):
                        worst = parcel
            result["harvestable_loss"] = round(harvestable, 2)
            result["harvestable_parcel_count"] = len(losses)
            if worst is not None:
                result["largest_loss_symbol"] = worst.get("symbol")
                result["largest_loss_amount"] = round(abs(_parcel_gain(worst) or 0.0), 2)
                result["largest_loss_purchased_on"] = worst.get("purchase_date")
        for key, field in (
            ("unrealised_short_term_parcels", "short_term_parcels"),
            ("unrealised_long_term_parcels", "long_term_parcels"),
        ):
            parcels = unrealised_cgt.get(field)
            if isinstance(parcels, list):
                result[key] = len(parcels)
        if balance_date := unrealised_cgt.get("balance_date"):
            result["unrealised_balance_date"] = str(balance_date)

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
