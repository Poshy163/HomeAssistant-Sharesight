"""Binary sensors for the Sharesight integration.

Exposes a subscription-health flag plus a handful of portfolio-level status
flags per portfolio (unconfirmed transactions, dividend imminent, API degraded)
— all derived from already-fetched coordinator data, so they add no API cost and
give users something concrete to automate against.
"""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import APP_VERSION
from .coordinator import SharesightCoordinator
from .data import SharesightConfigEntry
from .entity import SharesightBaseEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SharesightConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime_data = entry.runtime_data
    coordinator: SharesightCoordinator = runtime_data.coordinator
    portfolio_id = runtime_data.portfolio_id
    edge = runtime_data.edge
    entities = [
        SharesightSubscriptionBinarySensor(coordinator, portfolio_id, edge),
        SharesightUnconfirmedTransactions(coordinator, portfolio_id, edge),
        SharesightDividendImminent(coordinator, portfolio_id, edge),
        SharesightApiDegraded(coordinator, portfolio_id, edge),
        SharesightDataStale(coordinator, portfolio_id, edge),
    ]
    async_add_entities(entities)


class SharesightDataStale(SharesightBaseEntity, BinarySensorEntity):
    """On while the coordinator is serving carried-over rather than fresh data.

    The coordinator deliberately keeps publishing the previous payload through
    a transient failure so sensors hold their readings instead of flapping.
    That is the right behaviour, but without a signal it is indistinguishable
    from a healthy poll — the numbers on the dashboard simply stop moving.
    This is that signal, and its ``fetched_at`` / ``age_seconds`` attributes
    say exactly how old the figures are.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "data_stale"

    def __init__(self, coordinator, portfolio_id, edge):
        super().__init__(coordinator, portfolio_id, edge)
        self._attr_unique_id = f"{self._resource_id}_data_stale_{APP_VERSION}"
        self.entity_id = f"binary_sensor.sharesight_data_stale_{self._resource_id}"
        self._attr_device_info = self._service_device_info(
            "portfolio", f"Portfolio {portfolio_id}", "Portfolio"
        )

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.is_degraded

    @property
    def extra_state_attributes(self) -> dict:
        age = self.coordinator.data_age
        fetched_at = self.coordinator.data_timestamp
        return {
            "fetched_at": fetched_at.isoformat() if fetched_at else None,
            "age_seconds": int(age.total_seconds()) if age else None,
            "reason": self.coordinator.degraded_reason,
        }

    @property
    def available(self) -> bool:
        # Diagnostic — reporting on the integration itself, so it must stay
        # available precisely when everything else is not.
        return True


class SharesightSubscriptionBinarySensor(SharesightBaseEntity, BinarySensorEntity):
    """On when the Sharesight subscription is expired or cancelled."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "subscription_problem"

    def __init__(self, coordinator, portfolio_id, edge):
        super().__init__(coordinator, portfolio_id, edge)
        self._attr_unique_id = f"{self._resource_id}_subscription_problem_{APP_VERSION}"
        self.entity_id = f"binary_sensor.sharesight_subscription_problem_{self._resource_id}"
        self._attr_device_info = self._service_device_info("account", "Account", "Account")

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
        return super().available and self._user() is not None


class SharesightUnconfirmedTransactions(SharesightBaseEntity, BinarySensorEntity):
    """On when the portfolio has unconfirmed transactions awaiting review."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "has_unconfirmed_transactions"

    def __init__(self, coordinator, portfolio_id, edge):
        super().__init__(coordinator, portfolio_id, edge)
        self._attr_unique_id = f"{self._resource_id}_unconfirmed_transactions_problem_{APP_VERSION}"
        self.entity_id = f"binary_sensor.sharesight_unconfirmed_transactions_{self._resource_id}"
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
        return super().available and bool(self.coordinator.data)


class SharesightDividendImminent(SharesightBaseEntity, BinarySensorEntity):
    """On when a held instrument goes ex-dividend within the next few days."""

    _attr_translation_key = "dividend_imminent"

    # Ex-date lead time (days) that counts as "imminent".
    _IMMINENT_DAYS = 3

    def __init__(self, coordinator, portfolio_id, edge):
        super().__init__(coordinator, portfolio_id, edge)
        self._attr_unique_id = f"{self._resource_id}_dividend_imminent_{APP_VERSION}"
        self.entity_id = f"binary_sensor.sharesight_dividend_imminent_{self._resource_id}"
        self._attr_device_info = self._service_device_info("income", "Income", "Income")

    def _income_report(self) -> dict | None:
        income = (self.coordinator.data or {}).get("income_report")
        return income if isinstance(income, dict) else None

    @property
    def is_on(self) -> bool | None:
        income = self._income_report()
        if income is None:
            return None
        payouts = (income.get("payouts") or []) + (income.get("upcoming_payouts") or [])
        today = self.coordinator.current_date
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
        income = self._income_report()
        return (
            super().available
            and income is not None
            and bool(income.get("upcoming_payouts_available"))
        )


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
        self._attr_unique_id = f"{self._resource_id}_api_degraded_{APP_VERSION}"
        self.entity_id = f"binary_sensor.sharesight_api_degraded_{self._resource_id}"
        self._attr_device_info = self._service_device_info(
            "portfolio", f"Portfolio {portfolio_id}", "Portfolio"
        )

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.lockout_seconds_remaining > 0

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "seconds_remaining": self.coordinator.lockout_seconds_remaining,
            "reason": self.coordinator._lockout_reason,
        }

    @property
    def available(self) -> bool:
        # Diagnostic — always report, including during API failures.
        return True
