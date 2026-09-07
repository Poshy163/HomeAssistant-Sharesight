"""Exercise reload and registry continuity in an isolated running HA core."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.sharesight.const import DOMAIN

pytestmark = pytest.mark.usefixtures("mock_api", "credential")


async def test_reload_preserves_entity_ids_and_user_customisation(hass, mock_config_entry):
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.unique_id}_value_v2"
    )
    assert entity_id is not None
    registry.async_update_entity(entity_id, name="My investment value", icon="mdi:chart-line")
    before = {
        row.unique_id: row.entity_id
        for row in er.async_entries_for_config_entry(registry, mock_config_entry.entry_id)
    }

    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert before == {
        row.unique_id: row.entity_id
        for row in er.async_entries_for_config_entry(registry, mock_config_entry.entry_id)
    }
    row = registry.async_get(entity_id)
    assert row.name == "My investment value"
    assert row.icon == "mdi:chart-line"
    assert hass.states.get(entity_id).state == "22000.0"
