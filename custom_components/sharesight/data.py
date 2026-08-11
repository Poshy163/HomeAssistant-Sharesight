"""Runtime data types for the Sharesight integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import SharesightCoordinator


@dataclass
class SharesightRuntimeData:
    """State for a single configured portfolio, stored on the config entry.

    Mirrors the objects previously kept in
    ``hass.data[DOMAIN][entry.entry_id]``: the ``client`` field is the former
    ``sharesight_client`` key, and ``market_sensors`` / ``cash_sensors`` /
    ``holding_sensors`` are the same mutable lists the platforms append to.
    """

    coordinator: SharesightCoordinator
    client: Any
    portfolio_id: Any
    edge: bool
    market_sensors: list[Any] = field(default_factory=list)
    cash_sensors: list[Any] = field(default_factory=list)
    holding_sensors: list[Any] = field(default_factory=list)
    last_options: dict[str, Any] = field(default_factory=dict)
    # device_id -> consecutive polls its item has been missing from the
    # portfolio, counted only while auto-removal is enabled (see __init__).
    stale_device_strikes: dict[str, int] = field(default_factory=dict)


type SharesightConfigEntry = ConfigEntry[SharesightRuntimeData]
