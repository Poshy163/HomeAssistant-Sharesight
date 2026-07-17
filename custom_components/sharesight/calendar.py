"""Dividend calendar for the Sharesight integration.

Exposes one calendar entity per portfolio containing every received payout
plus announced-but-unpaid dividends (from the coordinator's forward payouts
window), so upcoming income shows up in HA calendar cards and can trigger
calendar-based automations.  Each payout with a known ex-dividend date also
contributes a distinct "ex-dividend" entry on that date (its own uid/dedupe
key) so users can automate against a holding going ex.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import APP_VERSION, DOMAIN
from .coordinator import SharesightCoordinator

_LOGGER: logging.Logger = logging.getLogger(__package__)

PARALLEL_UPDATES = 0


def _payout_date(payout: dict) -> date | None:
    """Best-effort payment date for a payout entry."""
    raw = payout.get("paid_on") or payout.get("date") or payout.get("ex_date")
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _ex_dividend_date(payout: dict) -> date | None:
    """Ex-dividend date for a payout entry, or None when absent/unparseable.

    The V2 payout carries the ex date as ``goes_ex_on`` (the derived forecast
    shape uses ``ex_date``); either can be an empty string when the company
    event has no ex date, which must be skipped rather than emitted (W5).
    """
    raw = payout.get("goes_ex_on") or payout.get("ex_date")
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _payout_symbol(payout: dict) -> str:
    """Instrument symbol for a payout, mirroring the income sensors."""
    return (
        payout.get("symbol")
        or payout.get("instrument_code")
        or (payout.get("holding", {}) or {}).get("instrument", {}).get("code", "")
        or payout.get("company_name", "")
        or "Dividend"
    )


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: SharesightCoordinator = data["coordinator"]
    portfolio_id = data["portfolio_id"]
    edge = data["edge"]

    portfolios = coordinator.data.get("portfolios", [])
    currency = "USD"
    if portfolios and isinstance(portfolios[0], dict):
        currency = portfolios[0].get("currency_code", "USD")

    async_add_entities(
        [SharesightDividendCalendar(coordinator, portfolio_id, edge, currency)]
    )


class SharesightDividendCalendar(CoordinatorEntity, CalendarEntity):
    """Calendar of received and announced dividends for the portfolio."""

    _attr_icon = "mdi:hand-coin"

    def __init__(self, coordinator, portfolio_id, edge, currency):
        super().__init__(coordinator)
        self._portfolio_id = portfolio_id
        self._currency = currency
        edge_name = " Edge " if edge else " "
        edge_url = "edge-" if edge else ""
        self._attr_name = f"Sharesight{edge_name}Dividends"
        self._attr_unique_id = f"{portfolio_id}_dividend_calendar_{APP_VERSION}"
        self.entity_id = f"calendar.sharesight_dividends_{portfolio_id}"
        # Attach to the existing Income device (same identifiers as the
        # income sensors in sensor.py).
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, f"{portfolio_id}_income")},
            configuration_url=(
                f"https://{edge_url}portfolio.sharesight.com/portfolios/{portfolio_id}"
            ),
            model=f"Sharesight{edge_name}API - Income",
            name=f"Sharesight{edge_name}Income",
        )

    def _build_events(self) -> list[CalendarEvent]:
        income = (self.coordinator.data or {}).get("income_report", {})
        if not isinstance(income, dict):
            return []
        payouts = (income.get("payouts", []) or []) + (
            income.get("upcoming_payouts", []) or []
        )

        events: list[CalendarEvent] = []
        seen: set[tuple] = set()
        for payout in payouts:
            if not isinstance(payout, dict):
                continue
            symbol = _payout_symbol(payout)
            try:
                amount = float(payout.get("amount", 0) or 0)
            except (ValueError, TypeError):
                amount = 0.0

            # Payment-date event: the received/announced payout on its pay date.
            day = _payout_date(payout)
            if day is not None:
                # A payout paid today can appear in both the historic and the
                # forward window — emit it once.
                dedupe_key = (payout.get("id"), day, symbol, amount)
                if dedupe_key not in seen:
                    seen.add(dedupe_key)

                    summary = f"{symbol} dividend"
                    if amount:
                        summary += f" {amount:.2f} {self._currency}"

                    description_parts = [payout.get("company_name") or symbol]
                    state = payout.get("state") or payout.get("status")
                    if state:
                        description_parts.append(f"State: {state}")
                    goes_ex = payout.get("goes_ex_on") or payout.get("ex_date")
                    if goes_ex:
                        description_parts.append(f"Ex-dividend: {str(goes_ex)[:10]}")

                    events.append(
                        CalendarEvent(
                            start=day,
                            end=day + timedelta(days=1),
                            summary=summary,
                            description="\n".join(
                                str(p) for p in description_parts if p
                            ),
                        )
                    )

            # Ex-dividend event (W5): a distinct calendar entry on the ex date,
            # so users can automate against "goes ex tomorrow".  It carries its
            # OWN dedupe key (prefixed so it never clashes with the payment
            # event's key above, even when both fall on the same day) and a
            # stable, distinct uid.  Payouts with no ex date are skipped.
            ex_day = _ex_dividend_date(payout)
            if ex_day is not None:
                ex_dedupe = ("ex", payout.get("id"), ex_day, symbol, amount)
                if ex_dedupe not in seen:
                    seen.add(ex_dedupe)

                    ex_summary = f"{symbol} ex-dividend"
                    if amount:
                        ex_summary += f" {amount:.2f} {self._currency}"

                    ex_parts = [
                        payout.get("company_name") or symbol,
                        "Ex-dividend date",
                    ]
                    pay_on = payout.get("paid_on") or payout.get("date")
                    if pay_on:
                        ex_parts.append(f"Pays: {str(pay_on)[:10]}")
                    ex_state = payout.get("state") or payout.get("status")
                    if ex_state:
                        ex_parts.append(f"State: {ex_state}")

                    ident = payout.get("id")
                    if ident is None:
                        ident = symbol or "unknown"
                    uid = f"{self._portfolio_id}_exdiv_{ident}_{ex_day.isoformat()}"

                    events.append(
                        CalendarEvent(
                            start=ex_day,
                            end=ex_day + timedelta(days=1),
                            summary=ex_summary,
                            description="\n".join(str(p) for p in ex_parts if p),
                            uid=uid,
                        )
                    )

        events.sort(key=lambda ev: ev.start)
        return events

    @property
    def event(self) -> CalendarEvent | None:
        """Today's or the next upcoming dividend."""
        today = dt_util.now().date()
        for ev in self._build_events():
            if ev.end > today:
                return ev
        return None

    async def async_get_events(self, hass, start_date, end_date) -> list[CalendarEvent]:
        """Return all dividend events overlapping the requested window."""
        window_start = (
            start_date.date() if isinstance(start_date, datetime) else start_date
        )
        window_end = end_date.date() if isinstance(end_date, datetime) else end_date
        return [
            ev
            for ev in self._build_events()
            if ev.start < window_end and ev.end > window_start
        ]
