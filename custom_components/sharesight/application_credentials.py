"""Application credentials platform for the Sharesight integration."""
from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from homeassistant.components.application_credentials import AuthorizationServer
from homeassistant.core import HomeAssistant

from .const import AUTHORIZATION_URL, DEFAULT_ACCOUNT_TYPE, DOMAIN, TOKEN_URL

# HA hands async_get_authorization_server() only `hass` — no ConfigEntry, no
# credential — so the account type cannot be read from the entry that needs it.
# Instead the config flow publishes the chosen account type here before it
# triggers the OAuth steps, and async_setup_entry re-enters the same context
# when it re-resolves the implementation on restart.  Core's
# onedrive_for_business steers its tenant_id through this same pattern.
_account_type_context: ContextVar[str] = ContextVar(f"{DOMAIN}_account_type")


@contextmanager
def account_type_context(account_type: str) -> Generator[None]:
    """Publish ``account_type`` to async_get_authorization_server()."""
    token = _account_type_context.set(account_type)
    try:
        yield
    finally:
        _account_type_context.reset(token)


async def async_get_authorization_server(_hass: HomeAssistant) -> AuthorizationServer:
    """Return the OAuth endpoints for the account type currently in context."""
    # Falls back to standard rather than raising LookupError: implementations
    # are rebuilt on every async_get_implementations() call, including from
    # paths this integration does not drive (the credentials UI listing them,
    # for one), where no context is active.  Standard is the only safe guess
    # there, and a developer entry cannot silently land on it because every
    # path that mints or refreshes its token enters the context first.
    account_type = _account_type_context.get(DEFAULT_ACCOUNT_TYPE)
    return AuthorizationServer(
        authorize_url=AUTHORIZATION_URL[account_type],
        token_url=TOKEN_URL[account_type],
    )
