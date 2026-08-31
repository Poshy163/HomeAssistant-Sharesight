"""Diagnostics: what it reports, and what it must never reveal."""

from __future__ import annotations

import json

from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sharesight.diagnostics import (
    async_get_config_entry_diagnostics,
)

pytestmark = pytest.mark.usefixtures("mock_api", "credential")


async def _diagnostics(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return await async_get_config_entry_diagnostics(hass, entry)


async def test_diagnostics_report_what_a_maintainer_needs(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    result = await _diagnostics(hass, mock_config_entry)

    assert result["coordinator"]["loaded"] is True
    assert result["coordinator"]["portfolio_currency"] == "AUD"
    assert result["coordinator"]["data_fetched_at"] is not None
    assert result["coordinator"]["degraded"] is False
    assert result["coordinator"]["financial_year"]["start"].endswith("-01-01")

    assert result["api"]["base_url"].startswith("https://api.sharesight.com")
    assert result["api"]["lockout_active"] is False
    assert result["api"]["documented_requests_per_minute"] == 360
    assert result["api"]["holding_limit"] is None

    assert "parked_count" in result["endpoints"]
    assert "carried_forward" in result["endpoints"]

    assert result["auth"]["has_access_token"] is True
    assert result["auth"]["has_refresh_token"] is True
    assert result["auth"]["account_type"] == "standard"

    assert result["entities"]["total"] > 0
    assert "sensor" in result["entities"]["by_platform"]

    assert result["options_effective"]["scan_interval"] == 300


async def test_diagnostics_summarise_rather_than_dump(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The old version reproduced a 1.4 MB payload verbatim."""
    result = await _diagnostics(hass, mock_config_entry)

    trades = result["data_summary"]["trades"]
    assert trades["lists"]["trades"]["count"] == len(
        mock_config_entry.runtime_data.coordinator.data["trades"]["trades"]
    )
    # The rows themselves are described, not reproduced.
    assert "transaction_type" in trades["lists"]["trades"]["item_keys"]
    assert "data_summary" in result
    assert "data" not in result

    serialised = json.dumps(result)
    assert len(serialised) < 200_000


async def test_diagnostics_never_leak_the_token(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    result = await _diagnostics(hass, mock_config_entry)
    serialised = json.dumps(result)
    assert "test-access-token" not in serialised
    assert "test-refresh-token" not in serialised
    assert result["entry"]["data"]["token"] == REDACTED


async def test_diagnostics_never_leak_the_account_holders_name(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """It reaches the payload through four separate routes."""
    result = await _diagnostics(hass, mock_config_entry)
    serialised = json.dumps(result)

    assert "Test User" not in serialised
    assert "test@example.invalid" not in serialised
    # The entry title embeds the portfolio name, which Sharesight defaults to
    # "<Full Name>'s Portfolio".
    assert result["entry"]["title"] == "Sharesight portfolio [redacted]"
    assert "1020131" not in serialised
    assert "data" not in result


async def test_diagnostics_expose_shapes_without_dynamic_mapping_keys(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Symbols/account ids used as mapping keys must not leak into a dump."""
    result = await _diagnostics(hass, mock_config_entry)
    summary = result["data_summary"]
    assert summary["portfolio_analytics"]["type"] == "dict"
    assert "keys" not in summary["portfolio_analytics"]
    serialised = json.dumps(result)
    assert '"AAA"' not in serialised
    assert "22000.0" not in serialised
    assert "AUD" in serialised  # portfolio currency is intentional metadata
