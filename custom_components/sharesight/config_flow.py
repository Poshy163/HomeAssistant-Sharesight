"""Config flow for the Sharesight integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from SharesightAPI.SharesightAPI import SharesightAPI

from .const import (
    API_URL_BASE,
    CONF_PORTFOLIO_ID,
    CONF_SCAN_INTERVAL,
    CONF_USE_EDGE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EDGE_API_URL_BASE,
    EDGE_TOKEN_URL,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
    TOKEN_URL,
)

_LOGGER = logging.getLogger(__name__)


class SharesightConfigFlow(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Handle the Sharesight config flow."""

    VERSION = 2
    DOMAIN = DOMAIN

    def __init__(self) -> None:
        super().__init__()
        self._oauth_data: dict[str, Any] = {}
        self._portfolios: dict[str, str] = {}  # {id_str: "name (id)"}
        self._reauth_entry: ConfigEntry | None = None

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow for this handler."""
        return SharesightOptionsFlow(config_entry)

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
            session=async_get_clientsession(self.hass),
        )

        try:
            response = await client.get_api_request(
                ["v3", "portfolios", None, False], access_token
            )
            _LOGGER.debug("Portfolios response: %s", response)

            if not isinstance(response, dict):
                return {}

            portfolios_list = response.get("portfolios", []) or []
            result: dict[str, str] = {}
            for p in portfolios_list:
                pid = str(p.get("id", ""))
                pname = p.get("name", f"Portfolio {pid}")
                if pid:
                    result[pid] = f"{pname} ({pid})"
            return result
        except (aiohttp.ClientError, OSError, ValueError) as err:
            _LOGGER.warning("Failed to fetch portfolio list: %s", err)
            return {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        # Skip domain-level unique_id (which would block multiple portfolios)
        # and go straight to the credential picker.
        return await self.async_step_pick_implementation()

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Handle a successful OAuth flow."""
        self._oauth_data = data

        # If this was a reauth flow, just update the existing entry's token data
        # and don't prompt the user to re-select the portfolio.
        if self._reauth_entry is not None:
            new_data = {**self._reauth_entry.data, **self._oauth_data}
            return self.async_update_reload_and_abort(
                self._reauth_entry,
                data=new_data,
                reason="reauth_successful",
            )

        self._portfolios = await self._fetch_portfolios()
        return await self.async_step_portfolio()

    async def async_step_portfolio(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            portfolio_id = str(user_input[CONF_PORTFOLIO_ID])
            use_edge = user_input.get(CONF_USE_EDGE, False)

            await self.async_set_unique_id(portfolio_id)
            self._abort_if_unique_id_configured()

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

        if self._portfolios:
            portfolio_selector: Any = vol.In(self._portfolios)
        else:
            _LOGGER.warning(
                "Could not fetch portfolio list from Sharesight API, "
                "falling back to manual entry"
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

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Kick off a reauth flow — remember the original entry so we can update it."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema({}),
            )
        # Bounce into the OAuth implementation picker; on success
        # async_oauth_create_entry will update the existing entry.
        return await self.async_step_pick_implementation()


class SharesightOptionsFlow(OptionsFlow):
    """Options flow for Sharesight — currently only the poll interval."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_seconds = int(
            self.config_entry.options.get(
                CONF_SCAN_INTERVAL,
                int(DEFAULT_SCAN_INTERVAL.total_seconds()),
            )
        )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=current_seconds,
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(
                        min=MIN_SCAN_INTERVAL_SECONDS,
                        max=MAX_SCAN_INTERVAL_SECONDS,
                    ),
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
