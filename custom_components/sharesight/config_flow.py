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

from .application_credentials import account_type_context
from .const import (
    ACCOUNT_DEVELOPER,
    ACCOUNT_STANDARD,
    API_URL_BASE,
    CONF_ACCOUNT_TYPE,
    CONF_AUTO_REMOVE_STALE_DEVICES,
    CONF_ENABLE_LTS_BACKFILL,
    CONF_PORTFOLIO_ID,
    CONF_SCAN_INTERVAL,
    DEFAULT_ACCOUNT_TYPE,
    DEFAULT_AUTO_REMOVE_STALE_DEVICES,
    DEFAULT_ENABLE_LTS_BACKFILL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
    TOKEN_URL,
)

_LOGGER = logging.getLogger(__name__)


class SharesightConfigFlow(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Handle the Sharesight config flow."""

    VERSION = 3
    DOMAIN = DOMAIN

    def __init__(self) -> None:
        super().__init__()
        self._oauth_data: dict[str, Any] = {}
        self._portfolios: dict[str, str] = {}  # {id_str: "name (id)"}
        self._reauth_entry: ConfigEntry | None = None
        self._account_type: str = DEFAULT_ACCOUNT_TYPE

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow for this handler."""
        return SharesightOptionsFlow(config_entry)

    async def _fetch_portfolios(self) -> dict[str, str]:
        """Fetch the portfolio list using the token just minted.

        The token is only valid on the deployment that issued it, so the list
        is implicitly scoped to ``self._account_type`` — there is nothing to
        pass in, and no way for the two to disagree.
        """
        access_token = self._oauth_data.get("token", {}).get("access_token")
        if not access_token:
            _LOGGER.error("No access token available to fetch portfolios")
            return {}

        client = SharesightAPI(
            client_id="",
            client_secret="",
            authorization_code="",
            redirect_uri="",
            token_url=TOKEN_URL[self._account_type],
            api_url_base=API_URL_BASE[self._account_type],
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
        # and ask for the account type first — see async_step_pick_account_type.
        return await self.async_step_pick_account_type()

    async def async_step_pick_account_type(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which kind of Sharesight account is being connected.

        This must run before async_step_pick_implementation, because that is
        where the authorize URL is generated and it cannot be changed
        afterwards.  Standard and developer accounts live on separate
        deployments with separate OAuth app registries, so the credential
        picked on the next step must match the account type chosen here.
        """
        if user_input is not None:
            self._account_type = user_input[CONF_ACCOUNT_TYPE]
            return await self.async_step_pick_implementation()

        return self.async_show_form(
            step_id="pick_account_type",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ACCOUNT_TYPE, default=DEFAULT_ACCOUNT_TYPE
                    ): vol.In(
                        {
                            ACCOUNT_STANDARD: "Standard",
                            ACCOUNT_DEVELOPER: "Developer (sandbox)",
                        }
                    )
                }
            ),
        )

    async def async_step_pick_implementation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the credential, with the account type published to the platform.

        Wrapped rather than entered once in async_step_pick_account_type: the
        ContextVar does not survive the form round-trip, since HA re-enters this
        step in a fresh task when the user submits the credential picker.
        """
        with account_type_context(self._account_type):
            return await super().async_step_pick_implementation(user_input)

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

            # Standard keeps a bare portfolio_id so entries created before the
            # account-type picker keep their unique_id; developer is
            # namespaced, since the two deployments allocate ids independently
            # and could otherwise collide into a spurious "already configured".
            await self.async_set_unique_id(
                portfolio_id
                if self._account_type == ACCOUNT_STANDARD
                else f"{self._account_type}:{portfolio_id}"
            )
            self._abort_if_unique_id_configured()

            portfolio_name = self._portfolios.get(portfolio_id, portfolio_id)
            edge_label = " (Edge)" if self._account_type == ACCOUNT_DEVELOPER else ""
            return self.async_create_entry(
                title=f"Sharesight: {portfolio_name}{edge_label}",
                data={
                    **self._oauth_data,
                    CONF_PORTFOLIO_ID: portfolio_id,
                    CONF_ACCOUNT_TYPE: self._account_type,
                },
            )

        if self._portfolios:
            portfolio_selector: Any = vol.In(self._portfolios)
        else:
            # Most likely cause is a credential/account-type mismatch: the
            # token minted above is only valid on the deployment that issued it.
            _LOGGER.warning(
                "Could not fetch portfolio list for a Sharesight %s account — "
                "check the credential picked matches that account type. "
                "Falling back to manual entry",
                self._account_type,
            )
            portfolio_selector = str

        return self.async_show_form(
            step_id="portfolio",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PORTFOLIO_ID): portfolio_selector,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Kick off a reauth flow — remember the original entry so we can update it."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if self._reauth_entry is not None:
            # Re-auth has to target the account type the entry was created
            # against — its credential does not exist in the other deployment's
            # registry, so re-minting there would fail with invalid_client.
            self._account_type = self._reauth_entry.data.get(
                CONF_ACCOUNT_TYPE, DEFAULT_ACCOUNT_TYPE
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
        current_backfill = self.config_entry.options.get(
            CONF_ENABLE_LTS_BACKFILL, DEFAULT_ENABLE_LTS_BACKFILL
        )
        current_auto_remove = self.config_entry.options.get(
            CONF_AUTO_REMOVE_STALE_DEVICES, DEFAULT_AUTO_REMOVE_STALE_DEVICES
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
                vol.Required(
                    CONF_ENABLE_LTS_BACKFILL,
                    default=current_backfill,
                ): bool,
                vol.Required(
                    CONF_AUTO_REMOVE_STALE_DEVICES,
                    default=current_auto_remove,
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
