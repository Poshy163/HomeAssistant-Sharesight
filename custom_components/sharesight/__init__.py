import logging

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from SharesightAPI import SharesightAPI
from .const import DOMAIN, REDIRECT_URL, PLATFORMS

from .coordinator import SharesightCoordinator

_LOGGER = logging.getLogger(__name__)

TOKEN_URL = 'https://api.sharesight.com/oauth2/token'
API_URL_BASE = 'https://api.sharesight.com/api/'
EDGE_TOKEN_URL = 'https://edge-api.sharesight.com/oauth2/token'
EDGE_API_URL_BASE = 'https://edge-api.sharesight.com/api/'


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    portfolio_id = entry.data["portfolio_id"]
    client_id = entry.data["client_id"]
    client_secret = entry.data["client_secret"]
    authorization_code = entry.data["authorization_code"]
    use_edge = entry.data["use_edge_url"]
    _LOGGER.info(f"USING {use_edge}")
    token_file = "HA.txt"

    if not use_edge:
        _LOGGER.info("USING NORMAL URL'S")
        client = SharesightAPI.SharesightAPI(client_id, client_secret, authorization_code, REDIRECT_URL, TOKEN_URL,
                                             API_URL_BASE, token_file, True)
    else:
        _LOGGER.info("USING EDGE URL'S")
        client = SharesightAPI.SharesightAPI(client_id, client_secret, authorization_code, REDIRECT_URL, EDGE_TOKEN_URL,
                                             EDGE_API_URL_BASE, token_file, True)
    await client.get_token_data()

    local_coordinator = SharesightCoordinator(hass, portfolio_id, client=client)
    await local_coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = local_coordinator
    hass.data.setdefault(DOMAIN, {})["portfolio_id"] = portfolio_id
    hass.data.setdefault(DOMAIN, {})["sharesight_client"] = client
    entry.async_on_unload(entry.add_update_listener(update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    _LOGGER.info(f"Removing Sharesight integration: {entry.entry_id}")
    if entry.entry_id in hass.data[DOMAIN]:
        hass.data[DOMAIN].pop(entry.entry_id)
    sharesight = hass.data[DOMAIN]["sharesight_client"]
    await sharesight.delete_token()
    return


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
