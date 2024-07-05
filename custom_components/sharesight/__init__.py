from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from SharesightAPI import SharesightAPI
from .const import DOMAIN, PORTFOLIO_ID, TOKEN_URL, REDIRECT_URL, API_URL_BASE



async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    client_id = entry.data["client_id"]
    client_secret = entry.data["client_secret"]
    authorization_code = entry.data["authorization_code"]
    token_file = "token.txt"
    const.PORTFOLIO_ID = entry.data["portfolio_id"]

    sharesight = SharesightAPI.SharesightAPI(client_id, client_secret, authorization_code, REDIRECT_URL, TOKEN_URL,
                                             API_URL_BASE, token_file, True)

    hass.data[DOMAIN] = sharesight

    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(entry, "sensor")
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    await hass.config_entries.async_forward_entry_unload(entry, "sensor")
    hass.data.pop(DOMAIN)
    return True
