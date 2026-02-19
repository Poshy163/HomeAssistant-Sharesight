import logging

import voluptuous as vol
from homeassistant.helpers import config_entry_oauth2_flow

from .const import CONF_PORTFOLIO_ID, CONF_USE_EDGE, DOMAIN

_LOGGER = logging.getLogger(__name__)


class SharesightConfigFlow(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    VERSION = 2
    DOMAIN = DOMAIN

    def __init__(self):
        super().__init__()
        self._oauth_data: dict = {}

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    async def async_step_user(self, user_input=None):
        # Skip domain-level unique_id (which would block multiple portfolios)
        # and go straight to the credential picker.
        return await self.async_step_pick_implementation()

    async def async_oauth_create_entry(self, data: dict):
        # OAuth succeeded — stash the token data and collect portfolio details.
        self._oauth_data = data
        return await self.async_step_portfolio()

    async def async_step_portfolio(self, user_input=None):
        if user_input is not None:
            portfolio_id = user_input[CONF_PORTFOLIO_ID]
            use_edge = user_input[CONF_USE_EDGE]

            await self.async_set_unique_id(portfolio_id)
            self._abort_if_unique_id_configured()

            edge_label = " edge " if use_edge else " "
            return self.async_create_entry(
                title=f"Sharesight{edge_label}portfolio: {portfolio_id}",
                data={
                    **self._oauth_data,
                    CONF_PORTFOLIO_ID: portfolio_id,
                    CONF_USE_EDGE: use_edge,
                },
            )

        return self.async_show_form(
            step_id="portfolio",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PORTFOLIO_ID): str,
                    vol.Required(CONF_USE_EDGE, default=False): bool,
                }
            ),
        )

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema({}),
            )
        return await self.async_step_user()
