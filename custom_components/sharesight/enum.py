from dataclasses import dataclass
from typing import Callable, List, Union
from homeassistant.components.sensor import SensorEntityDescription, SensorDeviceClass
from homeassistant.const import CURRENCY_DOLLAR, PERCENTAGE


@dataclass
class SharesightSensorDescription(SensorEntityDescription):
    native_value: Union[Callable[[Union[str, int, float]], Union[str, int, float]], None] = None


MARKET_SENSOR_DESCRIPTIONS: List[SharesightSensorDescription] = [
    SharesightSensorDescription(
        key="sub_totals",
        name="MARKET value",
        icon="mdi:cash",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=None
    )
]

SENSOR_DESCRIPTIONS: List[SharesightSensorDescription] = [
    SharesightSensorDescription(
        key="value",
        name="Portfolio value",
        icon="mdi:cash",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=None
    ),
    SharesightSensorDescription(
        key="capital_gain",
        name="Capital gain",
        icon="mdi:cash-plus",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=None
    ),
    SharesightSensorDescription(
        key="capital_gain_percent",
        name="Capital gain percent",
        icon="mdi:sack-percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.MONETARY,
        state_class=None
    ),
    SharesightSensorDescription(
        key="total_gain",
        name="Total gain",
        icon="mdi:cash-plus",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=None
    ),
    SharesightSensorDescription(
        key="total_gain_percent",
        name="Total gain percent",
        icon="mdi:sack-percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.MONETARY,
        state_class=None
    ),
    SharesightSensorDescription(
        key="currency_gain",
        name="Currency gain",
        icon="mdi:cash-plus",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=None
    ),
    SharesightSensorDescription(
        key="currency_gain_percent",
        name="Currency gain percent",
        icon="mdi:sack-percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.MONETARY,
        state_class=None
    ),
    SharesightSensorDescription(
        key="payout_gain",
        name="Dividend gain",
        icon="mdi:cash",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=None
    ),
    SharesightSensorDescription(
        key="payout_gain_percent",
        name="Dividend gain percent",
        icon="mdi:sack-percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.MONETARY,
        state_class=None
    )
]
