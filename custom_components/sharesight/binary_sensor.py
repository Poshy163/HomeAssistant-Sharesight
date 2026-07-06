"""Binary sensors for the Sharesight integration.

Currently exposes a single subscription-health flag per portfolio: if the
Sharesight subscription lapses, the polled data silently goes stale, so this
gives users something concrete to alert on.
"""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import APP_VERSION, DOMAIN
from .coordinator import SharesightCoordinator

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: SharesightCoordinator = data["coordinator"]
    portfolio_id = data["portfolio_id"]
    edge = data["edge"]
    async_add_entities(
        [SharesightSubscriptionBinarySensor(coordinator, portfolio_id, edge)]
    )


class SharesightSubscriptionBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """On when the Sharesight subscription is expired or cancelled."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:shield-alert"

    def __init__(self, coordinator, portfolio_id, edge):
        super().__init__(coordinator)
        self._portfolio_id = portfolio_id
        edge_name = " Edge " if edge else " "
        edge_url = "edge-" if edge else ""
        self._attr_name = f"Sharesight{edge_name}Subscription Problem"
        self._attr_unique_id = f"{portfolio_id}_subscription_problem_{APP_VERSION}"
        self.entity_id = f"binary_sensor.sharesight_subscription_problem_{portfolio_id}"
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, f"{portfolio_id}_account")},
            configuration_url=(
                f"https://{edge_url}portfolio.sharesight.com/portfolios/{portfolio_id}"
            ),
            model=f"Sharesight{edge_name}API - Account",
            name=f"Sharesight{edge_name}Account",
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
