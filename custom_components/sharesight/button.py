"""Buttons for the Sharesight integration.

Two buttons per portfolio:
- Refresh — forces an immediate (debounced) coordinator poll.
- Rebuild Value History — re-runs the long-term-statistics backfill that
  normally only runs once at startup, so users can recover the value history
  on demand (e.g. after the value-data endpoint becomes reachable).
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import APP_VERSION
from .coordinator import SharesightCoordinator
from .data import SharesightConfigEntry
from .entity import SharesightBaseEntity
from .statistics_import import async_backfill_value_statistics

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


class SharesightRefreshButton(SharesightBaseEntity, ButtonEntity):
    """Force an immediate coordinator refresh."""

    _attr_translation_key = "refresh"

    def __init__(self, coordinator, portfolio_id, edge):
        super().__init__(coordinator, portfolio_id, edge)
        self._attr_unique_id = f"{self._resource_id}_refresh_{APP_VERSION}"
        self.entity_id = f"button.sharesight_refresh_{self._resource_id}"
        self._attr_device_info = self._service_device_info(
            "portfolio", f"Portfolio {portfolio_id}", "Portfolio"
        )

    async def async_press(self) -> None:
        """Trigger a debounced on-demand poll."""
        await self.coordinator.async_request_refresh()


class SharesightRebuildValueHistoryButton(SharesightBaseEntity, ButtonEntity):
    """Re-run the portfolio-value long-term-statistics backfill."""

    _attr_translation_key = "rebuild_value_history"

    def __init__(self, coordinator, portfolio_id, edge):
        super().__init__(coordinator, portfolio_id, edge)
        self._attr_unique_id = f"{self._resource_id}_rebuild_lts_{APP_VERSION}"
        self.entity_id = f"button.sharesight_rebuild_value_history_{self._resource_id}"
        # Attach to the Account device (same identifiers as the account
        # sensors / subscription binary sensor).
        self._attr_device_info = self._service_device_info("account", "Account", "Account")

    async def async_press(self) -> None:
        """Schedule the idempotent value-history backfill in the background.

        Entry-scoped so a reload/unload cancels an in-flight rebuild (matching
        the startup backfill in ``__init__.async_setup_entry``).  The backfill
        upserts by ``(statistic_id, start)``, so re-running it is always safe.
        """
        self.coordinator.entry.async_create_background_task(
            self.hass,
            async_backfill_value_statistics(self.hass, self.coordinator.entry, self.coordinator),
            "sharesight_lts_rebuild",
        )
