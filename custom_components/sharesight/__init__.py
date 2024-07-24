import logging

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from SharesightAPI import SharesightAPI
from .const import DOMAIN, PORTFOLIO_ID, TOKEN_URL, REDIRECT_URL, API_URL_BASE, PLATFORMS, set_portfolio_id

from .coordinator import SharesightCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    portfolio_id = entry.data["portfolio_id"]
    client_id = entry.data["client_id"]
    client_secret = entry.data["client_secret"]
    authorization_code = entry.data["authorization_code"]
    token_file = "HA.txt"
    await set_portfolio_id(portfolio_id)

    client = SharesightAPI.SharesightAPI(client_id, client_secret, authorization_code, REDIRECT_URL, TOKEN_URL,
                                         API_URL_BASE, token_file, True)
    await client.get_token_data()

    local_coordinator = SharesightCoordinator(hass, client=client)
    await local_coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = local_coordinator

    entry.async_on_unload(entry.add_update_listener(update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
