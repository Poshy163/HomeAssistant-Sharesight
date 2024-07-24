from dataclasses import dataclass
from typing import Callable, List, Union
from homeassistant.components.sensor import SensorEntityDescription, SensorDeviceClass
from homeassistant.const import CURRENCY_DOLLAR, PERCENTAGE


@dataclass
class SharesightSensorDescription(SensorEntityDescription):
    native_value: Union[Callable[[Union[str, int, float]], Union[str, int, float]], None] = None


SENSOR_DESCRIPTIONS: List[SharesightSensorDescription] = [
    SharesightSensorDescription(
        key="portfolio_id",
        name="Portfolio ID",
        native_unit_of_measurement=None ,
        device_class=None,
        state_class=None
    ),
    SharesightSensorDescription(
        key="value",
        name="Portfolio Value",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=None
    ),
    SharesightSensorDescription(
        key="total_gain",
        name="Total Gain",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=None
    ),
    SharesightSensorDescription(
        key="total_gain_percent",
        name="Total Gain Percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.MONETARY,
        state_class=None
    ),
    SharesightSensorDescription(
        key="currency_gain",
        name="Currency Gain",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=None
    ),
    SharesightSensorDescription(
        key="payout_gain",
        name="Payout Gain",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=None
    )
]
