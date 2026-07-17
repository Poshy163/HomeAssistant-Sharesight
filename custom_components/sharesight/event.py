"""Activity event entity for the Sharesight integration.

Exposes one event entity per portfolio that fires HA events whenever the
coordinator's activity diff (Feature 2) detects new trades, dividends,
holdings opened/closed, cash transactions, published instrument news, or a
daily-close rollover.  The detection itself is a zero-cost in-coordinator diff
(the news batch is queued by the same diff once its optional endpoint comes
online); this entity only replays the queued events onto HA's event bus so
users can automate against them and device triggers can subscribe.
"""
from __future__ import annotations

import logging

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import APP_VERSION, DOMAIN
from .coordinator import SharesightCoordinator
from .data import SharesightConfigEntry

_LOGGER: logging.Logger = logging.getLogger(__package__)

PARALLEL_UPDATES = 0

# Event types kept byte-identical to the keys the coordinator stages in
# combined_dict["activity_events"] — any mismatch would silently drop events.
# "news_published" (W2) rides the same activity_events dict/seq: the coordinator
# only stages it on polls where the optional instrument_news endpoint returned
# data, so it fires like any other family once that endpoint comes online.
EVENT_TYPES = [
    "dividend_announced",
    "dividend_paid",
    "trade_confirmed",
    "holding_opened",
    "holding_closed",
    "cash_transaction",
    "daily_close",
    "news_published",
]


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
        [SharesightActivityEvent(coordinator, portfolio_id, edge)]
    )


class SharesightActivityEvent(CoordinatorEntity, EventEntity):
    """Fires portfolio activity events staged by the coordinator diff."""

    _attr_icon = "mdi:bell-ring"
    _attr_event_types = EVENT_TYPES

    def __init__(self, coordinator, portfolio_id, edge):
        super().__init__(coordinator)
        self._portfolio_id = portfolio_id
        # Sequence id of the last activity batch fired, so a keep-last-good
        # cached coordinator return (same self.data, re-notified listeners)
        # does not replay a batch that was already emitted.
        self._last_fired_seq: int | None = None
        edge_name = " Edge " if edge else " "
        edge_url = "edge-" if edge else ""
        self._attr_name = f"Sharesight{edge_name}Activity"
        self._attr_unique_id = f"{portfolio_id}_activity_events_{APP_VERSION}"
        self.entity_id = f"event.sharesight_activity_{portfolio_id}"
        # Attach to the Portfolio device (same identifiers as the portfolio
        # sensors in sensor.py).
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, f"{portfolio_id}_portfolio")},
            configuration_url=(
                f"https://{edge_url}portfolio.sharesight.com/portfolios/{portfolio_id}"
            ),
            model=f"Sharesight{edge_name}API - Portfolio",
            name=f"Sharesight{edge_name}Portfolio {portfolio_id}",
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Replay the coordinator's staged activity events onto HA.

        EventEntity fires exactly one event per state write, so each queued
        record is fired sequentially with its own async_write_ha_state().  The
        per-type ``count`` and ``items`` are carried in every event's attrs so
        an automation still sees the whole batch even though HA only surfaces
        the last write as the entity's current state.
        """
        data = self.coordinator.data or {}
        events = data.get("activity_events")
        if not isinstance(events, dict) or not events:
            return

        # Skip cached returns: the coordinator stamps a fresh sequence id on
        # each freshly-diffed batch, so an unchanged seq means this same batch
        # was already fired on an earlier update and must not replay.
        seq = data.get("activity_events_seq")
        if seq is not None and seq == self._last_fired_seq:
            return
        self._last_fired_seq = seq

        for event_type in self._attr_event_types:
            items = events.get(event_type)
            if not isinstance(items, list) or not items:
                continue
            count = len(items)
            for index, attrs in enumerate(items):
                if not isinstance(attrs, dict):
                    continue
                try:
                    self._trigger_event(
                        event_type,
                        {**attrs, "count": count, "index": index, "items": items},
                    )
                    self.async_write_ha_state()
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug(
                        "Failed to fire %s event: %s: %s",
                        event_type,
                        type(err).__name__,
                        err,
                    )
