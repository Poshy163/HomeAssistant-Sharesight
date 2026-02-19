import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
from SharesightAPI.SharesightAPI import SharesightAPI

from .const import (
    CONF_PORTFOLIO_ID,
    CONF_USE_EDGE,
    DOMAIN,
    API_URL_BASE,
    EDGE_API_URL_BASE,
    EDGE_TOKEN_URL,
    PLATFORMS,
    TOKEN_URL,
)
from .coordinator import SharesightCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
        hass, entry
    )
    oauth_session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)
    await oauth_session.async_ensure_token_valid()

    portfolio_id = entry.data[CONF_PORTFOLIO_ID]
    use_edge = entry.data.get(CONF_USE_EDGE, False)

    api_url = EDGE_API_URL_BASE if use_edge else API_URL_BASE
    token_url = EDGE_TOKEN_URL if use_edge else TOKEN_URL

    client = SharesightAPI(
        client_id="",
        client_secret="",
        authorization_code="",
        redirect_uri="",
        token_url=token_url,
        api_url_base=api_url,
        use_token_file=False,
    )

    local_coordinator = SharesightCoordinator(
        hass, portfolio_id, client=client, oauth_session=oauth_session
    )
    await local_coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": local_coordinator,
        "portfolio_id": portfolio_id,
        "edge": use_edge,
        "sharesight_client": client,
    }

    entry.async_on_unload(entry.add_update_listener(update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        _LOGGER.info(f"Unloaded platforms for entry {entry.entry_id}")
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    _LOGGER.info(f"Removing Sharesight integration: {entry.entry_id}")
    domain_data = hass.data.get(DOMAIN, {})
    if entry.entry_id not in domain_data:
        _LOGGER.warning(f"Entry {entry.entry_id} not found in {DOMAIN} during removal")
        return

    domain_data.pop(entry.entry_id, None)
    if not domain_data:
        hass.data.pop(DOMAIN, None)
    _LOGGER.info(f"Successfully removed Sharesight integration: {entry.entry_id}")


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
