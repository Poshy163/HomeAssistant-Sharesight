"""New response actions through a real HA service registry and coordinator."""

from datetime import date

from homeassistant.exceptions import ServiceValidationError
import pytest
from SharesightAPI import SharesightResponse

from custom_components.sharesight import services
from custom_components.sharesight.const import DOMAIN

pytestmark = pytest.mark.usefixtures("mock_api", "credential")


async def test_history_actions_dispatch_through_coordinator_and_unload(
    hass, mock_config_entry, mock_api
):
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = mock_config_entry.runtime_data.coordinator
    coordinator.current_date = date(2026, 9, 7)
    coordinator.data["holdings"] = {
        "holdings": [{"id": 123, "instrument": {"id": 456, "code": "TEST", "currency_code": "USD"}}]
    }
    requests_before = len(coordinator._request_gate.request_times)
    mock_api.reset_mock()

    async def response(endpoint, _token):
        path = endpoint[1]
        if path.endswith("holding_value_data.json"):
            body = {"chart": {"data": [{"timestamp": "2026-08-02", "value": 12}]}}
        elif path.endswith("prices.json"):
            body = {"prices": [{"date": "2026-08-02", "close": 7}]}
        else:
            body = {"portfolio_value": {"value": 123}}
        return SharesightResponse(
            body,
            200,
            {"X-MinuteRate-Limit": "360", "X-MinuteRate-Remaining": "250"},
            "https://example.test",
        )

    mock_api.side_effect = response
    data = {"symbol": "TEST", "start_date": "2026-08-01", "end_date": "2026-08-31"}
    value = await hass.services.async_call(
        DOMAIN, "get_holding_value_history", data, blocking=True, return_response=True
    )
    prices = await hass.services.async_call(
        DOMAIN, "get_instrument_price_history", data, blocking=True, return_response=True
    )
    balance = await hass.services.async_call(
        DOMAIN, "get_portfolio_value", {}, blocking=True, return_response=True
    )
    assert value["points"] == [{"date": "2026-08-02", "value": 12}]
    assert prices["prices"] == [{"date": "2026-08-02", "close": 7}]
    assert balance == {"portfolio_value": {"value": 123}}
    assert mock_api.await_count == 3
    assert len(coordinator._request_gate.request_times) == requests_before + 3
    assert coordinator._request_gate.minute_remaining == 250

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "get_instrument_price_history",
            {**data, "end_date": "2026-01-01"},
            blocking=True,
            return_response=True,
        )
    assert mock_api.await_count == 3
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    # HA's action-setup contract keeps services registered for the process
    # lifetime. An unloaded portfolio must be rejected before any request.
    assert all(hass.services.has_service(DOMAIN, name) for name in services._SERVICES)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "get_portfolio_value", {}, blocking=True, return_response=True
        )
    assert mock_api.await_count == 3
