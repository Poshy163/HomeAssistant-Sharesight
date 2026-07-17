"""Buttons for the Sharesight integration.

Two buttons per portfolio:
- Refresh — forces an immediate (debounced) coordinator poll.
- Rebuild Value History — re-runs the long-term-statistics backfill that
  normally only runs once at startup, so users can recover the value history
  on demand (e.g. after the value-data endpoint becomes reachable).
"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import APP_VERSION, DOMAIN
from .coordinator import SharesightCoordinator
from .data import SharesightConfigEntry
from .statistics_import import async_backfill_value_statistics

_LOGGER: logging.Logger = logging.getLogger(__package__)

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
    async_add_entities(
        [
            SharesightRefreshButton(coordinator, portfolio_id, edge),
            SharesightRebuildValueHistoryButton(coordinator, portfolio_id, edge),
        ]
    )


class SharesightRefreshButton(CoordinatorEntity, ButtonEntity):
    """Force an immediate coordinator refresh."""

    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator, portfolio_id, edge):
        super().__init__(coordinator)
        self._portfolio_id = portfolio_id
        edge_name = " Edge " if edge else " "
        edge_url = "edge-" if edge else ""
        self._attr_name = f"Sharesight{edge_name}Refresh"
        self._attr_unique_id = f"{portfolio_id}_refresh_{APP_VERSION}"
        self.entity_id = f"button.sharesight_refresh_{portfolio_id}"
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, f"{portfolio_id}_portfolio")},
            configuration_url=(
                f"https://{edge_url}portfolio.sharesight.com/portfolios/{portfolio_id}"
            ),
            model=f"Sharesight{edge_name}API - Portfolio",
            name=f"Sharesight{edge_name}Portfolio {portfolio_id}",
        )

    async def async_press(self) -> None:
        """Trigger a debounced on-demand poll."""
        await self.coordinator.async_request_refresh()


class SharesightRebuildValueHistoryButton(CoordinatorEntity, ButtonEntity):
    """Re-run the portfolio-value long-term-statistics backfill."""

    _attr_icon = "mdi:database-refresh"

    def __init__(self, coordinator, portfolio_id, edge):
        super().__init__(coordinator)
        self._portfolio_id = portfolio_id
        edge_name = " Edge " if edge else " "
        edge_url = "edge-" if edge else ""
        self._attr_name = f"Sharesight{edge_name}Rebuild Value History"
        self._attr_unique_id = f"{portfolio_id}_rebuild_lts_{APP_VERSION}"
        self.entity_id = f"button.sharesight_rebuild_value_history_{portfolio_id}"
        # Attach to the Account device (same identifiers as the account
        # sensors / subscription binary sensor).
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, f"{portfolio_id}_account")},
            configuration_url=(
                f"https://{edge_url}portfolio.sharesight.com/portfolios/{portfolio_id}"
            ),
            model=f"Sharesight{edge_name}API - Account",
            name=f"Sharesight{edge_name}Account",
        )

    async def async_press(self) -> None:
        """Schedule the idempotent value-history backfill in the background."""
        # Resolve the portfolio currency defensively; the backfill upserts by
        # (statistic_id, start) so re-running it is always safe.
        currency = "USD"
        portfolios = (self.coordinator.data or {}).get("portfolios", [])
        if portfolios and isinstance(portfolios[0], dict):
            currency = portfolios[0].get("currency_code", "USD")
        # Entry-scoped so a reload/unload cancels an in-flight rebuild (matching
        # the startup backfill in __init__.async_setup_entry).
        self.coordinator.entry.async_create_background_task(
            self.hass,
            async_backfill_value_statistics(
                self.hass, self.coordinator, self._portfolio_id, currency
            ),
            "sharesight_lts_rebuild",
        )
