"""Binary sensors for the Sharesight integration.

Exposes a subscription-health flag plus a handful of portfolio-level status
flags per portfolio (market open, unconfirmed transactions, dividend imminent,
API degraded) — all derived from already-fetched coordinator data, so they add
no API cost and give users something concrete to automate against.
"""
from __future__ import annotations

import logging
import time
from datetime import time as dt_time, timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import analytics
from .const import APP_VERSION
from .coordinator import SharesightCoordinator
from .data import SharesightConfigEntry
from .entity import SharesightBaseEntity

_LOGGER: logging.Logger = logging.getLogger(__package__)

PARALLEL_UPDATES = 0


def _market_is_open(market, now, tz):
    """Best-effort open/closed for a Sharesight markets[] entry.

    ``market`` is a markets[] dict (tz_name + trading_start_time +
    trading_end_time as HH:MM); ``now`` is an aware datetime; ``tz`` is the
    market's already-resolved tzinfo (resolved off the event loop via
    ``async_get_time_zone`` so this stays non-blocking).  Returns True/False,
    or None when the market lacks usable trading-hours metadata so the caller
    can treat it as unknown rather than closed.  Weekends are treated as
    closed; public holidays and half-days are not modelled.
    """
    start_raw = market.get("trading_start_time")
    end_raw = market.get("trading_end_time")
    if tz is None or not (start_raw and end_raw):
        return None
    try:
        start_h, start_m = (int(x) for x in str(start_raw).split(":")[:2])
        end_h, end_m = (int(x) for x in str(end_raw).split(":")[:2])
    except (ValueError, TypeError):
        return None
    local_now = now.astimezone(tz)
    if local_now.weekday() >= 5:
        return False
    return dt_time(start_h, start_m) <= local_now.time() <= dt_time(end_h, end_m)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SharesightConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime_data = entry.runtime_data
    coordinator: SharesightCoordinator = runtime_data.coordinator
    portfolio_id = runtime_data.portfolio_id
    edge = runtime_data.edge
    async_add_entities(
        [
            SharesightSubscriptionBinarySensor(coordinator, portfolio_id, edge),
            SharesightAnyMarketOpen(coordinator, portfolio_id, edge),
            SharesightUnconfirmedTransactions(coordinator, portfolio_id, edge),
            SharesightDividendImminent(coordinator, portfolio_id, edge),
            SharesightApiDegraded(coordinator, portfolio_id, edge),
        ]
    )


class SharesightSubscriptionBinarySensor(SharesightBaseEntity, BinarySensorEntity):
    """On when the Sharesight subscription is expired or cancelled."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "subscription_problem"

    def __init__(self, coordinator, portfolio_id, edge):
        super().__init__(coordinator, portfolio_id, edge)
        self._attr_unique_id = f"{portfolio_id}_subscription_problem_{APP_VERSION}"
        self.entity_id = f"binary_sensor.sharesight_subscription_problem_{portfolio_id}"
        self._attr_device_info = self._service_device_info(
            "account", "Account", "Account"
        )

    def _user(self) -> dict | None:
        my_user = (self.coordinator.data or {}).get("my_user")
        if not isinstance(my_user, dict):
            return None
        user = my_user.get("user")
        return user if isinstance(user, dict) else my_user

    @property
    def is_on(self) -> bool | None:
        """True if the subscription is expired/cancelled; None if unknown."""
        user = self._user()
        if user is None:
            return None
        return bool(user.get("is_expired") or user.get("is_cancelled"))

    @property
    def available(self) -> bool:
        """Available whenever the account endpoint has ever returned data."""
        return self._user() is not None


class SharesightAnyMarketOpen(SharesightBaseEntity, BinarySensorEntity):
    """On when any market the portfolio holds is currently open.

    Only markets the portfolio actually holds are considered (a global
    all-markets view would be open almost around the clock and useless).
    When no held market has usable trading-hours metadata the state is
    unknown (available=False) rather than off.
    """

    _attr_translation_key = "any_market_open"

    def __init__(self, coordinator, portfolio_id, edge):
        super().__init__(coordinator, portfolio_id, edge)
        # tz_name -> resolved tzinfo (or None), populated off the event loop via
        # async_get_time_zone so the synchronous is_on/available reads never
        # trigger a blocking zoneinfo load on the loop.
        self._tz_cache: dict[str, object | None] = {}
        self._attr_unique_id = f"{portfolio_id}_any_market_open_{APP_VERSION}"
        self.entity_id = f"binary_sensor.sharesight_any_market_open_{portfolio_id}"
        self._attr_device_info = self._service_device_info(
            "market_hours", "Market Hours", "Market Hours"
        )

    def _markets(self) -> list:
        markets_data = (self.coordinator.data or {}).get("markets")
        if not isinstance(markets_data, dict):
            return []
        markets = markets_data.get("markets", [])
        return markets if isinstance(markets, list) else []

    async def async_added_to_hass(self) -> None:
        """Resolve current market time zones before the first state read."""
        await super().async_added_to_hass()
        await self._resolve_timezones()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Pre-resolve any newly-seen market time zones, then write state.

        Resolution runs off the event loop; the state write below may render a
        brand-new market as unknown for one cycle until its tz lands in the
        cache, which is preferable to a blocking zoneinfo load in a property.
        """
        if self.hass is not None:
            self.hass.async_create_task(self._resolve_timezones())
        super()._handle_coordinator_update()

    async def _resolve_timezones(self) -> None:
        for market in self._markets():
            if not isinstance(market, dict):
                continue
            tz_name = market.get("tz_name")
            if tz_name and tz_name not in self._tz_cache:
                self._tz_cache[tz_name] = await dt_util.async_get_time_zone(tz_name)

    def _status(self) -> bool | None:
        """True/False when at least one held market's hours are known; else None."""
        data = self.coordinator.data or {}
        markets = self._markets()
        if not markets:
            return None
        holdings = (data.get("holdings") or {}).get("holdings") or []
        held: set[str] = set()
        for holding in holdings:
            if not isinstance(holding, dict):
                continue
            market_code = analytics.holding_market(holding)
            if market_code:
                held.add(str(market_code))
        if not held:
            return None
        now = dt_util.now()
        statuses = []
        for market in markets:
            if not isinstance(market, dict) or str(market.get("code")) not in held:
                continue
            tz = self._tz_cache.get(market.get("tz_name"))
            state = _market_is_open(market, now, tz)
            if state is not None:
                statuses.append(state)
        if not statuses:
            return None
        return any(statuses)

    @property
    def is_on(self) -> bool | None:
        return self._status()

    @property
    def available(self) -> bool:
        return self._status() is not None


class SharesightUnconfirmedTransactions(SharesightBaseEntity, BinarySensorEntity):
    """On when the portfolio has unconfirmed transactions awaiting review."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "has_unconfirmed_transactions"

    def __init__(self, coordinator, portfolio_id, edge):
        super().__init__(coordinator, portfolio_id, edge)
        self._attr_unique_id = f"{portfolio_id}_unconfirmed_transactions_problem_{APP_VERSION}"
        self.entity_id = f"binary_sensor.sharesight_unconfirmed_transactions_{portfolio_id}"
        self._attr_device_info = self._service_device_info(
            "portfolio", f"Portfolio {portfolio_id}", "Portfolio"
        )

    def _count(self) -> int:
        data = self.coordinator.data or {}
        report_holdings = (data.get("report") or {}).get("holdings") or []
        total = 0
        for holding in report_holdings:
            if not isinstance(holding, dict):
                continue
            val = holding.get("number_of_unconfirmed_transactions", 0)
            if val:
                try:
                    total += int(val)
                except (ValueError, TypeError):
                    pass
        return total

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self._count() > 0

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)


class SharesightDividendImminent(SharesightBaseEntity, BinarySensorEntity):
    """On when a held instrument goes ex-dividend within the next few days."""

    _attr_translation_key = "dividend_imminent"

    # Ex-date lead time (days) that counts as "imminent".
    _IMMINENT_DAYS = 3

    def __init__(self, coordinator, portfolio_id, edge):
        super().__init__(coordinator, portfolio_id, edge)
        self._attr_unique_id = f"{portfolio_id}_dividend_imminent_{APP_VERSION}"
        self.entity_id = f"binary_sensor.sharesight_dividend_imminent_{portfolio_id}"
        self._attr_device_info = self._service_device_info(
            "income", "Income", "Income"
        )

    def _income_report(self) -> dict | None:
        income = (self.coordinator.data or {}).get("income_report")
        return income if isinstance(income, dict) else None

    @property
    def is_on(self) -> bool | None:
        income = self._income_report()
        if income is None:
            return None
        payouts = (income.get("payouts") or []) + (income.get("upcoming_payouts") or [])
        today = dt_util.now().date()
        today_iso = today.isoformat()
        horizon = (today + timedelta(days=self._IMMINENT_DAYS)).isoformat()
        for payout in payouts:
            if not isinstance(payout, dict):
                continue
            ex = payout.get("goes_ex_on") or payout.get("ex_date")
            if not ex:
                continue
            if today_iso <= str(ex)[:10] <= horizon:
                return True
        return False

    @property
    def available(self) -> bool:
        return self._income_report() is not None


class SharesightApiDegraded(SharesightBaseEntity, BinarySensorEntity):
    """On while the Sharesight API is rejecting us (global cooldown/lockout).

    Reads the coordinator's ``_lockout_until`` deadline, which is set when the
    API returns a brute-force lockout or a parallel-request rate-limit.  This
    is a diagnostic flag that stays available even during failures.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "api_degraded"

    def __init__(self, coordinator, portfolio_id, edge):
        super().__init__(coordinator, portfolio_id, edge)
        self._attr_unique_id = f"{portfolio_id}_api_degraded_{APP_VERSION}"
        self.entity_id = f"binary_sensor.sharesight_api_degraded_{portfolio_id}"
        self._attr_device_info = self._service_device_info(
            "portfolio", f"Portfolio {portfolio_id}", "Portfolio"
        )

    @property
    def is_on(self) -> bool | None:
        lockout_until = getattr(self.coordinator, "_lockout_until", 0.0) or 0.0
        try:
            return time.monotonic() < float(lockout_until)
        except (TypeError, ValueError):
            return False

    @property
    def available(self) -> bool:
        # Diagnostic — always report, including during API failures.
        return True
