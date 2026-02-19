import logging

import voluptuous as vol
from homeassistant.helpers import config_entry_oauth2_flow
from SharesightAPI.SharesightAPI import SharesightAPI

from .const import (
    API_URL_BASE,
    CONF_PORTFOLIO_ID,
    CONF_USE_EDGE,
    DOMAIN,
    EDGE_API_URL_BASE,
    EDGE_TOKEN_URL,
    TOKEN_URL,
)

_LOGGER = logging.getLogger(__name__)


class SharesightConfigFlow(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    VERSION = 2
    DOMAIN = DOMAIN

    def __init__(self):
        super().__init__()
        self._oauth_data: dict = {}
        self._portfolios: dict[str, str] = {}  # {id_str: "name (id)"}

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    async def _fetch_portfolios(self, use_edge: bool = False) -> dict[str, str]:
        """Fetch portfolio list from Sharesight API using the current OAuth token."""
        access_token = self._oauth_data.get("token", {}).get("access_token")
        if not access_token:
            _LOGGER.error("No access token available to fetch portfolios")
            return {}

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

        try:
            response = await client.get_api_request(
                ["v3", "portfolios", None, False], access_token
            )
            _LOGGER.debug("Portfolios response: %s", response)

            portfolios_list = response.get("portfolios", [])
            result = {}
            for p in portfolios_list:
                pid = str(p.get("id", ""))
                pname = p.get("name", f"Portfolio {pid}")
                if pid:
                    result[pid] = f"{pname} ({pid})"
            return result
        except Exception as err:
            _LOGGER.warning("Failed to fetch portfolio list: %s", err)
            return {}

    async def async_step_user(self, user_input=None):
        # Skip domain-level unique_id (which would block multiple portfolios)
        # and go straight to the credential picker.
        return await self.async_step_pick_implementation()

    async def async_oauth_create_entry(self, data: dict):
        # OAuth succeeded — stash the token data and collect portfolio details.
        self._oauth_data = data
        self._portfolios = await self._fetch_portfolios()
        return await self.async_step_portfolio()

    async def async_step_portfolio(self, user_input=None):
        errors = {}

        if user_input is not None:
            portfolio_id = str(user_input[CONF_PORTFOLIO_ID])
            use_edge = user_input.get(CONF_USE_EDGE, False)

            await self.async_set_unique_id(portfolio_id)
            self._abort_if_unique_id_configured()

            # Build a friendly title
            portfolio_name = self._portfolios.get(portfolio_id, portfolio_id)
            edge_label = " (Edge)" if use_edge else ""
            return self.async_create_entry(
                title=f"Sharesight: {portfolio_name}{edge_label}",
                data={
                    **self._oauth_data,
                    CONF_PORTFOLIO_ID: portfolio_id,
                    CONF_USE_EDGE: use_edge,
                },
            )

        # Build the form — use a dropdown if we fetched portfolios, otherwise fall back to text input
        if self._portfolios:
            portfolio_selector = vol.In(self._portfolios)
        else:
            _LOGGER.warning(
                "Could not fetch portfolio list from Sharesight API, falling back to manual entry"
            )
            portfolio_selector = str

        return self.async_show_form(
            step_id="portfolio",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PORTFOLIO_ID): portfolio_selector,
                    vol.Required(CONF_USE_EDGE, default=False): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, _entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema({}),
            )
        return await self.async_step_user()
