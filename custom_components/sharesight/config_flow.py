import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
import logging
from SharesightAPI import SharesightAPI
from .const import DOMAIN, REDIRECT_URL

_LOGGER = logging.getLogger(__name__)

TOKEN_URL = 'https://api.sharesight.com/oauth2/token'
API_URL_BASE = 'https://api.sharesight.com/api/'
EDGE_TOKEN_URL = 'https://edge-api.sharesight.com/oauth2/token'
EDGE_API_URL_BASE = 'https://edge-api.sharesight.com/api/'


class SharesightConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        data_schema = vol.Schema({
            vol.Required("client_id"): str,
            vol.Required("client_secret"): str,
            vol.Required("portfolio_id"): str,
            vol.Required("authorization_code"): str,
            vol.Required("use_edge_url"): bool,
        })

        if user_input is not None:
            try:
                client_id = user_input.get("client_id")
                client_secret = user_input.get("client_secret")
                authorization_code = user_input.get("authorization_code")
                use_edge = user_input.get("use_edge_url")
                _LOGGER.info(f"CALLED FROM CONFIG_FLOW 2")
                token_file = "HA.txt"

                if not use_edge:
                    _LOGGER.info("USING NORMAL URL'S")
                    client = SharesightAPI.SharesightAPI(client_id, client_secret, authorization_code, REDIRECT_URL,
                                                         TOKEN_URL,
                                                         API_URL_BASE, token_file, True)
                else:
                    _LOGGER.info("USING EDGE URL'S")
                    client = SharesightAPI.SharesightAPI(client_id, client_secret, authorization_code, REDIRECT_URL,
                                                         EDGE_TOKEN_URL,
                                                         EDGE_API_URL_BASE, token_file, True)
                await client.get_token_data()
                if await client.validate_token() is None:
                    errors["base"] = "auth"
                    return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

                return self.async_create_entry(title=f"Sharesight portfolio: {user_input["portfolio_id"]}",
                                               data=user_input)
            except Exception:
                errors["base"] = "other"
                return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)



