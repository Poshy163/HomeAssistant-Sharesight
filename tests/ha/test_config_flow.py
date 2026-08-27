"""Config, reauth, immutable-portfolio and options flows."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
import voluptuous as vol

from custom_components.sharesight.const import (
    ACCOUNT_DEVELOPER,
    ACCOUNT_STANDARD,
    AUTHORIZATION_URL,
    CONF_ACCOUNT_TYPE,
    CONF_PORTFOLIO_ID,
    DOMAIN,
    TOKEN_URL,
)

from .conftest import CLIENT_ID, CLIENT_SECRET, PORTFOLIO_ID

REDIRECT_URI = "https://example.com/auth/external/callback"

pytestmark = pytest.mark.usefixtures("mock_api")


async def _complete_oauth(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    result: dict[str, Any],
    account_type: str = ACCOUNT_STANDARD,
) -> dict[str, Any]:
    """Drive Home Assistant's external OAuth step to completion."""
    state = config_entry_oauth2_flow._encode_jwt(
        hass,
        {"flow_id": result["flow_id"], "redirect_uri": REDIRECT_URI},
    )
    assert result["url"].startswith(AUTHORIZATION_URL[account_type])
    assert f"client_id={CLIENT_ID}" in result["url"]

    client = await hass_client_no_auth()
    response = await client.get(f"/auth/external/callback?code=abcd&state={state}")
    assert response.status == 200

    aioclient_mock.clear_requests()
    aioclient_mock.post(
        TOKEN_URL[account_type],
        json={
            "refresh_token": "mock-refresh-token",
            "access_token": "mock-access-token",
            "type": "Bearer",
            "expires_in": 1800,
        },
    )
    return await hass.config_entries.flow.async_configure(result["flow_id"])


@pytest.fixture(name="setup_credentials")
async def setup_credentials_fixture(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "application_credentials", {})
    await async_import_client_credential(
        hass, DOMAIN, ClientCredential(CLIENT_ID, CLIENT_SECRET), DOMAIN
    )


@pytest.mark.usefixtures("current_request_with_host", "setup_credentials")
async def test_full_user_flow(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Account type, credential, OAuth, then the portfolio picker."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pick_account_type"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCOUNT_TYPE: ACCOUNT_STANDARD}
    )
    assert result["type"] is FlowResultType.EXTERNAL_STEP

    result = await _complete_oauth(hass, hass_client_no_auth, aioclient_mock, result)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "portfolio"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PORTFOLIO_ID: PORTFOLIO_ID}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == PORTFOLIO_ID
    assert result["data"][CONF_PORTFOLIO_ID] == PORTFOLIO_ID
    assert result["data"][CONF_ACCOUNT_TYPE] == ACCOUNT_STANDARD
    assert result["data"]["token"]["access_token"] == "mock-access-token"


@pytest.mark.usefixtures("current_request_with_host", "setup_credentials")
async def test_developer_account_uses_the_sandbox_endpoints(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """The two deployments have separate OAuth registries."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCOUNT_TYPE: ACCOUNT_DEVELOPER}
    )
    assert result["url"].startswith(AUTHORIZATION_URL[ACCOUNT_DEVELOPER])

    result = await _complete_oauth(
        hass,
        hass_client_no_auth,
        aioclient_mock,
        result,
        account_type=ACCOUNT_DEVELOPER,
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PORTFOLIO_ID: PORTFOLIO_ID}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # Developer ids are namespaced: the two deployments allocate them
    # independently and would otherwise collide.
    assert result["result"].unique_id == f"{ACCOUNT_DEVELOPER}:{PORTFOLIO_ID}"


@pytest.mark.usefixtures("current_request_with_host", "setup_credentials")
async def test_duplicate_portfolio_is_rejected(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCOUNT_TYPE: ACCOUNT_STANDARD}
    )
    result = await _complete_oauth(hass, hass_client_no_auth, aioclient_mock, result)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PORTFOLIO_ID: PORTFOLIO_ID}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("current_request_with_host", "setup_credentials")
async def test_reauth_updates_the_existing_entry(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _complete_oauth(hass, hass_client_no_auth, aioclient_mock, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data["token"]["access_token"] == "mock-access-token"
    # The portfolio selection survives reauthentication.
    assert mock_config_entry.data[CONF_PORTFOLIO_ID] == PORTFOLIO_ID


@pytest.mark.usefixtures("current_request_with_host", "setup_credentials")
async def test_reauth_with_the_wrong_account_is_refused(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    token: dict[str, Any],
) -> None:
    """A second Sharesight login cannot see this portfolio, so refuse it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        unique_id="999999",
        data={
            "auth_implementation": DOMAIN,
            CONF_PORTFOLIO_ID: "999999",
            CONF_ACCOUNT_TYPE: ACCOUNT_STANDARD,
            "token": token,
        },
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _complete_oauth(hass, hass_client_no_auth, aioclient_mock, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"


@pytest.mark.usefixtures("setup_credentials")
async def test_reconfigure_protects_the_portfolio_identity(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Switching portfolio IDs would duplicate every registry identity."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "portfolio_identity_immutable"
    assert mock_config_entry.data[CONF_PORTFOLIO_ID] == PORTFOLIO_ID


@pytest.mark.usefixtures("setup_credentials")
async def test_options_flow(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "scan_interval": 600,
            "enable_lts_backfill": False,
            "auto_remove_stale_devices": True,
            "enable_holding_entities": True,
            "enable_extended_performance": True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options["scan_interval"] == 600
    assert mock_config_entry.options["enable_extended_performance"] is True


@pytest.mark.usefixtures("setup_credentials")
@pytest.mark.parametrize("interval", [1, 59, 3601, 100000])
async def test_options_flow_rejects_an_out_of_range_interval(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, interval: int
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    # voluptuous rejects it before the flow can store anything.
    with pytest.raises(vol.Invalid):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "scan_interval": interval,
                "enable_lts_backfill": True,
                "auto_remove_stale_devices": False,
                "enable_holding_entities": True,
                "enable_extended_performance": False,
            },
        )
