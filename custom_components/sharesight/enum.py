from dataclasses import dataclass
from typing import Callable, List, Union
from homeassistant.components.sensor import SensorEntityDescription, SensorDeviceClass, SensorStateClass
from homeassistant.const import CURRENCY_DOLLAR, PERCENTAGE, EntityCategory


@dataclass
class SharesightSensorDescription(SensorEntityDescription):
    sub_key: str = None
    native_value: Union[Callable[[Union[str, int, float]], Union[str, int, float]], None] = None


CASH_SENSOR_DESCRIPTIONS: List[SharesightSensorDescription] = [
    SharesightSensorDescription(
        key='cash_accounts',
        sub_key="value",
        name="CASH balance",
        icon="mdi:cash",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        entity_category=None,
        suggested_display_precision=2
    )
]

MARKET_SENSOR_DESCRIPTIONS: List[SharesightSensorDescription] = [
    SharesightSensorDescription(
        key='sub_totals',
        sub_key="value",
        name="MARKET value",
        icon="mdi:finance",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        entity_category=None,
        suggested_display_precision=2
    )
]

SENSOR_DESCRIPTIONS: List[SharesightSensorDescription] = [
    SharesightSensorDescription(
        key="value",
        sub_key="report",
        name="Portfolio value",
        icon="mdi:cash",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        entity_category=None,
        suggested_display_precision=2
    ),
    SharesightSensorDescription(
        key="capital_gain",
        sub_key="report",
        name="Capital gain",
        icon="mdi:cash-plus",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        entity_category=None,
        suggested_display_precision=2
    ),
    SharesightSensorDescription(
        key="capital_gain_percent",
        sub_key="report",
        name="Capital gain percent",
        icon="mdi:sack-percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        entity_category=None,
        suggested_display_precision=2
    ),
    SharesightSensorDescription(
        key="total_gain",
        sub_key="report",
        name="Total gain",
        icon="mdi:cash-plus",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        entity_category=None,
        suggested_display_precision=2
    ),
    SharesightSensorDescription(
        key="total_gain_percent",
        sub_key="report",
        name="Total gain percent",
        icon="mdi:sack-percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        entity_category=None,
        suggested_display_precision=2
    ),
    SharesightSensorDescription(
        key="currency_gain",
        sub_key="report",
        name="Currency gain",
        icon="mdi:cash-plus",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        entity_category=None,
        suggested_display_precision=2
    ),
    SharesightSensorDescription(
        key="currency_gain_percent",
        sub_key="report",
        name="Currency gain percent",
        icon="mdi:sack-percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        entity_category=None,
        suggested_display_precision=2
    ),
    SharesightSensorDescription(
        key="payout_gain",
        sub_key="report",
        name="Dividend gain",
        icon="mdi:cash-plus",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        entity_category=None,
        suggested_display_precision=2
    ),
    SharesightSensorDescription(
        key="payout_gain_percent",
        sub_key="report",
        name="Dividend gain percent",
        icon="mdi:sack-percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        entity_category=None,
        suggested_display_precision=2

    ),
    SharesightSensorDescription(
        key="portfolio_id",
        sub_key="report",
        name="Portfolio ID",
        icon="mdi:identifier",
        native_unit_of_measurement=None,
        device_class=None,
        state_class=SensorStateClass.TOTAL,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2
    ),
    SharesightSensorDescription(
        key="user_id",
        sub_key="portfolios",
        name="User ID",
        icon="mdi:identifier",
        native_unit_of_measurement=None,
        device_class=None,
        state_class=SensorStateClass.TOTAL,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2
    )
]
