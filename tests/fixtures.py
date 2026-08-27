"""Synthetic Sharesight payloads shaped exactly like the real API responses.

Every field name, nesting level and value type here was taken from a live
Home Assistant diagnostics dump of a real portfolio, so the fixtures exercise
the same parsing paths production does.  The *numbers* are invented and chosen
to be hand-checkable, which is what lets the tests assert exact expected values
instead of merely characterising whatever the code happens to produce.

Deliberate edge cases baked in, each covering a real failure mode:

* ``ZERO`` is a sold-out holding left behind as dust (``valid_position``
  false, a -3e-05 quantity) - it must be filtered out of every aggregate.
* ``GLB`` is a foreign-currency holding with a currency gain, so FX exposure
  and currency-allocation maths get a non-trivial input.
* ``STALE`` carries a ``current_price_updated_at`` well in the past to drive
  the stale-price analytics.
* Payouts include franking credits, foreign source income, both capital-gain
  distribution flavours, withholding tax and a DRP reinvestment.
* Trades include a SELL (whose ``value`` is NEGATIVE, exactly as Sharesight
  returns it), a SPLIT, a CAPITAL_RETURN and brokerage.
* ``portfolio_detail.financial_year_end`` is ``12-31`` - a calendar-year
  financial year, which the old June-30-only date maths got wrong.
"""

from __future__ import annotations

from typing import Any

PORTFOLIO_ID = 1020131
CURRENCY = "AUD"
TODAY = "2026-08-27"


def _instrument(
    code: str,
    market: str = "ASX",
    currency: str = CURRENCY,
    *,
    instrument_id: int,
    sector: str = "Finance",
    industry: str = "Investment Trusts/Mutual Funds",
    description: str = "Exchange Traded Fund",
) -> dict[str, Any]:
    """An ``instrument`` block as embedded in holdings/benchmark/watchlist."""
    return {
        "code": code,
        "country_id": 2,
        "crypto": False,
        "currency_code": currency,
        "expires_on": None,
        "expired": False,
        "id": instrument_id,
        "market_code": market,
        "name": f"{code} Test Instrument",
        "supported_denominations": None,
        "tz_name": "Australia/Sydney",
        "industry_classification_name": industry,
        "sector_classification_name": sector,
        "friendly_instrument_description": description,
        "friendly_instrument_description_code": description.lower().replace(" ", "_"),
        "logo": {
            "light_url": f"https://logo-assets.sharesight.com/{market}/light/{code}.svg",
            "dark_url": f"https://logo-assets.sharesight.com/{market}/dark/{code}.svg",
        },
    }


def _currency(code: str = CURRENCY) -> dict[str, Any]:
    return {
        "code": code,
        "id": 2 if code == CURRENCY else 5,
        "symbol": "$",
        "qualified_symbol": f"{code[:2]}$",
    }


def _holding(
    code: str,
    *,
    holding_id: int,
    instrument_id: int,
    market: str = "ASX",
    currency: str = CURRENCY,
    quantity: float,
    value: float,
    price: float,
    capital_gain: float,
    payout_gain: float = 0.0,
    currency_gain: float = 0.0,
    labels: list[str] | None = None,
    valid_position: bool = True,
    unconfirmed: int = 0,
    sector: str = "Finance",
    industry: str = "Investment Trusts/Mutual Funds",
    description: str = "Exchange Traded Fund",
) -> dict[str, Any]:
    cost = value - capital_gain
    total_gain = capital_gain + payout_gain + currency_gain
    return {
        "id": holding_id,
        "instrument": _instrument(
            code,
            market,
            currency,
            instrument_id=instrument_id,
            sector=sector,
            industry=industry,
            description=description,
        ),
        "instrument_currency": _currency(currency),
        "limited": False,
        "valid_position": valid_position,
        "inception_date": None,
        "quantity": quantity,
        "value": value,
        "instrument_price": price,
        "portfolio": {
            "id": PORTFOLIO_ID,
            "consolidated": False,
            "name": "Test Portfolio",
            "external_identifier": None,
        },
        "labels": [{"name": label} for label in (labels or [])],
        "group_id": 2 if market == "ASX" else 3,
        "group_name": market,
        "capital_gain": capital_gain,
        "capital_gain_percent": round(capital_gain / cost * 100, 2) if cost else 0.0,
        "payout_gain": payout_gain,
        "payout_gain_percent": round(payout_gain / cost * 100, 2) if cost else 0.0,
        "currency_gain": currency_gain,
        "currency_gain_percent": round(currency_gain / cost * 100, 2) if cost else 0.0,
        "total_gain": round(total_gain, 2),
        "total_gain_percent": round(total_gain / cost * 100, 2) if cost else 0.0,
        "number_of_unconfirmed_transactions": unconfirmed,
    }


HOLDINGS: list[dict[str, Any]] = [
    _holding(
        "AAA",
        holding_id=101,
        instrument_id=1001,
        quantity=1000.0,
        value=10000.0,
        price=10.0,
        capital_gain=1000.0,
        payout_gain=400.0,
        labels=["Core"],
    ),
    _holding(
        "BBB",
        holding_id=102,
        instrument_id=1002,
        quantity=500.0,
        value=4000.0,
        price=8.0,
        capital_gain=-500.0,
        payout_gain=100.0,
        labels=["Core", "Growth"],
        sector="Technology",
        industry="Semiconductors",
        description="Ordinary Share",
        unconfirmed=2,
    ),
    _holding(
        "GLB",
        holding_id=103,
        instrument_id=1003,
        market="NASDAQ",
        currency="USD",
        quantity=20.0,
        value=6000.0,
        price=200.0,
        capital_gain=800.0,
        currency_gain=-200.0,
        sector="Technology",
        industry="Computer Manufacturing",
        description="Ordinary Share",
    ),
    _holding(
        "STALE",
        holding_id=104,
        instrument_id=1004,
        quantity=100.0,
        value=2000.0,
        price=20.0,
        capital_gain=0.0,
        sector="Health Care",
        industry="Biotechnology",
        description="Ordinary Share",
    ),
    # Dust left behind by a sale that did not net to exactly zero.
    _holding(
        "ZERO",
        holding_id=105,
        instrument_id=1005,
        quantity=-3e-05,
        value=0.0,
        price=1.0,
        capital_gain=0.0,
        valid_position=False,
    ),
]

OPEN_HOLDINGS = [h for h in HOLDINGS if h["instrument"]["code"] != "ZERO"]
PORTFOLIO_VALUE = sum(h["value"] for h in OPEN_HOLDINGS)  # 22000.0

CASH_ACCOUNTS_IN_REPORT = [
    {
        "id": 900,
        "key": 900,
        "name": "Broker Cash",
        "source": None,
        "value": 3000.0,
        "currency": _currency(),
        "portfolio": {
            "id": PORTFOLIO_ID,
            "consolidated": False,
            "name": "Test Portfolio",
            "external_identifier": None,
        },
    }
]


def _sub_total(market: str, holdings: list[dict[str, Any]]) -> dict[str, Any]:
    value = sum(h["value"] for h in holdings)
    capital_gain = sum(h["capital_gain"] for h in holdings)
    payout_gain = sum(h["payout_gain"] for h in holdings)
    currency_gain = sum(h["currency_gain"] for h in holdings)
    cost = value - capital_gain
    total = capital_gain + payout_gain + currency_gain
    return {
        "value": round(value, 2),
        "group_id": 2 if market == "ASX" else 3,
        "group_name": market,
        "capital_gain": round(capital_gain, 2),
        "capital_gain_percent": round(capital_gain / cost * 100, 2) if cost else 0.0,
        "payout_gain": round(payout_gain, 2),
        "payout_gain_percent": round(payout_gain / cost * 100, 2) if cost else 0.0,
        "currency_gain": round(currency_gain, 2),
        "currency_gain_percent": round(currency_gain / cost * 100, 2) if cost else 0.0,
        "total_gain": round(total, 2),
        "total_gain_percent": round(total / cost * 100, 2) if cost else 0.0,
    }


def performance_report(
    start_date: str = "2023-07-18",
    end_date: str = TODAY,
    *,
    holdings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A V3 ``report`` body, matching the live payload field-for-field."""
    rows = HOLDINGS if holdings is None else holdings
    open_rows = [r for r in rows if r["instrument"]["code"] != "ZERO"]
    asx = [r for r in open_rows if r["group_name"] == "ASX"]
    nasdaq = [r for r in open_rows if r["group_name"] == "NASDAQ"]
    capital_gain = sum(r["capital_gain"] for r in open_rows)
    payout_gain = sum(r["payout_gain"] for r in open_rows)
    currency_gain = sum(r["currency_gain"] for r in open_rows)
    value = sum(r["value"] for r in open_rows)
    cost = value - capital_gain
    total = capital_gain + payout_gain + currency_gain
    return {
        "id": f"PerformanceReport_{PORTFOLIO_ID}",
        "portfolio_id": PORTFOLIO_ID,
        "portfolio_tz_name": "Australia/Sydney",
        "value": round(value, 2),
        "grouping": "market",
        "currency": _currency(),
        "start_date": start_date,
        "end_date": end_date,
        "include_sales": False,
        "capital_gain": round(capital_gain, 2),
        "capital_gain_percent": round(capital_gain / cost * 100, 2),
        "payout_gain": round(payout_gain, 2),
        "payout_gain_percent": round(payout_gain / cost * 100, 2),
        "currency_gain": round(currency_gain, 2),
        "currency_gain_percent": round(currency_gain / cost * 100, 2),
        "total_gain": round(total, 2),
        "total_gain_percent": round(total / cost * 100, 2),
        "percentages_annualised": True,
        "holdings": rows,
        "sub_totals": [_sub_total("ASX", asx), _sub_total("NASDAQ", nasdaq)],
        "combined_holdings": [],
        "cash_accounts": CASH_ACCOUNTS_IN_REPORT,
    }


def period_report(
    *,
    start_date: str,
    end_date: str,
    capital_gain: float,
    payout_gain: float = 0.0,
    currency_gain: float = 0.0,
) -> dict[str, Any]:
    """A V2 period performance body (one-day / one-week / ytd / FY)."""
    total = capital_gain + payout_gain + currency_gain
    return {
        "id": f"PerformanceReport_{PORTFOLIO_ID}",
        "portfolio_id": PORTFOLIO_ID,
        "grouping": "market",
        "custom_group_id": None,
        "value": PORTFOLIO_VALUE,
        "capital_gain": capital_gain,
        "capital_gain_percent": round(capital_gain / PORTFOLIO_VALUE * 100, 2),
        "payout_gain": payout_gain,
        "payout_gain_percent": round(payout_gain / PORTFOLIO_VALUE * 100, 2),
        "currency_gain": currency_gain,
        "currency_gain_percent": round(currency_gain / PORTFOLIO_VALUE * 100, 2),
        "total_gain": round(total, 2),
        "total_gain_percent": round(total / PORTFOLIO_VALUE * 100, 2),
        "start_date": start_date,
        "end_date": end_date,
        "include_sales": False,
        "holdings": OPEN_HOLDINGS,
        "cash_accounts": CASH_ACCOUNTS_IN_REPORT,
        "sub_totals": performance_report()["sub_totals"],
        "links": {"portfolio": "https://api.sharesight.com/api/v2/portfolios/1"},
    }


def _payout(
    symbol: str,
    *,
    payout_id: int | None,
    holding_id: int,
    instrument_id: int,
    paid_on: str,
    goes_ex_on: str,
    amount: float,
    franking_credits: float = 0.0,
    franked_amount: float = 0.0,
    unfranked_amount: float | None = None,
    foreign_source_income: float = 0.0,
    resident_withholding_tax: float = 0.0,
    non_resident_withholding_tax: float = 0.0,
    discounted_capital_gains: float = 0.0,
    non_discounted_capital_gains: float = 0.0,
    lic_capital_gain: float = 0.0,
    interest_payment: float = 0.0,
    confirmed: bool = True,
    drp: dict[str, Any] | None = None,
    market: str = "ASX",
    currency: str = CURRENCY,
) -> dict[str, Any]:
    payout: dict[str, Any] = {
        "id": payout_id,
        "portfolio_id": PORTFOLIO_ID,
        "holding_id": holding_id,
        "instrument_id": instrument_id,
        "symbol": symbol,
        "market": market,
        "paid_on": paid_on,
        "goes_ex_on": goes_ex_on,
        "amount": amount,
        "gross_amount": amount,
        "non_taxable": False,
        "comments": f"Dividend for {symbol}",
        "currency": currency,
        "exchange_rate": 1.0,
        "company_event_id": 5000 + (payout_id or 0),
        "confirmed": confirmed,
        "state": "confirmed" if confirmed else "unconfirmed",
        "links": {"portfolio": "https://api.sharesight.com/api/v2/portfolios/1"},
        "other_net_fsi": 0.0,
        "lic_capital_gain": lic_capital_gain,
        "non_resident_withholding_tax": non_resident_withholding_tax,
        "amit_decrease_amount": 0.0,
        "amit_increase_amount": 0.0,
        "resident_withholding_tax": resident_withholding_tax,
        "interest_payment": interest_payment,
        "discounted_capital_gains": discounted_capital_gains,
        "cgt_concession_amount": 0.0,
        "franking_credits": franking_credits,
        "franked_amount": franked_amount,
        "unfranked_amount": (
            amount - franked_amount if unfranked_amount is None else unfranked_amount
        ),
        "trust": False,
        "deferred_income": 0.0,
        "non_discounted_capital_gains": non_discounted_capital_gains,
        "foreign_source_income": foreign_source_income,
        "non_assessable": 0.0,
    }
    if drp is not None:
        payout["drp_trade_attributes"] = drp
    return payout


# Historic payouts.  Totals are deliberately easy to check by hand:
#   amount             = 100 + 200 + 50 + 25 = 375.00
#   franking_credits   =  30 +  60           =  90.00
#   franked_amount     =  70 + 140           = 210.00
#   unfranked_amount   =  30 +  60 + 50 + 25 = 165.00
#   foreign_source_income                    =  25.00
#   discounted_capital_gains                 =  12.00
#   non_discounted_capital_gains             =   8.00
#   non_resident_withholding_tax             =   5.00
PAYOUTS: list[dict[str, Any]] = [
    _payout(
        "AAA",
        payout_id=1,
        holding_id=101,
        instrument_id=1001,
        paid_on="2025-09-18",
        goes_ex_on="2025-09-01",
        amount=100.0,
        franking_credits=30.0,
        franked_amount=70.0,
    ),
    _payout(
        "AAA",
        payout_id=2,
        holding_id=101,
        instrument_id=1001,
        paid_on="2026-03-18",
        goes_ex_on="2026-03-01",
        amount=200.0,
        franking_credits=60.0,
        franked_amount=140.0,
        discounted_capital_gains=12.0,
        non_discounted_capital_gains=8.0,
    ),
    _payout(
        "BBB",
        payout_id=3,
        holding_id=102,
        instrument_id=1002,
        paid_on="2026-08-10",
        goes_ex_on="2026-07-28",
        amount=50.0,
        drp={
            "dividend_reinvested": True,
            "quantity": 6,
            "price": "8.0000",
            "source_adjustment_id": 91,
        },
    ),
    _payout(
        "GLB",
        payout_id=4,
        holding_id=103,
        instrument_id=1003,
        market="NASDAQ",
        currency="USD",
        paid_on="2026-06-30",
        goes_ex_on="2026-06-15",
        amount=25.0,
        foreign_source_income=25.0,
        non_resident_withholding_tax=5.0,
    ),
]

# Announced-but-unpaid payouts.  Note the null id - Sharesight does not assign
# one until the payout is confirmed, so any de-duplication key has to fall back
# to (symbol, goes_ex_on, amount).
UPCOMING_PAYOUTS: list[dict[str, Any]] = [
    _payout(
        "AAA",
        payout_id=None,
        holding_id=101,
        instrument_id=1001,
        paid_on="2026-09-18",
        goes_ex_on="2026-09-01",
        amount=110.0,
        confirmed=False,
        drp={
            "dividend_reinvested": True,
            "quantity": 11,
            "price": "10.0000",
            "source_adjustment_id": 92,
        },
    ),
    _payout(
        "BBB",
        payout_id=None,
        holding_id=102,
        instrument_id=1002,
        paid_on="2026-11-05",
        goes_ex_on="2026-10-20",
        amount=55.0,
        confirmed=False,
    ),
]


def _trade(
    symbol: str,
    *,
    trade_id: int,
    holding_id: int,
    instrument_id: int,
    transaction_date: str,
    transaction_type: str,
    quantity: float,
    price: float,
    value: float,
    brokerage: float = 0.0,
    market: str = "ASX",
) -> dict[str, Any]:
    return {
        "id": trade_id,
        "unique_identifier": str(900000 + trade_id),
        "transaction_date": transaction_date,
        "quantity": quantity,
        "price": price,
        "price_currency_code": None,
        "market_price": None,
        "market_price_exchange_rate": None,
        "cost_base": None,
        "exchange_rate": 1.0,
        "brokerage": brokerage,
        "brokerage_currency_code": CURRENCY,
        "value": value,
        "paid_on": None,
        "capital_return_value": None,
        "company_event_id": None,
        "comments": "",
        "portfolio_id": PORTFOLIO_ID,
        "holding_id": holding_id,
        "state": "confirmed",
        "transaction_type": transaction_type,
        "instrument_id": instrument_id,
        "symbol": symbol,
        "market": market,
        "attachment_filename": None,
        "attachment_id": None,
        "confirmed": True,
        "links": {"portfolio": "https://api.sharesight.com/api/v2/portfolios/1"},
    }


# Trades.  SELL values are NEGATIVE, exactly as the live API returns them:
#   buys  = 9000 + 4500 + 5200 + 2000 = 20700.00
#   sells = -1500                     = -1500.00  (magnitude 1500.00)
#   net capital deployed              = 19200.00
#   brokerage = 10 + 10 + 10 + 10 + 10 = 50.00
TRADES: list[dict[str, Any]] = [
    _trade(
        "AAA",
        trade_id=1,
        holding_id=101,
        instrument_id=1001,
        transaction_date="2024-02-01",
        transaction_type="BUY",
        quantity=900.0,
        price=10.0,
        value=9000.0,
        brokerage=10.0,
    ),
    _trade(
        "BBB",
        trade_id=2,
        holding_id=102,
        instrument_id=1002,
        transaction_date="2024-06-01",
        transaction_type="BUY",
        quantity=500.0,
        price=9.0,
        value=4500.0,
        brokerage=10.0,
    ),
    _trade(
        "GLB",
        trade_id=3,
        holding_id=103,
        instrument_id=1003,
        market="NASDAQ",
        transaction_date="2025-01-15",
        transaction_type="BUY",
        quantity=20.0,
        price=260.0,
        value=5200.0,
        brokerage=10.0,
    ),
    _trade(
        "STALE",
        trade_id=4,
        holding_id=104,
        instrument_id=1004,
        transaction_date="2025-03-01",
        transaction_type="BUY",
        quantity=100.0,
        price=20.0,
        value=2000.0,
        brokerage=10.0,
    ),
    _trade(
        "AAA",
        trade_id=5,
        holding_id=101,
        instrument_id=1001,
        transaction_date="2026-08-06",
        transaction_type="SELL",
        quantity=150.0,
        price=10.0,
        value=-1500.0,
        brokerage=10.0,
    ),
    _trade(
        "AAA",
        trade_id=6,
        holding_id=101,
        instrument_id=1001,
        transaction_date="2025-05-01",
        transaction_type="SPLIT",
        quantity=250.0,
        price=0.0,
        value=0.0,
    ),
    _trade(
        "BBB",
        trade_id=7,
        holding_id=102,
        instrument_id=1002,
        transaction_date="2025-07-01",
        transaction_type="CAPITAL_RETURN",
        quantity=0.0,
        price=0.0,
        value=-20.0,
    ),
]


CASH_ACCOUNTS_V2 = [
    {
        "id": 900,
        "name": "Broker Cash",
        "currency": CURRENCY,
        "portfolio_id": PORTFOLIO_ID,
        "portfolio_currency": CURRENCY,
        "date": TODAY,
        "balance": 3000.0,
        "balance_in_portfolio_currency": 3000.0,
        "links": {"portfolio": "https://api.sharesight.com/api/v2/portfolios/1"},
    }
]


def _cash_txn(
    txn_id: int, date_time: str, amount: float, type_name: str, balance: float
) -> dict[str, Any]:
    return {
        "id": txn_id,
        "description": "",
        "date_time": date_time,
        "amount": amount,
        "balance": balance,
        "cash_account_id": 900,
        "foreign_identifier": None,
        "holding_id": None,
        "trade_id": None,
        "payout_id": None,
        "cash_account_transaction_type": {"name": type_name},
        "links": {"portfolio": "https://api.sharesight.com/api/v2/portfolios/1"},
    }


# Deposits 20000 + 5000 = 25000; withdrawals 2000; net contributions 23000.
CASH_TRANSACTIONS = [
    _cash_txn(1, "2024-01-05T00:00:00.000Z", 20000.0, "DEPOSIT", 20000.0),
    _cash_txn(2, "2025-04-05T00:00:00.000Z", 5000.0, "DEPOSIT", 25000.0),
    _cash_txn(3, "2026-08-05T00:00:00.000Z", -2000.0, "WITHDRAWAL", 3000.0),
    # Not a contribution - must be ignored by the contributions maths.
    _cash_txn(4, "2026-08-06T00:00:00.000Z", 50.0, "DIVIDEND", 3050.0),
]


USER_INSTRUMENTS = {
    "instruments": [
        {
            "id": 1001,
            "code": "AAA",
            "market_code": "ASX",
            "name": "AAA Test Instrument",
            "currency_code": CURRENCY,
            "pe_ratio": 20.0,
            "nta": 0.0,
            "eps": 0.5,
            "current_price": 10.0,
            "current_price_updated_at": f"{TODAY}T16:10:13.000+10:00",
            "sector_classification_name": "Finance",
            "industry_classification_name": "Investment Trusts/Mutual Funds",
            "security_type": "Exchange Traded Fund",
            "friendly_instrument_description": "Exchange Traded Fund",
            "registry_name": "Test Registry",
        },
        {
            "id": 1002,
            "code": "BBB",
            "market_code": "ASX",
            "name": "BBB Test Instrument",
            "currency_code": CURRENCY,
            "pe_ratio": 10.0,
            "nta": 0.0,
            "eps": 0.8,
            "current_price": 8.0,
            "current_price_updated_at": f"{TODAY}T16:10:13.000+10:00",
            "sector_classification_name": "Technology",
            "industry_classification_name": "Semiconductors",
            "security_type": "Ordinary Share",
            "friendly_instrument_description": "Ordinary Share",
            "registry_name": "Test Registry",
        },
        {
            "id": 1003,
            "code": "GLB",
            "market_code": "NASDAQ",
            "name": "GLB Test Instrument",
            "currency_code": "USD",
            "pe_ratio": 40.0,
            "nta": 0.0,
            "eps": 5.0,
            "current_price": 200.0,
            "current_price_updated_at": f"{TODAY}T16:10:13.000+10:00",
            "sector_classification_name": "Technology",
            "industry_classification_name": "Computer Manufacturing",
            "security_type": "Ordinary Share",
            "friendly_instrument_description": "Ordinary Share",
            "registry_name": "Test Registry",
        },
        {
            "id": 1004,
            "code": "STALE",
            "market_code": "ASX",
            "name": "STALE Test Instrument",
            "currency_code": CURRENCY,
            "pe_ratio": None,
            "nta": 0.0,
            "eps": 0.0,
            "current_price": 20.0,
            # Deliberately old, to trip the stale-price analytics.
            "current_price_updated_at": "2026-07-01T16:10:13.000+10:00",
            "sector_classification_name": "Health Care",
            "industry_classification_name": "Biotechnology",
            "security_type": "Ordinary Share",
            "friendly_instrument_description": "Ordinary Share",
            "registry_name": "Test Registry",
        },
    ]
}


MY_USER = {
    "user": {
        "id": 846645,
        "name": "Test User",
        "first_name": "Test",
        "last_name": "User",
        "email": "test@example.invalid",
        "plan_code": "investor",
        "plan_label": "Standard",
        "is_activated": True,
        "is_free": False,
        "is_beta": False,
        "is_ai": False,
        "is_guest": False,
        "is_staff": False,
        "is_professional": False,
        "is_cancelled": False,
        "is_expired": False,
        "signed_up_at": "2024-01-22T19:19:45Z",
        "signup_via_your_integration": False,
    }
}


USER_SETTING = {
    "portfolio_user_setting": {
        "portfolio_chart": "VALUE",
        "portfolio_chart_range": "IN",
        "holding_chart": "PRICE",
        "holding_chart_range": "IN",
        "combined": "0",
        "report_combined": False,
        "grouping": "market",
        "report_grouping": "market",
        "report_currency": CURRENCY,
        "include_sold_shares": False,
        "report_include_sold_shares": False,
        "benchmark_instrument_id": 1851280,
        "taxable_show_comments": False,
        "taxable_grouped_by_holding": True,
        "overview_show_as_percentage": False,
        "overview_sort_column": "total_gain",
        "overview_sort_direction": "desc",
        "overview_start_date": None,
        "overview_end_date": None,
    }
}


PORTFOLIO_DETAIL = {
    "id": PORTFOLIO_ID,
    "consolidated": False,
    "name": "Test Portfolio",
    "external_identifier": None,
    "tz_name": "Australia/Sydney",
    "default_sale_allocation_method": "fifo",
    "cg_discount": "Individuals / Trust",
    # A calendar-year financial year: the case the old June-30-only date maths
    # placed entirely in the future.
    "financial_year_end": "12-31",
    "interest_method": "simple",
    "country_code": "AU",
    "currency_code": CURRENCY,
    "inception_date": "2023-07-18",
    "access_level": "OWNER",
    "user_id": 846645,
    "owner_name": "Test User",
    "rwtr_rate": 33.0,
    "trader": False,
    "disable_automatic_transactions": False,
    "tax_entity_type": "non_registered",
    "has_investments": True,
    "trade_sync_cash_account_id": None,
    "payout_sync_cash_account_id": None,
}


PORTFOLIOS = [PORTFOLIO_DETAIL]


BENCHMARK = {
    "start_date": "2023-07-18",
    "end_date": TODAY,
    "portfolio_tz_name": "Australia/Sydney",
    "instrument": _instrument("IDX", instrument_id=1851280),
    "capital_gain_percent": 8.84792589,
    "payout_gain_percent": 5.00571428,
    "currency_gain_percent": 0.0,
    "total_gain_percent": 13.85364017,
    "percentages_annualised": True,
    # Undocumented in the apiDoc but present in every live response.
    "maximum_drawdown": 8.4,
    "return_over_drawdown": 1.65,
}


WATCHLIST = {
    "watchlist": [
        {
            "instrument": {
                "id": 24025,
                "code": "WLA",
                "market_code": "NASDAQ",
                "name": "Watchlist A",
                "logo": {
                    "light_url": "https://logo-assets.sharesight.com/NASDAQ/light/WLA.svg",
                    "dark_url": "https://logo-assets.sharesight.com/NASDAQ/dark/WLA.svg",
                },
            },
            "price": {
                "value": 308.26,
                "timestamp": "2026-08-26T20:31:29Z",
                "diff_value": -5.07,
                "diff_percent": -1.62,
                "currency": _currency("USD"),
            },
        },
        {
            "instrument": {
                "id": 24026,
                "code": "WLB",
                "market_code": "ASX",
                "name": "Watchlist B",
                "logo": {
                    "light_url": "https://logo-assets.sharesight.com/ASX/light/WLB.svg",
                    "dark_url": "https://logo-assets.sharesight.com/ASX/dark/WLB.svg",
                },
            },
            "price": {
                "value": 42.0,
                "timestamp": "2026-08-26T06:10:00Z",
                "diff_value": 1.05,
                "diff_percent": 2.56,
                "currency": _currency(),
            },
        },
    ]
}


DIVERSITY_V2 = {
    "groups": [
        {"ASX": {"percentage": 72.73, "value": 16000.0}},
        {"NASDAQ": {"percentage": 27.27, "value": 6000.0}},
    ],
    "percentage": 100.0,
    "value": PORTFOLIO_VALUE,
    "date": TODAY,
}


CAPITAL_GAINS = {
    "short_term_gains": 120.0,
    "long_term_gains": 480.0,
    "losses": -200.0,
    "short_term_losses": -50.0,
    "long_term_losses": -150.0,
    "total_discounted_capital_gain_distributions": 12.0,
    "total_non_discounted_capital_gain_distributions": 8.0,
    "cgt_concession_rate": 0.5,
    "cgt_concession_amount": 240.0,
    "market_value": PORTFOLIO_VALUE,
    "tax_gain_loss": 160.0,
    "claimable_loss": 0.0,
    "short_term_parcels": [],
    "long_term_parcels": [],
    "loss_parcels": [],
    "start_date": "2026-01-01",
    "end_date": "2026-12-31",
    "portfolio_id": PORTFOLIO_ID,
}


UNREALISED_CGT = {
    "unrealised_short_term_gains": 300.0,
    "unrealised_long_term_gains": 1000.0,
    "unrealised_losses": -500.0,
    "cgt_concession_rate": 0.5,
    "unrealised_cgt_concession_amount": 500.0,
    "market_value": PORTFOLIO_VALUE,
    "unrealised_tax_gain_loss": 300.0,
    "short_term_parcels": [],
    "long_term_parcels": [],
    "losses": [],
    "balance_date": TODAY,
    "portfolio_id": PORTFOLIO_ID,
}


# A 30-day daily value series with a clear peak-then-trough, so drawdown and
# trend maths have an unambiguous answer:
#   start 20000 -> peak 24000 (day 10) -> trough 18000 (day 20) -> 22000 today
#   maximum drawdown = (24000 - 18000) / 24000 = 25.0%
VALUE_SERIES = {
    "chart": {
        "data": [
            {"timestamp": "2026-07-28", "value": 20000.0},
            {"timestamp": "2026-08-01", "value": 21000.0},
            {"timestamp": "2026-08-06", "value": 24000.0},
            {"timestamp": "2026-08-11", "value": 21000.0},
            {"timestamp": "2026-08-16", "value": 18000.0},
            {"timestamp": "2026-08-20", "value": 19500.0},
            {"timestamp": "2026-08-27", "value": 22000.0},
        ]
    }
}


def coordinator_data() -> dict[str, Any]:
    """A full ``coordinator.data`` snapshot, as the platforms consume it."""
    report = performance_report()
    return {
        "report": report,
        "portfolio_detail": PORTFOLIO_DETAIL,
        "portfolios": PORTFOLIOS,
        "holdings": {"holdings": OPEN_HOLDINGS, "value": report["value"]},
        "payouts": {"payouts": PAYOUTS},
        "upcoming_payouts": {"payouts": UPCOMING_PAYOUTS},
        "trades": {"trades": TRADES},
        "cash_accounts_v2": {"cash_accounts": CASH_ACCOUNTS_V2},
        "cash_account_transactions": {"cash_account_transactions": CASH_TRANSACTIONS},
        "user_instruments": USER_INSTRUMENTS,
        "user_setting": USER_SETTING,
        "my_user": MY_USER,
        "watchlist": WATCHLIST,
        "capital_gains": CAPITAL_GAINS,
        "unrealised_cgt": UNREALISED_CGT,
        "benchmark": BENCHMARK,
        "value_series": VALUE_SERIES,
        "one-day": period_report(
            start_date=TODAY, end_date=TODAY, capital_gain=100.0, payout_gain=0.0
        ),
        "one-week": period_report(start_date="2026-08-24", end_date=TODAY, capital_gain=250.0),
        "one-month": period_report(
            start_date="2026-07-28",
            end_date=TODAY,
            capital_gain=-300.0,
            payout_gain=50.0,
        ),
        "ytd": period_report(
            start_date="2026-01-01",
            end_date=TODAY,
            capital_gain=1500.0,
            payout_gain=375.0,
            currency_gain=-200.0,
        ),
        "financial-year": period_report(
            start_date="2026-01-01",
            end_date=TODAY,
            capital_gain=1500.0,
            payout_gain=375.0,
            currency_gain=-200.0,
        ),
    }
