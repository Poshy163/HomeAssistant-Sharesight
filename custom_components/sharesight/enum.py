from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from homeassistant.components.sensor import SensorEntityDescription, SensorDeviceClass, SensorStateClass
from homeassistant.const import CURRENCY_DOLLAR, PERCENTAGE, EntityCategory


@dataclass(frozen=True)
class SharesightSensorDescription(SensorEntityDescription):
    sub_key: str | None = None
    extension_key: str | None = None
    native_value: Callable[[str | int | float], str | int | float] | None = None
    device_group: str | None = "portfolio"

    def __post_init__(self) -> None:
        # Home Assistant rejects state_class='measurement' for device_class='monetary',
        # but it does accept 'total'. Coerce here so individual descriptions don't have
        # to repeat the rule, and so previously-recorded long-term statistics keep a
        # valid state_class (avoiding "no longer has a state class" repair issues).
        if (
            self.device_class == SensorDeviceClass.MONETARY
            and self.state_class == SensorStateClass.MEASUREMENT
        ):
            object.__setattr__(self, "state_class", SensorStateClass.TOTAL)


CASH_SENSOR_DESCRIPTIONS: list[SharesightSensorDescription] = [
    SharesightSensorDescription(
        key='cash_accounts',
        sub_key="value",
        extension_key=None,
        name="CASH balance",
        icon="mdi:cash",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2,
        device_group="cash"
    )
]

MARKET_SENSOR_DESCRIPTIONS: list[SharesightSensorDescription] = [
    SharesightSensorDescription(
        key='sub_totals',
        sub_key="value",
        extension_key=None,
        name="MARKET value",
        icon="mdi:finance",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2,
        device_group="market"
    ),
    SharesightSensorDescription(
        key='sub_totals',
        sub_key="capital_gain",
        extension_key=None,
        name="MARKET capital gain",
        icon="mdi:cash-plus",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2,
        device_group="market"
    ),
    SharesightSensorDescription(
        key='sub_totals',
        sub_key="capital_gain_percent",
        extension_key=None,
        name="MARKET capital gain percent",
        icon="mdi:sack-percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2,
        device_group="market"
    ),
    SharesightSensorDescription(
        key='sub_totals',
        sub_key="total_gain",
        extension_key=None,
        name="MARKET total gain",
        icon="mdi:cash-plus",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2,
        device_group="market"
    ),
    SharesightSensorDescription(
        key='sub_totals',
        sub_key="total_gain_percent",
        extension_key=None,
        name="MARKET total gain percent",
        icon="mdi:sack-percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2,
        device_group="market"
    ),
    SharesightSensorDescription(
        key='sub_totals',
        sub_key="currency_gain",
        extension_key=None,
        name="MARKET currency gain",
        icon="mdi:cash-plus",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2,
        device_group="market"
    ),
    SharesightSensorDescription(
        key='sub_totals',
        sub_key="currency_gain_percent",
        extension_key=None,
        name="MARKET currency gain percent",
        icon="mdi:sack-percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2,
        device_group="market"
    ),
    SharesightSensorDescription(
        key='sub_totals',
        sub_key="payout_gain",
        extension_key=None,
        name="MARKET dividend gain",
        icon="mdi:hand-coin",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2,
        device_group="market"
    ),
    SharesightSensorDescription(
        key='sub_totals',
        sub_key="payout_gain_percent",
        extension_key=None,
        name="MARKET dividend gain percent",
        icon="mdi:hand-coin",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2,
        device_group="market"
    ),
    SharesightSensorDescription(
        key='sub_totals',
        sub_key="holding_count",
        extension_key=None,
        name="MARKET holding count",
        icon="mdi:format-list-numbered",
        native_unit_of_measurement="holdings",
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=0,
        device_group="market"
    ),
    SharesightSensorDescription(
        key='sub_totals',
        sub_key="cost_base",
        extension_key=None,
        name="MARKET cost basis",
        icon="mdi:cash-register",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2,
        device_group="market"
    ),
    SharesightSensorDescription(
        key='sub_totals',
        sub_key="annualised_return_percent",
        extension_key=None,
        name="MARKET annualised return percent",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2,
        device_group="market"
    ),
]

# Per-holding individual sensors (created dynamically for each holding)
HOLDING_SENSOR_DESCRIPTIONS: list[SharesightSensorDescription] = [
    SharesightSensorDescription(
        key='holdings_list',
        sub_key="value",
        name="HOLDING value",
        icon="mdi:cash",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        device_group="holding",
    ),
    SharesightSensorDescription(
        key='holdings_list',
        sub_key="capital_gain",
        name="HOLDING capital gain",
        icon="mdi:cash-plus",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        device_group="holding",
    ),
    SharesightSensorDescription(
        key='holdings_list',
        sub_key="capital_gain_percent",
        name="HOLDING capital gain percent",
        icon="mdi:sack-percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        device_group="holding",
    ),
    SharesightSensorDescription(
        key='holdings_list',
        sub_key="payout_gain",
        name="HOLDING dividend gain",
        icon="mdi:hand-coin",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        device_group="holding",
    ),
    SharesightSensorDescription(
        key='holdings_list',
        sub_key="payout_gain_percent",
        name="HOLDING dividend gain percent",
        icon="mdi:hand-coin",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        device_group="holding",
    ),
    SharesightSensorDescription(
        key='holdings_list',
        sub_key="currency_gain",
        name="HOLDING currency gain",
        icon="mdi:currency-usd",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        device_group="holding",
    ),
    SharesightSensorDescription(
        key='holdings_list',
        sub_key="currency_gain_percent",
        name="HOLDING currency gain percent",
        icon="mdi:currency-usd",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        device_group="holding",
    ),
    SharesightSensorDescription(
        key='holdings_list',
        sub_key="total_gain",
        name="HOLDING total gain",
        icon="mdi:cash-plus",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        device_group="holding",
    ),
    SharesightSensorDescription(
        key='holdings_list',
        sub_key="total_gain_percent",
        name="HOLDING total gain percent",
        icon="mdi:sack-percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        device_group="holding",
    ),
    SharesightSensorDescription(
        key='holdings_list',
        sub_key="cost_base",
        name="HOLDING cost basis",
        icon="mdi:cash-register",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        device_group="holding",
    ),
    SharesightSensorDescription(
        key='holdings_list',
        sub_key="quantity",
        name="HOLDING quantity",
        icon="mdi:counter",
        native_unit_of_measurement="shares",
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        device_group="holding",
    ),
    SharesightSensorDescription(
        key='holdings_list',
        sub_key="instrument_price",
        name="HOLDING price",
        icon="mdi:tag-outline",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        device_group="holding",
    ),
    SharesightSensorDescription(
        key='holdings_list',
        sub_key="annualised_return_percent",
        name="HOLDING annualised return percent",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        device_group="holding",
    ),
]


def _period_sensor(key, sub_key, name, icon, unit, device_class, state_class, precision, device_group="monthly"):
    """Helper to build monthly/YTD sensor descriptions."""
    return SharesightSensorDescription(
        key=key,
        sub_key=sub_key,
        extension_key="Extension",
        name=name,
        icon=icon,
        native_unit_of_measurement=unit,
        device_class=device_class,
        state_class=state_class,
        entity_category=None,
        suggested_display_precision=precision,
        device_group=device_group,
    )


SENSOR_DESCRIPTIONS: list[SharesightSensorDescription] = [
    # ===== Portfolio-level performance from V3 report =====
    SharesightSensorDescription(
        key="value",
        sub_key="report",
        extension_key=None,
        name="Portfolio value",
        icon="mdi:cash",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2
    ),
    SharesightSensorDescription(
        key="capital_gain",
        sub_key="report",
        extension_key=None,
        name="Capital gain",
        icon="mdi:cash-plus",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2
    ),
    SharesightSensorDescription(
        key="capital_gain_percent",
        sub_key="report",
        extension_key=None,
        name="Capital gain percent",
        icon="mdi:sack-percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2
    ),
    SharesightSensorDescription(
        key="total_gain",
        sub_key="report",
        extension_key=None,
        name="Total gain",
        icon="mdi:cash-plus",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2
    ),
    SharesightSensorDescription(
        key="total_gain_percent",
        sub_key="report",
        extension_key=None,
        name="Total gain percent",
        icon="mdi:sack-percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2
    ),
    SharesightSensorDescription(
        key="annualised_return_percent",
        sub_key="report",
        extension_key=None,
        name="Annualised Return Percent",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2
    ),
    SharesightSensorDescription(
        key="currency_gain",
        sub_key="report",
        extension_key=None,
        name="Currency gain",
        icon="mdi:cash-plus",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2
    ),
    SharesightSensorDescription(
        key="currency_gain_percent",
        sub_key="report",
        extension_key=None,
        name="Currency gain percent",
        icon="mdi:sack-percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2
    ),
    SharesightSensorDescription(
        key="payout_gain",
        sub_key="report",
        extension_key=None,
        name="Dividend gain",
        icon="mdi:cash-plus",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2
    ),
    SharesightSensorDescription(
        key="payout_gain_percent",
        sub_key="report",
        extension_key=None,
        name="Dividend gain percent",
        icon="mdi:sack-percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        suggested_display_precision=2
    ),

    # ===== Daily performance (one-day) =====
    SharesightSensorDescription(key="total_gain_percent", sub_key="one-day", extension_key="Extension", name="Daily Change Percent", icon="mdi:chart-line-variant", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="daily"),
    SharesightSensorDescription(key="total_gain", sub_key="one-day", extension_key="Extension", name="Daily Change Amount", icon="mdi:chart-line-variant", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="daily"),
    SharesightSensorDescription(key="capital_gain", sub_key="one-day", extension_key="Extension", name="Daily Capital Gain", icon="mdi:chart-line-variant", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="daily"),
    SharesightSensorDescription(key="capital_gain_percent", sub_key="one-day", extension_key="Extension", name="Daily Capital Gain Percent", icon="mdi:chart-line-variant", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="daily"),
    SharesightSensorDescription(key="currency_gain", sub_key="one-day", extension_key="Extension", name="Daily Currency Gain", icon="mdi:currency-usd", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="daily"),
    SharesightSensorDescription(key="currency_gain_percent", sub_key="one-day", extension_key="Extension", name="Daily Currency Gain Percent", icon="mdi:currency-usd", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="daily"),
    SharesightSensorDescription(key="payout_gain", sub_key="one-day", extension_key="Extension", name="Daily Dividend Gain", icon="mdi:hand-coin", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="daily"),
    SharesightSensorDescription(key="payout_gain_percent", sub_key="one-day", extension_key="Extension", name="Daily Dividend Gain Percent", icon="mdi:hand-coin", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="daily"),
    SharesightSensorDescription(key="start_value", sub_key="one-day", extension_key="Extension", name="Daily Start Value", icon="mdi:flag-checkered", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="daily"),
    SharesightSensorDescription(key="end_value", sub_key="one-day", extension_key="Extension", name="Daily End Value", icon="mdi:flag-variant", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="daily"),

    # ===== Weekly performance (one-week) =====
    SharesightSensorDescription(key="total_gain", sub_key="one-week", extension_key="Extension", name="Weekly Change Amount", icon="mdi:chart-line-variant", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="weekly"),
    SharesightSensorDescription(key="total_gain_percent", sub_key="one-week", extension_key="Extension", name="Weekly Change Percent", icon="mdi:chart-line-variant", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="weekly"),
    SharesightSensorDescription(key="capital_gain", sub_key="one-week", extension_key="Extension", name="Weekly Capital Gain", icon="mdi:chart-line-variant", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="weekly"),
    SharesightSensorDescription(key="capital_gain_percent", sub_key="one-week", extension_key="Extension", name="Weekly Capital Gain Percent", icon="mdi:chart-line-variant", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="weekly"),
    SharesightSensorDescription(key="currency_gain", sub_key="one-week", extension_key="Extension", name="Weekly Currency Gain", icon="mdi:currency-usd", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="weekly"),
    SharesightSensorDescription(key="currency_gain_percent", sub_key="one-week", extension_key="Extension", name="Weekly Currency Gain Percent", icon="mdi:currency-usd", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="weekly"),
    SharesightSensorDescription(key="payout_gain", sub_key="one-week", extension_key="Extension", name="Weekly Dividend Gain", icon="mdi:hand-coin", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="weekly"),
    SharesightSensorDescription(key="payout_gain_percent", sub_key="one-week", extension_key="Extension", name="Weekly Dividend Gain Percent", icon="mdi:hand-coin", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="weekly"),
    SharesightSensorDescription(key="start_value", sub_key="one-week", extension_key="Extension", name="Weekly Start Value", icon="mdi:flag-checkered", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="weekly"),
    SharesightSensorDescription(key="end_value", sub_key="one-week", extension_key="Extension", name="Weekly End Value", icon="mdi:flag-variant", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="weekly"),

    # ===== Financial Year =====
    SharesightSensorDescription(key="total_gain_percent", sub_key="financial-year", extension_key="Extension", name="Financial Year Change Percent", icon="mdi:chart-timeline-variant", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="financial_year"),
    SharesightSensorDescription(key="total_gain", sub_key="financial-year", extension_key="Extension", name="Financial Year Change Amount", icon="mdi:chart-timeline-variant", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="financial_year"),
    SharesightSensorDescription(key="annualised_return_percent", sub_key="financial-year", extension_key="Extension", name="Financial Year Annualised Return Percent", icon="mdi:percent", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="financial_year"),
    SharesightSensorDescription(key="capital_gain", sub_key="financial-year", extension_key="Extension", name="Financial Year Capital Gain", icon="mdi:chart-timeline-variant", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="financial_year"),
    SharesightSensorDescription(key="capital_gain_percent", sub_key="financial-year", extension_key="Extension", name="Financial Year Capital Gain Percent", icon="mdi:chart-timeline-variant", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="financial_year"),
    SharesightSensorDescription(key="currency_gain", sub_key="financial-year", extension_key="Extension", name="Financial Year Currency Gain", icon="mdi:currency-usd", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="financial_year"),
    SharesightSensorDescription(key="currency_gain_percent", sub_key="financial-year", extension_key="Extension", name="Financial Year Currency Gain Percent", icon="mdi:currency-usd", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="financial_year"),
    SharesightSensorDescription(key="payout_gain", sub_key="financial-year", extension_key="Extension", name="Financial Year Dividend Gain", icon="mdi:hand-coin", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="financial_year"),
    SharesightSensorDescription(key="payout_gain_percent", sub_key="financial-year", extension_key="Extension", name="Financial Year Dividend Gain Percent", icon="mdi:hand-coin", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="financial_year"),
    SharesightSensorDescription(key="start_value", sub_key="financial-year", extension_key="Extension", name="Financial Year Start Value", icon="mdi:flag-checkered", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="financial_year"),
    SharesightSensorDescription(key="end_value", sub_key="financial-year", extension_key="Extension", name="Financial Year End Value", icon="mdi:flag-variant", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="financial_year"),

    # ===== Monthly performance (one-month, 30 days) =====
    _period_sensor("total_gain", "one-month", "Monthly Change Amount", "mdi:calendar-month", CURRENCY_DOLLAR, SensorDeviceClass.MONETARY, SensorStateClass.MEASUREMENT, 2),
    _period_sensor("total_gain_percent", "one-month", "Monthly Change Percent", "mdi:calendar-month", PERCENTAGE, None, SensorStateClass.MEASUREMENT, 2),
    _period_sensor("capital_gain", "one-month", "Monthly Capital Gain", "mdi:calendar-month", CURRENCY_DOLLAR, SensorDeviceClass.MONETARY, SensorStateClass.MEASUREMENT, 2),
    _period_sensor("capital_gain_percent", "one-month", "Monthly Capital Gain Percent", "mdi:calendar-month", PERCENTAGE, None, SensorStateClass.MEASUREMENT, 2),
    _period_sensor("currency_gain", "one-month", "Monthly Currency Gain", "mdi:currency-usd", CURRENCY_DOLLAR, SensorDeviceClass.MONETARY, SensorStateClass.MEASUREMENT, 2),
    _period_sensor("currency_gain_percent", "one-month", "Monthly Currency Gain Percent", "mdi:currency-usd", PERCENTAGE, None, SensorStateClass.MEASUREMENT, 2),
    _period_sensor("payout_gain", "one-month", "Monthly Dividend Gain", "mdi:hand-coin", CURRENCY_DOLLAR, SensorDeviceClass.MONETARY, SensorStateClass.MEASUREMENT, 2),
    _period_sensor("payout_gain_percent", "one-month", "Monthly Dividend Gain Percent", "mdi:hand-coin", PERCENTAGE, None, SensorStateClass.MEASUREMENT, 2),
    _period_sensor("start_value", "one-month", "Monthly Start Value", "mdi:flag-checkered", CURRENCY_DOLLAR, SensorDeviceClass.MONETARY, SensorStateClass.MEASUREMENT, 2),
    _period_sensor("end_value", "one-month", "Monthly End Value", "mdi:flag-variant", CURRENCY_DOLLAR, SensorDeviceClass.MONETARY, SensorStateClass.MEASUREMENT, 2),
    _period_sensor("annualised_return_percent", "one-month", "Monthly Annualised Return Percent", "mdi:percent", PERCENTAGE, None, SensorStateClass.MEASUREMENT, 2),

    # ===== Year-to-Date (YTD) performance =====
    _period_sensor("total_gain", "ytd", "YTD Change Amount", "mdi:calendar-today", CURRENCY_DOLLAR, SensorDeviceClass.MONETARY, SensorStateClass.MEASUREMENT, 2, "ytd"),
    _period_sensor("total_gain_percent", "ytd", "YTD Change Percent", "mdi:calendar-today", PERCENTAGE, None, SensorStateClass.MEASUREMENT, 2, "ytd"),
    _period_sensor("capital_gain", "ytd", "YTD Capital Gain", "mdi:calendar-today", CURRENCY_DOLLAR, SensorDeviceClass.MONETARY, SensorStateClass.MEASUREMENT, 2, "ytd"),
    _period_sensor("capital_gain_percent", "ytd", "YTD Capital Gain Percent", "mdi:calendar-today", PERCENTAGE, None, SensorStateClass.MEASUREMENT, 2, "ytd"),
    _period_sensor("currency_gain", "ytd", "YTD Currency Gain", "mdi:currency-usd", CURRENCY_DOLLAR, SensorDeviceClass.MONETARY, SensorStateClass.MEASUREMENT, 2, "ytd"),
    _period_sensor("currency_gain_percent", "ytd", "YTD Currency Gain Percent", "mdi:currency-usd", PERCENTAGE, None, SensorStateClass.MEASUREMENT, 2, "ytd"),
    _period_sensor("payout_gain", "ytd", "YTD Dividend Gain", "mdi:hand-coin", CURRENCY_DOLLAR, SensorDeviceClass.MONETARY, SensorStateClass.MEASUREMENT, 2, "ytd"),
    _period_sensor("payout_gain_percent", "ytd", "YTD Dividend Gain Percent", "mdi:hand-coin", PERCENTAGE, None, SensorStateClass.MEASUREMENT, 2, "ytd"),
    _period_sensor("start_value", "ytd", "YTD Start Value", "mdi:flag-checkered", CURRENCY_DOLLAR, SensorDeviceClass.MONETARY, SensorStateClass.MEASUREMENT, 2, "ytd"),
    _period_sensor("end_value", "ytd", "YTD End Value", "mdi:flag-variant", CURRENCY_DOLLAR, SensorDeviceClass.MONETARY, SensorStateClass.MEASUREMENT, 2, "ytd"),
    _period_sensor("annualised_return_percent", "ytd", "YTD Annualised Return Percent", "mdi:percent", PERCENTAGE, None, SensorStateClass.MEASUREMENT, 2, "ytd"),

    # ===== Diagnostic / metadata sensors =====
    SharesightSensorDescription(key="portfolio_id", sub_key="report", extension_key=None, name="Portfolio ID", icon="mdi:identifier", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=2),
    SharesightSensorDescription(key="market_count", sub_key="report", extension_key=None, name="Market Count", icon="mdi:format-list-numbered", native_unit_of_measurement="markets", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=0),
    SharesightSensorDescription(key="cash_accounts_count", sub_key="report", extension_key=None, name="Cash Accounts Count", icon="mdi:bank", native_unit_of_measurement="accounts", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=0),
    SharesightSensorDescription(key="total_cash_value", sub_key="report", extension_key=None, name="Total Cash Value", icon="mdi:cash-multiple", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2),
    SharesightSensorDescription(key="largest_market_name", sub_key="report", extension_key=None, name="Largest Market Name", icon="mdi:earth", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None),
    SharesightSensorDescription(key="largest_market_value", sub_key="report", extension_key=None, name="Largest Market Value", icon="mdi:earth", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2),
    SharesightSensorDescription(key="largest_market_percent", sub_key="report", extension_key=None, name="Largest Market Percent", icon="mdi:earth", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2),
    SharesightSensorDescription(key="portfolio_tz_name", sub_key="report", extension_key=None, name="Portfolio Timezone", icon="mdi:map-clock-outline", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None),
    SharesightSensorDescription(key="grouping", sub_key="report", extension_key=None, name="Active Grouping", icon="mdi:view-grid-outline", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None),
    SharesightSensorDescription(key="report_currency", sub_key="user_setting", extension_key=None, name="Report Currency", icon="mdi:cash", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None),
    SharesightSensorDescription(key="report_grouping", sub_key="user_setting", extension_key=None, name="Report Grouping", icon="mdi:view-list-outline", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None),
    SharesightSensorDescription(key="report_combined", sub_key="user_setting", extension_key=None, name="Report Combined", icon="mdi:call-merge", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None),
    SharesightSensorDescription(key="report_include_sold_shares", sub_key="user_setting", extension_key=None, name="Report Includes Sold Shares", icon="mdi:identifier", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None),
    SharesightSensorDescription(key="user_id", sub_key="portfolios", extension_key=None, name="User ID", icon="mdi:identifier", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=2),
    SharesightSensorDescription(key="currency_code", sub_key="portfolios", extension_key=None, name="Primary Currency", icon="mdi:cash", native_unit_of_measurement=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC),
    SharesightSensorDescription(key="name", sub_key="portfolios", extension_key=None, name="Portfolio Name", icon="mdi:briefcase", native_unit_of_measurement=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC),
    SharesightSensorDescription(key="financial_year_end", sub_key="portfolios", extension_key=None, name="Financial Year End", icon="mdi:calendar-end", native_unit_of_measurement=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC),

    # ===== All-time computed from report =====
    SharesightSensorDescription(key="cost_base", sub_key="report", extension_key=None, name="Cost Basis", icon="mdi:cash-register", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2),
    SharesightSensorDescription(key="unrealised_gain", sub_key="report", extension_key=None, name="Unrealised Gain", icon="mdi:trending-up", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2),
    SharesightSensorDescription(key="unrealised_gain_percent", sub_key="report", extension_key=None, name="Unrealised Gain Percent", icon="mdi:percent", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2),
    SharesightSensorDescription(key="start_value", sub_key="report", extension_key=None, name="Portfolio Start Value", icon="mdi:flag-checkered", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2),

    # ===== Portfolio aggregate sensors =====
    SharesightSensorDescription(key="equity_value", sub_key="report", extension_key=None, name="Equity Value", icon="mdi:chart-areaspline", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2),
    SharesightSensorDescription(key="cash_allocation_percent", sub_key="report", extension_key=None, name="Cash Allocation Percent", icon="mdi:cash-100", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2),
    SharesightSensorDescription(key="equity_allocation_percent", sub_key="report", extension_key=None, name="Equity Allocation Percent", icon="mdi:chart-pie", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2),

    # ===== Unconfirmed transactions =====
    SharesightSensorDescription(key="unconfirmed_transactions", sub_key="holdings", extension_key=None, name="Unconfirmed Transactions", icon="mdi:alert-circle-outline", native_unit_of_measurement="transactions", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=0, device_group="holdings"),

    # ===== Holdings aggregate sensors =====
    SharesightSensorDescription(key="holding_count", sub_key="holdings", extension_key=None, name="Number of Holdings", icon="mdi:chart-box-multiple", native_unit_of_measurement="holdings", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=0, device_group="holdings"),
    SharesightSensorDescription(key="largest_holding_symbol", sub_key="holdings", extension_key=None, name="Largest Holding Symbol", icon="mdi:crown", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None, device_group="holdings"),
    SharesightSensorDescription(key="largest_holding_value", sub_key="holdings", extension_key=None, name="Largest Holding Value", icon="mdi:crown", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="holdings"),
    SharesightSensorDescription(key="largest_holding_percent", sub_key="holdings", extension_key=None, name="Largest Holding Percent", icon="mdi:crown", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="holdings"),
    SharesightSensorDescription(key="top_gain_symbol", sub_key="holdings", extension_key=None, name="Top Gain Symbol", icon="mdi:trending-up", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None, device_group="holdings"),
    SharesightSensorDescription(key="top_gain_amount", sub_key="holdings", extension_key=None, name="Top Gain Amount", icon="mdi:trending-up", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="holdings"),
    SharesightSensorDescription(key="top_gain_percent", sub_key="holdings", extension_key=None, name="Top Gain Percent", icon="mdi:trending-up", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="holdings"),
    SharesightSensorDescription(key="worst_gain_symbol", sub_key="holdings", extension_key=None, name="Worst Gain Symbol", icon="mdi:trending-down", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None, device_group="holdings"),
    SharesightSensorDescription(key="worst_gain_amount", sub_key="holdings", extension_key=None, name="Worst Gain Amount", icon="mdi:trending-down", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="holdings"),
    SharesightSensorDescription(key="worst_gain_percent", sub_key="holdings", extension_key=None, name="Worst Gain Percent", icon="mdi:trending-down", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="holdings"),
    SharesightSensorDescription(key="positive_holdings_count", sub_key="holdings", extension_key=None, name="Positive Holdings Count", icon="mdi:trending-up", native_unit_of_measurement="holdings", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=0, device_group="holdings"),
    SharesightSensorDescription(key="negative_holdings_count", sub_key="holdings", extension_key=None, name="Negative Holdings Count", icon="mdi:trending-down", native_unit_of_measurement="holdings", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=0, device_group="holdings"),
    SharesightSensorDescription(key="average_holding_value", sub_key="holdings", extension_key=None, name="Average Holding Value", icon="mdi:calculator", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="holdings"),
    SharesightSensorDescription(key="total_holdings_value", sub_key="holdings", extension_key=None, name="Total Holdings Value", icon="mdi:chart-areaspline", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="holdings"),
    SharesightSensorDescription(key="total_holdings_gain", sub_key="holdings", extension_key=None, name="Total Holdings Gain", icon="mdi:cash-plus", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="holdings"),
    SharesightSensorDescription(key="smallest_holding_symbol", sub_key="holdings", extension_key=None, name="Smallest Holding Symbol", icon="mdi:arrow-down-thin", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None, device_group="holdings"),
    SharesightSensorDescription(key="smallest_holding_value", sub_key="holdings", extension_key=None, name="Smallest Holding Value", icon="mdi:arrow-down-thin", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="holdings"),
    SharesightSensorDescription(key="median_holding_value", sub_key="holdings", extension_key=None, name="Median Holding Value", icon="mdi:chart-bell-curve-cumulative", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="holdings"),
    SharesightSensorDescription(key="top_5_holdings_percent", sub_key="holdings", extension_key=None, name="Top 5 Holdings Percent", icon="mdi:chart-donut", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="holdings"),
    SharesightSensorDescription(key="top_3_holdings_percent", sub_key="holdings", extension_key=None, name="Top 3 Holdings Percent", icon="mdi:chart-donut", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="holdings"),
    SharesightSensorDescription(key="positive_holdings_percent", sub_key="holdings", extension_key=None, name="Positive Holdings Percent", icon="mdi:trending-up", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="holdings"),
    SharesightSensorDescription(key="negative_holdings_percent", sub_key="holdings", extension_key=None, name="Negative Holdings Percent", icon="mdi:trending-down", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="holdings"),

    # ===== Income Report sensors =====
    SharesightSensorDescription(key="total_income", sub_key="income_report", extension_key=None, name="Total Dividend Income", icon="mdi:hand-coin", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL, entity_category=None, suggested_display_precision=2, device_group="income"),
    SharesightSensorDescription(key="dividend_count", sub_key="income_report", extension_key=None, name="Number of Dividends", icon="mdi:hand-coin", native_unit_of_measurement="dividends", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=0, device_group="income"),
    SharesightSensorDescription(key="last_dividend_date", sub_key="income_report", extension_key=None, name="Last Dividend Date", icon="mdi:calendar-check", native_unit_of_measurement=None, device_class=SensorDeviceClass.DATE, state_class=None, entity_category=None, suggested_display_precision=None, device_group="income"),
    SharesightSensorDescription(key="average_dividend_amount", sub_key="income_report", extension_key=None, name="Average Dividend Amount", icon="mdi:calculator-variant", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="income"),
    SharesightSensorDescription(key="largest_dividend_symbol", sub_key="income_report", extension_key=None, name="Largest Dividend Symbol", icon="mdi:star", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None, device_group="income"),
    SharesightSensorDescription(key="largest_dividend_amount", sub_key="income_report", extension_key=None, name="Largest Dividend Amount", icon="mdi:star", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="income"),

    # ===== Diversity sensors =====
    SharesightSensorDescription(key="market_1_name", sub_key="diversity", extension_key=None, name="Top Market 1 Name", icon="mdi:earth", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None, device_group="diversity"),
    SharesightSensorDescription(key="market_1_percent", sub_key="diversity", extension_key=None, name="Top Market 1 Percent", icon="mdi:earth", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="diversity"),
    SharesightSensorDescription(key="market_1_value", sub_key="diversity", extension_key=None, name="Top Market 1 Value", icon="mdi:earth", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="diversity"),
    SharesightSensorDescription(key="market_2_name", sub_key="diversity", extension_key=None, name="Top Market 2 Name", icon="mdi:earth", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None, device_group="diversity"),
    SharesightSensorDescription(key="market_2_percent", sub_key="diversity", extension_key=None, name="Top Market 2 Percent", icon="mdi:earth", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="diversity"),
    SharesightSensorDescription(key="market_2_value", sub_key="diversity", extension_key=None, name="Top Market 2 Value", icon="mdi:earth", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="diversity"),
    SharesightSensorDescription(key="market_3_name", sub_key="diversity", extension_key=None, name="Top Market 3 Name", icon="mdi:earth", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None, device_group="diversity"),
    SharesightSensorDescription(key="market_3_percent", sub_key="diversity", extension_key=None, name="Top Market 3 Percent", icon="mdi:earth", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="diversity"),
    SharesightSensorDescription(key="market_3_value", sub_key="diversity", extension_key=None, name="Top Market 3 Value", icon="mdi:earth", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="diversity"),
    SharesightSensorDescription(key="market_4_name", sub_key="diversity", extension_key=None, name="Top Market 4 Name", icon="mdi:earth", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None, device_group="diversity"),
    SharesightSensorDescription(key="market_4_percent", sub_key="diversity", extension_key=None, name="Top Market 4 Percent", icon="mdi:earth", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="diversity"),
    SharesightSensorDescription(key="market_4_value", sub_key="diversity", extension_key=None, name="Top Market 4 Value", icon="mdi:earth", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="diversity"),
    SharesightSensorDescription(key="market_5_name", sub_key="diversity", extension_key=None, name="Top Market 5 Name", icon="mdi:earth", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None, device_group="diversity"),
    SharesightSensorDescription(key="market_5_percent", sub_key="diversity", extension_key=None, name="Top Market 5 Percent", icon="mdi:earth", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="diversity"),
    SharesightSensorDescription(key="market_5_value", sub_key="diversity", extension_key=None, name="Top Market 5 Value", icon="mdi:earth", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="diversity"),
    SharesightSensorDescription(key="diversity_group_count", sub_key="diversity", extension_key=None, name="Diversity Group Count", icon="mdi:chart-pie", native_unit_of_measurement="groups", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=0, device_group="diversity"),
    SharesightSensorDescription(key="top_3_markets_percent", sub_key="diversity", extension_key=None, name="Top 3 Markets Percent", icon="mdi:chart-donut", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="diversity"),
    SharesightSensorDescription(key="top_5_markets_percent", sub_key="diversity", extension_key=None, name="Top 5 Markets Percent", icon="mdi:chart-donut", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="diversity"),

    # ===== Contribution sensors =====
    SharesightSensorDescription(key="total_contributions", sub_key="contributions", extension_key=None, name="Total Contributions", icon="mdi:cash-plus", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL, entity_category=None, suggested_display_precision=2, device_group="contributions"),
    SharesightSensorDescription(key="total_withdrawals", sub_key="contributions", extension_key=None, name="Total Withdrawals", icon="mdi:cash-minus", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL, entity_category=None, suggested_display_precision=2, device_group="contributions"),
    SharesightSensorDescription(key="net_contributions", sub_key="contributions", extension_key=None, name="Net Contributions", icon="mdi:cash-sync", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="contributions"),
    SharesightSensorDescription(key="last_contribution_date", sub_key="contributions", extension_key=None, name="Last Contribution Date", icon="mdi:calendar-clock", native_unit_of_measurement=None, device_class=SensorDeviceClass.DATE, state_class=None, entity_category=None, suggested_display_precision=None, device_group="contributions"),
    SharesightSensorDescription(key="last_contribution_amount", sub_key="contributions", extension_key=None, name="Last Contribution Amount", icon="mdi:cash-fast", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="contributions"),
    SharesightSensorDescription(key="net_investment_gain", sub_key="contributions", extension_key=None, name="Net Investment Gain", icon="mdi:chart-line", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="contributions"),
    SharesightSensorDescription(key="net_investment_gain_percent", sub_key="contributions", extension_key=None, name="Net Investment Gain Percent", icon="mdi:chart-line", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="contributions"),
    SharesightSensorDescription(key="contribution_count", sub_key="contributions", extension_key=None, name="Contribution Count", icon="mdi:counter", native_unit_of_measurement="contributions", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=0, device_group="contributions"),
    SharesightSensorDescription(key="withdrawal_count", sub_key="contributions", extension_key=None, name="Withdrawal Count", icon="mdi:counter", native_unit_of_measurement="withdrawals", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=0, device_group="contributions"),
    SharesightSensorDescription(key="average_contribution_amount", sub_key="contributions", extension_key=None, name="Average Contribution Amount", icon="mdi:calculator-variant", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="contributions"),

    # ===== Payout Tax Detail Aggregate sensors =====
    SharesightSensorDescription(key="total_gross_income", sub_key="income_report", extension_key=None, name="Total Gross Dividend Income", icon="mdi:cash-multiple", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL, entity_category=None, suggested_display_precision=2, device_group="income"),
    SharesightSensorDescription(key="total_resident_withholding_tax", sub_key="income_report", extension_key=None, name="Total Resident Withholding Tax", icon="mdi:bank-minus", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL, entity_category=None, suggested_display_precision=2, device_group="income"),
    SharesightSensorDescription(key="total_non_resident_withholding_tax", sub_key="income_report", extension_key=None, name="Total Non-Resident Withholding Tax", icon="mdi:bank-minus", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL, entity_category=None, suggested_display_precision=2, device_group="income"),
    SharesightSensorDescription(key="total_tax_credits", sub_key="income_report", extension_key=None, name="Total Tax Credits", icon="mdi:bank-plus", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL, entity_category=None, suggested_display_precision=2, device_group="income"),
    SharesightSensorDescription(key="total_franked_amount", sub_key="income_report", extension_key=None, name="Total Franked Amount", icon="mdi:cash-check", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL, entity_category=None, suggested_display_precision=2, device_group="income"),
    SharesightSensorDescription(key="total_unfranked_amount", sub_key="income_report", extension_key=None, name="Total Unfranked Amount", icon="mdi:cash-remove", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL, entity_category=None, suggested_display_precision=2, device_group="income"),
    SharesightSensorDescription(key="total_foreign_source_income", sub_key="income_report", extension_key=None, name="Total Foreign Source Income", icon="mdi:earth", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL, entity_category=None, suggested_display_precision=2, device_group="income"),
    SharesightSensorDescription(key="total_capital_gains_distributions", sub_key="income_report", extension_key=None, name="Total Capital Gains Distributions", icon="mdi:chart-line", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL, entity_category=None, suggested_display_precision=2, device_group="income"),
    SharesightSensorDescription(key="drp_reinvestment_count", sub_key="income_report", extension_key=None, name="DRP Reinvestment Count", icon="mdi:autorenew", native_unit_of_measurement="reinvestments", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=0, device_group="income"),
    SharesightSensorDescription(key="dividend_yield_percent", sub_key="income_report", extension_key=None, name="Dividend Yield Percent", icon="mdi:percent", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="income"),
    SharesightSensorDescription(key="dividends_30d", sub_key="income_report", extension_key=None, name="Dividends Last 30 Days", icon="mdi:hand-coin", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL, entity_category=None, suggested_display_precision=2, device_group="income"),
    SharesightSensorDescription(key="dividends_ytd", sub_key="income_report", extension_key=None, name="Dividends YTD", icon="mdi:calendar-today", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL, entity_category=None, suggested_display_precision=2, device_group="income"),
    SharesightSensorDescription(key="dividends_ttm", sub_key="income_report", extension_key=None, name="Dividends Last 12 Months", icon="mdi:hand-coin", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL, entity_category=None, suggested_display_precision=2, device_group="income"),
    SharesightSensorDescription(key="dividends_prev_year", sub_key="income_report", extension_key=None, name="Dividends Previous Year", icon="mdi:calendar-blank", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL, entity_category=None, suggested_display_precision=2, device_group="income"),
    SharesightSensorDescription(key="dividend_yield_ttm_percent", sub_key="income_report", extension_key=None, name="Dividend Yield TTM Percent", icon="mdi:percent", native_unit_of_measurement=PERCENTAGE, device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="income"),
    SharesightSensorDescription(key="upcoming_dividends_count", sub_key="income_report", extension_key=None, name="Upcoming Dividends Count", icon="mdi:calendar-arrow-right", native_unit_of_measurement="dividends", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=0, device_group="income"),
    SharesightSensorDescription(key="dividends_received_cash", sub_key="income_report", extension_key=None, name="Dividends Received (Cash)", icon="mdi:cash-fast", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL, entity_category=None, suggested_display_precision=2, device_group="income"),
    SharesightSensorDescription(key="next_dividend_date", sub_key="income_report", extension_key=None, name="Next Dividend Date", icon="mdi:calendar-arrow-right", native_unit_of_measurement=None, device_class=SensorDeviceClass.DATE, state_class=None, entity_category=None, suggested_display_precision=None, device_group="income"),
    SharesightSensorDescription(key="next_dividend_amount", sub_key="income_report", extension_key=None, name="Next Dividend Amount", icon="mdi:hand-coin", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="income"),
    SharesightSensorDescription(key="next_dividend_symbol", sub_key="income_report", extension_key=None, name="Next Dividend Symbol", icon="mdi:calendar-arrow-right", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None, device_group="income"),

    # ===== Portfolio Metadata sensors =====
    SharesightSensorDescription(key="inception_date", sub_key="portfolio_detail", extension_key=None, name="Portfolio Inception Date", icon="mdi:calendar-start", native_unit_of_measurement=None, device_class=SensorDeviceClass.DATE, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None),
    SharesightSensorDescription(key="country_code", sub_key="portfolio_detail", extension_key=None, name="Portfolio Country", icon="mdi:flag", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None),
    SharesightSensorDescription(key="interest_method", sub_key="portfolio_detail", extension_key=None, name="Performance Calculation Method", icon="mdi:calculator-variant-outline", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None),
    SharesightSensorDescription(key="access_level", sub_key="portfolio_detail", extension_key=None, name="Portfolio Access Level", icon="mdi:shield-account", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None),
    SharesightSensorDescription(key="owner_name", sub_key="portfolio_detail", extension_key=None, name="Portfolio Owner", icon="mdi:account", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None),

    # ===== Trades sensors =====
    SharesightSensorDescription(key="last_trade_date", sub_key="trades", extension_key=None, name="Last Trade Date", icon="mdi:calendar-clock", native_unit_of_measurement=None, device_class=SensorDeviceClass.DATE, state_class=None, entity_category=None, suggested_display_precision=None, device_group="trades"),
    SharesightSensorDescription(key="last_trade_symbol", sub_key="trades", extension_key=None, name="Last Trade Symbol", icon="mdi:swap-horizontal", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=None, suggested_display_precision=None, device_group="trades"),
    SharesightSensorDescription(key="last_trade_type", sub_key="trades", extension_key=None, name="Last Trade Type", icon="mdi:swap-horizontal-bold", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=None, suggested_display_precision=None, device_group="trades"),
    SharesightSensorDescription(key="last_trade_value", sub_key="trades", extension_key=None, name="Last Trade Value", icon="mdi:cash-fast", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="trades"),
    SharesightSensorDescription(key="trade_count_30d", sub_key="trades", extension_key=None, name="Trades Last 30 Days", icon="mdi:counter", native_unit_of_measurement="trades", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=0, device_group="trades"),
    SharesightSensorDescription(key="total_trades", sub_key="trades", extension_key=None, name="Total Trades", icon="mdi:counter", native_unit_of_measurement="trades", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=0, device_group="trades"),
    SharesightSensorDescription(key="buy_count", sub_key="trades", extension_key=None, name="Total Buy Trades", icon="mdi:cart-arrow-down", native_unit_of_measurement="trades", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=0, device_group="trades"),
    SharesightSensorDescription(key="sell_count", sub_key="trades", extension_key=None, name="Total Sell Trades", icon="mdi:cart-arrow-up", native_unit_of_measurement="trades", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=0, device_group="trades"),
    SharesightSensorDescription(key="total_buy_value", sub_key="trades", extension_key=None, name="Total Buy Value", icon="mdi:cart-arrow-down", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL, entity_category=None, suggested_display_precision=2, device_group="trades"),
    SharesightSensorDescription(key="total_sell_value", sub_key="trades", extension_key=None, name="Total Sell Value", icon="mdi:cart-arrow-up", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL, entity_category=None, suggested_display_precision=2, device_group="trades"),
    SharesightSensorDescription(key="net_trade_flow", sub_key="trades", extension_key=None, name="Net Trade Flow", icon="mdi:swap-vertical", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="trades"),
    SharesightSensorDescription(key="largest_trade_value", sub_key="trades", extension_key=None, name="Largest Trade Value", icon="mdi:trophy", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="trades"),
    SharesightSensorDescription(key="largest_trade_symbol", sub_key="trades", extension_key=None, name="Largest Trade Symbol", icon="mdi:trophy", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None, device_group="trades"),
    SharesightSensorDescription(key="trade_count_7d", sub_key="trades", extension_key=None, name="Trades Last 7 Days", icon="mdi:counter", native_unit_of_measurement="trades", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=0, device_group="trades"),
    SharesightSensorDescription(key="trade_count_ytd", sub_key="trades", extension_key=None, name="Trades YTD", icon="mdi:counter", native_unit_of_measurement="trades", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=0, device_group="trades"),
    SharesightSensorDescription(key="average_trade_value", sub_key="trades", extension_key=None, name="Average Trade Value", icon="mdi:calculator", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="trades"),
    SharesightSensorDescription(key="average_buy_value", sub_key="trades", extension_key=None, name="Average Buy Value", icon="mdi:calculator", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="trades"),
    SharesightSensorDescription(key="average_sell_value", sub_key="trades", extension_key=None, name="Average Sell Value", icon="mdi:calculator", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="trades"),
    SharesightSensorDescription(key="total_brokerage", sub_key="trades", extension_key=None, name="Total Brokerage", icon="mdi:cash-minus", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL, entity_category=None, suggested_display_precision=2, device_group="trades"),
    SharesightSensorDescription(key="trades_per_month", sub_key="trades", extension_key=None, name="Average Trades Per Month", icon="mdi:chart-timeline-variant", native_unit_of_measurement="trades/month", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="trades"),
    SharesightSensorDescription(key="last_buy_date", sub_key="trades", extension_key=None, name="Last Buy Date", icon="mdi:cart-arrow-down", native_unit_of_measurement=None, device_class=SensorDeviceClass.DATE, state_class=None, entity_category=None, suggested_display_precision=None, device_group="trades"),
    SharesightSensorDescription(key="last_buy_symbol", sub_key="trades", extension_key=None, name="Last Buy Symbol", icon="mdi:cart-arrow-down", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=None, suggested_display_precision=None, device_group="trades"),
    SharesightSensorDescription(key="last_buy_value", sub_key="trades", extension_key=None, name="Last Buy Value", icon="mdi:cart-arrow-down", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="trades"),
    SharesightSensorDescription(key="last_sell_date", sub_key="trades", extension_key=None, name="Last Sell Date", icon="mdi:cart-arrow-up", native_unit_of_measurement=None, device_class=SensorDeviceClass.DATE, state_class=None, entity_category=None, suggested_display_precision=None, device_group="trades"),
    SharesightSensorDescription(key="last_sell_symbol", sub_key="trades", extension_key=None, name="Last Sell Symbol", icon="mdi:cart-arrow-up", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=None, suggested_display_precision=None, device_group="trades"),
    SharesightSensorDescription(key="last_sell_value", sub_key="trades", extension_key=None, name="Last Sell Value", icon="mdi:cart-arrow-up", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="trades"),
    SharesightSensorDescription(key="most_traded_symbol", sub_key="trades", extension_key=None, name="Most Traded Symbol", icon="mdi:fire", native_unit_of_measurement=None, device_class=None, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None, device_group="trades"),

    # ===== Cash transaction analytics =====
    SharesightSensorDescription(key="cash_transaction_count", sub_key="cash_account_transactions", extension_key=None, name="Cash Transactions Count", icon="mdi:bank-transfer", native_unit_of_measurement="transactions", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=0, device_group="contributions"),
    SharesightSensorDescription(key="last_cash_transaction_date", sub_key="cash_account_transactions", extension_key=None, name="Last Cash Transaction Date", icon="mdi:calendar-clock", native_unit_of_measurement=None, device_class=SensorDeviceClass.DATE, state_class=None, entity_category=None, suggested_display_precision=None, device_group="contributions"),
    SharesightSensorDescription(key="last_cash_transaction_amount", sub_key="cash_account_transactions", extension_key=None, name="Last Cash Transaction Amount", icon="mdi:bank-transfer", native_unit_of_measurement=CURRENCY_DOLLAR, device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.MEASUREMENT, entity_category=None, suggested_display_precision=2, device_group="contributions"),

    # ===== Portfolio derived =====
    SharesightSensorDescription(key="portfolio_age_days", sub_key="portfolio_detail", extension_key=None, name="Portfolio Age (days)", icon="mdi:timer-sand", native_unit_of_measurement="days", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=0),

    # ===== Integration diagnostics =====
    SharesightSensorDescription(key="last_update_timestamp", sub_key="_integration", extension_key=None, name="Last Successful Update", icon="mdi:update", native_unit_of_measurement=None, device_class=SensorDeviceClass.TIMESTAMP, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=None),
    SharesightSensorDescription(key="update_interval_seconds", sub_key="_integration", extension_key=None, name="Update Interval (s)", icon="mdi:timer-cog", native_unit_of_measurement="s", device_class=SensorDeviceClass.DURATION, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=0),
    SharesightSensorDescription(key="optional_endpoints_on_cooldown", sub_key="_integration", extension_key=None, name="Endpoints on Cooldown", icon="mdi:pause-circle-outline", native_unit_of_measurement="endpoints", device_class=None, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=0),
]
