from homeassistant.components.application_credentials import AuthorizationServer
from homeassistant.core import HomeAssistant

from .const import AUTHORIZATION_URL, TOKEN_URL


async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    return AuthorizationServer(
        authorize_url=AUTHORIZATION_URL,
        token_url=TOKEN_URL,
    )
