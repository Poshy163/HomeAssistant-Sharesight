from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from SharesightAPI import SharesightAPI
from .const import DOMAIN, PORTFOLIO_ID, TOKEN_URL, REDIRECT_URL, API_URL_BASE


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    client_id = entry.data["client_id"]
    client_secret = entry.data["client_secret"]
    authorization_code = entry.data["authorization_code"]
    token_file = "HA.txt"
    const.PORTFOLIO_ID = entry.data["portfolio_id"]

    sharesight = SharesightAPI.SharesightAPI(client_id, client_secret, authorization_code, REDIRECT_URL, TOKEN_URL,
                                             API_URL_BASE, token_file, True)
    await sharesight.get_token_data()
    hass.data[DOMAIN] = sharesight

    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    await hass.config_entries.async_forward_entry_unload(entry, "sensor")
    hass.data.pop(DOMAIN)
    return True
