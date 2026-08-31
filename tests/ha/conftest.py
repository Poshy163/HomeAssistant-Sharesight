"""Fixtures for the Home Assistant integration tests.

These require ``pytest-homeassistant-custom-component``, which brings a real
Home Assistant core.  They live under ``tests/ha/`` so the pure-logic suites in
``tests/`` stay importable without it.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from SharesightAPI import SharesightResponse

from custom_components.sharesight.const import (
    ACCOUNT_STANDARD,
    CONF_ACCOUNT_TYPE,
    CONF_PORTFOLIO_ID,
    DOMAIN,
)

from .. import fixtures as F

CLIENT_ID = "test-client-id"
CLIENT_SECRET = "test-client-secret"
PORTFOLIO_ID = str(F.PORTFOLIO_ID)
REDIRECT_URI = "https://example.com/auth/external/callback"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Make custom_components/sharesight loadable in every test."""
    yield


@pytest.fixture(name="credential")
async def credential_fixture(hass: HomeAssistant) -> None:
    """Register an application credential for the OAuth flow to pick."""
    assert await async_setup_component(hass, "application_credentials", {})
    await async_import_client_credential(
        hass, DOMAIN, ClientCredential(CLIENT_ID, CLIENT_SECRET), DOMAIN
    )


@pytest.fixture(name="token")
def token_fixture() -> dict[str, Any]:
    return {
        "access_token": "test-access-token",
        "refresh_token": "test-refresh-token",
        "expires_at": 9_999_999_999,
        "expires_in": 1800,
        "type": "Bearer",
    }


@pytest.fixture(name="mock_config_entry")
def mock_config_entry_fixture(token: dict[str, Any]) -> MockConfigEntry:
    """A v3 entry that looks exactly like one the flow would create."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Sharesight: Test Portfolio (1020131)",
        version=3,
        unique_id=PORTFOLIO_ID,
        data={
            "auth_implementation": DOMAIN,
            CONF_PORTFOLIO_ID: PORTFOLIO_ID,
            CONF_ACCOUNT_TYPE: ACCOUNT_STANDARD,
            "token": token,
        },
    )


def api_response(endpoint: list[Any]) -> Any:
    """The fixture payload for one endpoint of the plan."""
    _version, path, params, _ = endpoint
    canonical_path = path.removesuffix(".json")
    pid = PORTFOLIO_ID
    if canonical_path == "portfolios":
        return {"portfolios": F.PORTFOLIOS}
    if canonical_path == f"portfolios/{pid}":
        if _version == "v2":
            detail = dict(F.PORTFOLIO_DETAIL)
            detail["inception_date"] = "18 Jul 2023"
            return detail
        return {"portfolio": F.PORTFOLIO_DETAIL}
    if canonical_path.endswith("/performance"):
        if endpoint[0] == "v3":
            return {"report": F.performance_report()}
        return F.period_report(
            start_date=(params or {}).get("start_date", "2026-01-01"),
            end_date=(params or {}).get("end_date", F.TODAY),
            capital_gain=100.0,
        )
    if canonical_path.endswith("/payouts"):
        if params and params.get("start_date") == F.TODAY:
            return {"payouts": F.UPCOMING_PAYOUTS}
        return {"payouts": F.PAYOUTS}
    if canonical_path.endswith("/trades"):
        return {"trades": F.TRADES}
    if canonical_path.endswith("/diversity"):
        return F.DIVERSITY_V2
    if canonical_path == "cash_accounts":
        return {"cash_accounts": F.CASH_ACCOUNTS_V2}
    if "cash_account_transactions" in path:
        return {"cash_account_transactions": F.CASH_TRANSACTIONS}
    if path.endswith("/user_setting"):
        return F.USER_SETTING
    if canonical_path == "user_instruments":
        return F.USER_INSTRUMENTS
    if canonical_path.endswith("/benchmark"):
        return {"benchmark": F.BENCHMARK}
    if path == "my_user.json":
        return F.MY_USER
    if path == "watchlist.json":
        return F.WATCHLIST
    if canonical_path.endswith("/capital_gains"):
        return F.CAPITAL_GAINS
    if canonical_path.endswith("/unrealised_cgt"):
        return F.UNREALISED_CGT
    if "portfolio_value_data" in path:
        return F.VALUE_SERIES
    return {}


@pytest.fixture(name="mock_api")
def mock_api_fixture() -> Generator[AsyncMock]:
    """Stub the Sharesight client everywhere the integration constructs one."""

    async def _get(endpoint: list[Any], _token: Any = None) -> Any:
        return api_response(endpoint)

    async def _get_response(endpoint: list[Any], _token: Any = None) -> SharesightResponse:
        return SharesightResponse(
            data=api_response(endpoint),
            status=200,
            headers={},
            url=f"https://api.sharesight.com/api/{endpoint[0]}/{endpoint[1]}",
        )

    legacy_request = AsyncMock(side_effect=_get)
    rich_request = AsyncMock(side_effect=_get_response)
    with (
        patch("custom_components.sharesight.SharesightAPI", autospec=True) as setup_client,
        patch(
            "custom_components.sharesight.config_flow.SharesightAPI", autospec=True
        ) as flow_client,
    ):
        setup_client.return_value.get_api_request = legacy_request
        setup_client.return_value.get_api_response = rich_request
        flow_client.return_value.get_api_request = legacy_request
        flow_client.return_value.get_api_response = rich_request
        yield rich_request
