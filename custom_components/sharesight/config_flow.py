import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from .const import DOMAIN


class SharesightConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        data_schema = vol.Schema({
            vol.Required("client_id"): str,
            vol.Required("client_secret"): str,
            vol.Required("portfolio_id"): str,
            vol.Required("authorization_code"): str,
        })

        if user_input is not None:
            try:
                return self.async_create_entry(title=f"Sharesight portfolio: {user_input["portfolio_id"]}", data=user_input)
            except Exception:
                errors["base"] = "auth"

        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SharesightOptionsFlowHandler(config_entry)


class SharesightOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self):
        return await self.async_step_user()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                return self.async_create_entry(title="", data=user_input)
            except Exception:
                errors["base"] = "auth"

        data_schema = vol.Schema({
            vol.Required("client_id", default=self.config_entry.data.get("client_id")): str,
            vol.Required("client_secret", default=self.config_entry.data.get("client_secret")): str,
            vol.Required("portfolio_id", default=self.config_entry.data.get("portfolio_id")): str,
            vol.Required("authorization_code", default=self.config_entry.data.get("authorization_code")): str,
        })

        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)
