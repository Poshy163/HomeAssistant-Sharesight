"""Entry setup, migration and unload against a real Home Assistant core."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sharesight import _LAST_OPTIONS
from custom_components.sharesight.const import (
    ACCOUNT_STANDARD,
    CONF_ACCOUNT_TYPE,
    CONF_PORTFOLIO_ID,
    CONF_USE_EDGE,
    DOMAIN,
)

pytestmark = pytest.mark.usefixtures("mock_api", "credential")


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> bool:
    entry.add_to_hass(hass)
    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return result


async def test_entry_sets_up_and_creates_entities(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    assert await setup_entry(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED

    coordinator = mock_config_entry.runtime_data.coordinator
    assert coordinator.portfolio_currency == "AUD"
    assert coordinator.data["report"]["value"] == 22000.0

    states = hass.states.async_all("sensor")
    assert states, "no sensors were created"
    value = hass.states.get(f"sensor.portfolio_value_{mock_config_entry.unique_id}")
    assert value is not None
    assert float(value.state) == 22000.0
    assert value.attributes["unit_of_measurement"] == "AUD"


async def test_per_holding_entities_can_be_switched_off(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={"enable_holding_entities": False}
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    holding_states = [
        state
        for state in hass.states.async_all("sensor")
        if state.entity_id.startswith("sensor.aaa_")
    ]
    assert holding_states == []


async def test_unload_removes_entities(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    assert await setup_entry(hass, mock_config_entry)
    assert mock_config_entry.entry_id in _LAST_OPTIONS
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    assert mock_config_entry.entry_id not in _LAST_OPTIONS


async def test_migration_from_version_two(hass: HomeAssistant, token) -> None:
    """The edge flag becomes an account type, and the token is preserved."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id="1020131",
        data={
            "auth_implementation": DOMAIN,
            CONF_PORTFOLIO_ID: "1020131",
            CONF_USE_EDGE: True,
            "token": token,
        },
    )
    assert await setup_entry(hass, entry)
    assert entry.version == 3
    assert entry.data[CONF_ACCOUNT_TYPE] == ACCOUNT_STANDARD
    assert CONF_USE_EDGE not in entry.data
    assert entry.data["token"] == token


async def test_migration_from_version_one_asks_for_reauth(
    hass: HomeAssistant,
) -> None:
    """A pre-OAuth entry used to die on a KeyError instead of prompting."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        unique_id="1020131",
        data={
            CONF_PORTFOLIO_ID: "1020131",
            "client_id": "legacy",
            "client_secret": "legacy-secret",
            "authorization_code": "legacy-code",
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 3
    # The stale credentials are gone rather than lingering in .storage.
    assert "client_secret" not in entry.data
    assert entry.data[CONF_PORTFOLIO_ID] == "1020131"
    assert entry.state is ConfigEntryState.SETUP_ERROR

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow for flow in flows if flow["context"]["source"] == "reauth"]


async def test_transient_token_failure_is_retried_not_fatal(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """An unguarded failure here used to park the entry in SETUP_ERROR."""
    mock_config_entry.add_to_hass(hass)
    with patch(
        "homeassistant.helpers.config_entry_oauth2_flow.OAuth2Session.async_ensure_token_valid",
        side_effect=TimeoutError("token endpoint slow"),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_options_change_reloads_the_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    assert await setup_entry(hass, mock_config_entry)
    first = mock_config_entry.runtime_data.coordinator

    hass.config_entries.async_update_entry(mock_config_entry, options={"scan_interval": 900})
    await hass.async_block_till_done()

    second = mock_config_entry.runtime_data.coordinator
    assert second is not first
    assert second.update_interval.total_seconds() == 900


async def test_token_refresh_does_not_reload_the_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """OAuth2Session rewrites entry.data periodically; that must not reload."""
    assert await setup_entry(hass, mock_config_entry)
    first = mock_config_entry.runtime_data.coordinator

    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, "token": {"access_token": "rotated"}},
    )
    await hass.async_block_till_done()

    assert mock_config_entry.runtime_data.coordinator is first
